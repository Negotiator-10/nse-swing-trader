"""seed_live_tape.py - ONE-TIME seed of the point-in-time live tape (DECISIONS #002).

The freeze-first-seen mechanism needs a starting frozen tape for the live era that
already happened (2026-07-17..). We reconstruct it as honestly as the surviving data
allows:

  * CLOSE  = the risk panel's `ref_px`. Because the panel deduped keep="first", this IS
             the first-seen close for each live-era date -- exactly the value that drove
             the decision made that evening (e.g. SBIN 2026-07-20 = 1060.60, before Kite
             later restated it to 1060.00).
  * O/H/L/V = current Kite (the original intraday O/H/L was never persisted -- the very
             bug we are fixing -- so this is the best available). High/low are clamped to
             contain the first-seen close so the bar is internally consistent.

Every place where current Kite's close disagrees with the first-seen close is written to
the revisions log as a SEED-detected restatement, so the record is honest about what was
reconstructed vs original.

HONEST LIMITATION (stated, not buried): for the seed period the O/H/L are today's
possibly-restated values, not the true first-seen intraday. Only the CLOSE (which drives
signals, stops via close-3.5*ATR, and mark-to-market) is genuine first-seen. Going
forward every bar is frozen at true first-seen. Run ONCE:

    C:/Users/Admin/anaconda3/python.exe seed_live_tape.py    (env with Kite key/token loaded)

ASCII-only prints.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

import config as C
import p10_swing_feed as P10

TAPE = C.PAPER_LIVE_TAPE_PATH


def main() -> int:
    if TAPE.exists():
        print(f"  live tape already exists ({TAPE.name}); refusing to overwrite. "
              f"Delete it first to re-seed.")
        return 1

    research_last = pd.to_datetime(
        pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")["date"]).max()
    print(f"  research tape ends {research_last.date()}; live era = every date after it.")

    risk = pd.read_parquet(C.DATA_DIR / "swing_risk_panel.parquet")
    risk["date"] = pd.to_datetime(risk["date"])
    live = risk[risk["date"] > research_last][["symbol", "date", "ref_px"]].copy()
    if not len(live):
        print("  no live-era rows in the risk panel; nothing to seed.")
        return 1
    watch = sorted(live["symbol"].unique())
    print(f"  {len(live)} live-era (symbol,date) closes across {len(watch)} symbols "
          f"[{live['date'].min().date()} .. {live['date'].max().date()}]")

    # current Kite OHLCV for the O/H/L/V columns
    src = P10.LiveKiteSource(watch)
    kite = src.fetch_daily()
    for s in watch:
        kite[s] = kite[s].copy()
        kite[s]["date"] = pd.to_datetime(kite[s]["date"])

    now_utc = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows, revisions, degenerate = [], [], 0
    for t in live.itertuples():
        s, d, close = t.symbol, pd.Timestamp(t.date), float(t.ref_px)
        kh = kite.get(s)
        kr = kh[kh["date"] == d] if kh is not None else None
        if kr is not None and len(kr):
            o, h, l = float(kr["open"].iloc[0]), float(kr["high"].iloc[0]), float(kr["low"].iloc[0])
            v = int(kr["volume"].iloc[0])
            kc = float(kr["close"].iloc[0])
            if abs(kc - close) > 1e-6:
                revisions.append({"symbol": s, "date": d, "field": "close",
                                  "frozen": close, "kite": kc,
                                  "diff_bp": round((kc / close - 1) * 1e4, 2),
                                  "detected_utc": now_utc + " (SEED)"})
            h = max(h, o, close)          # keep the bar internally consistent with the
            l = min(l, o, close)          # first-seen close
        else:
            o = h = l = close             # Kite lacks this date -> degenerate bar, close only
            v = 0
            degenerate += 1
        rows.append({"open": o, "high": h, "low": l, "close": close, "volume": v,
                     "date": d, "symbol": s, "split": "test", "first_seen_utc": now_utc})

    tape = pd.DataFrame(rows).sort_values(["symbol", "date"]).reset_index(drop=True)
    tape.to_parquet(TAPE, index=False)
    print(f"  wrote {TAPE.name}: {len(tape)} frozen bars"
          + (f" ({degenerate} close-only where Kite lacked the date)" if degenerate else ""))

    if revisions:
        rv = pd.DataFrame(revisions)
        rv["date"] = pd.to_datetime(rv["date"])
        old = (pd.read_parquet(C.PAPER_REVISIONS_PATH)
               if C.PAPER_REVISIONS_PATH.exists() else pd.DataFrame())
        pd.concat([old, rv], ignore_index=True).to_parquet(C.PAPER_REVISIONS_PATH, index=False)
        print(f"  logged {len(revisions)} seed-detected restatement(s) "
              f"(Kite now != first-seen close):")
        for r in revisions:
            print(f"    {r['symbol']:<11} {str(r['date'])[:10]} first-seen {r['frozen']:.4f}"
                  f" -> Kite {r['kite']:.4f} ({r['diff_bp']:+.1f}bp)")
    else:
        print("  no seed-detected restatements (current Kite == every first-seen close).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
