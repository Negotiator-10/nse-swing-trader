"""
Phase 0b - Universe screener: pick a TRADABLE watchlist from the NIFTY 50.

THE BIAS TRAP (why selection must be careful)
---------------------------------------------
Picking the stocks that PERFORMED BEST in the past and then backtesting on them is
survivorship / data-snooping bias -- the backtest looks good for a fake reason. So we
select the universe on EX-ANTE TRADABILITY ONLY:
  * enough history (>= SWING_MIN_DAYS) for a fair walk-forward,
  * liquidity  = median daily traded VALUE (close*volume), so fills are realistic,
  * data quality = few zero-volume bars and few OHLC adjustment artifacts.
We rank by LIQUIDITY (not returns) and take the top N. The strategy's SIGNAL, not the
screener, decides which names to actually hold. This is a legitimate, forward-fair
selection; ranking by past return would not be.

Candidate universe = NIFTY 100 (2026-07-29 expansion, DECISIONS #012; was NIFTY 50).
Constituents are fetched from the OFFICIAL niftyindices.com CSV so membership is
point-in-time-current, not hand-maintained. Selection policy (user choice): ALL names
that pass the ex-ante screen -- no top-N cut. Symbols yfinance cannot serve, or with
too little history (recent listings / demergers), are dropped cleanly.

ASCII-only prints. Run:  C:/Users/Admin/anaconda3/python.exe p0_universe.py
Writes: data/watchlist.json, artifacts/universe_screen.json
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

import config as C
from p1_swing_data import fetch_daily

warnings.filterwarnings("ignore")

CANDIDATE_INDEX = "NIFTY 100"
NIFTY100_URLS = [    # official constituent CSV (same source/pattern gfs_backtest used)
    "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "https://niftyindices.com/IndexConstituent/ind_nifty100list.csv",
]
_FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "text/csv,application/csv,*/*",
    "Referer": "https://www.niftyindices.com/",
}


def fetch_constituents() -> list[str]:
    """NIFTY 100 symbols from the official CSV; cached to data/ for offline re-runs."""
    import io
    import urllib.request
    cache = C.DATA_DIR / "nifty100_symbols.json"
    last = None
    for url in NIFTY100_URLS:
        try:
            req = urllib.request.Request(url, headers=_FETCH_HEADERS)
            raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
            df = pd.read_csv(io.StringIO(raw))
            col = next(c for c in df.columns if c.strip().lower() == "symbol")
            syms = sorted(df[col].astype(str).str.strip().unique().tolist())
            if len(syms) >= 90:                      # sanity: the index has ~100 names
                cache.write_text(json.dumps({"source": url, "n": len(syms),
                                             "symbols": syms}, indent=2))
                return syms
            last = f"parsed only {len(syms)} symbols from {url}"
        except Exception as e:
            last = f"{type(e).__name__} on {url}"
    if cache.exists():                                # offline fallback: last good fetch
        doc = json.loads(cache.read_text())
        print(f"  [warn] live fetch failed ({last}); using cached list "
              f"({doc['n']} symbols, {doc['source']})")
        return doc["symbols"]
    raise RuntimeError(f"could not fetch NIFTY 100 constituents: {last}")


SELECT_ALL_PASSING = True           # DECISIONS #012 (user choice): every name that passes
MIN_TURNOVER_INR = 20e7            # >= Rs.20 crore median daily turnover to be tradable
LIQ_WINDOW = 252                    # recent ~1y window for the liquidity estimate


def screen_symbol(sym: str) -> dict | None:
    """Fetch + compute ex-ante tradability metrics for one symbol (None if unavailable)."""
    try:
        d = fetch_daily(sym)
    except Exception as e:
        return {"symbol": sym, "ok": False, "reason": f"fetch_fail:{type(e).__name__}"}
    n = len(d)
    if n < C.SWING_MIN_DAYS:
        return {"symbol": sym, "ok": False, "reason": f"short_history:{n}", "n_days": n}
    recent = d.tail(LIQ_WINDOW)
    turnover = (recent["close"] * recent["volume"]).to_numpy()
    med_turn = float(np.median(turnover))
    zero_vol_frac = float((d["volume"] == 0).mean())
    repaired = int(d.attrs.get("n_ohlc_repaired", 0))
    return {"symbol": sym, "ok": True, "n_days": int(n),
            "start": str(d["date"].min().date()), "end": str(d["date"].max().date()),
            "med_daily_turnover_inr": med_turn,
            "med_daily_turnover_cr": med_turn / 1e7,
            "zero_vol_frac": zero_vol_frac, "n_ohlc_repaired": repaired,
            "liquid_enough": med_turn >= MIN_TURNOVER_INR}


def main() -> int:
    print("=" * 66)
    print("  UNIVERSE SCREEN - NIFTY 100 -> tradable swing watchlist (ex-ante only)")
    print("=" * 66)
    candidates = fetch_constituents()
    print(f"  candidates: {len(candidates)} ({CANDIDATE_INDEX}, official CSV)")
    rows = []
    for i, sym in enumerate(candidates, 1):
        r = screen_symbol(sym)
        rows.append(r)
        tag = ("OK  turn=%6.1f cr  hist=%d" % (r["med_daily_turnover_cr"], r["n_days"])
               if r.get("ok") else "DROP %s" % r.get("reason"))
        print(f"  [{i:3d}/{len(candidates)}] {sym:12s} {tag}")

    ok_rows = [r for r in rows if r.get("ok") and r.get("liquid_enough")]
    # RANK BY LIQUIDITY (not returns) for display; selection = ALL that pass (#012)
    ok_rows.sort(key=lambda r: r["med_daily_turnover_inr"], reverse=True)
    selected = ok_rows
    sel_syms = [r["symbol"] for r in selected]

    print("\n  --- SELECTED (all %d passing the ex-ante screen) ---" % len(sel_syms))
    for r in selected:
        print(f"    {r['symbol']:12s} turnover={r['med_daily_turnover_cr']:7.1f} cr/day  "
              f"hist={r['n_days']}d  zero_vol={r['zero_vol_frac']*100:.2f}%")
    dropped = [r["symbol"] for r in rows if not (r.get("ok") and r.get("liquid_enough"))]
    print(f"\n  dropped ({len(dropped)}): {', '.join(dropped)}")

    # ---- handoffs ----
    (C.DATA_DIR / "watchlist.json").write_text(json.dumps({
        "selected": sel_syms, "n": len(sel_syms),
        "criteria": "ex-ante tradability: history>=%d, median daily turnover>=Rs.%.0f cr, "
                    "ALL passing selected (NOT ranked by past returns)" % (C.SWING_MIN_DAYS, MIN_TURNOVER_INR / 1e7),
        "candidate_universe": CANDIDATE_INDEX, "generated": str(pd.Timestamp.today().date()),
    }, indent=2))
    (C.ARTIFACTS_DIR / "universe_screen.json").write_text(json.dumps({
        "candidate_universe": CANDIDATE_INDEX, "n_candidates": len(candidates),
        "selection_policy": "all_passing (DECISIONS #012)", "min_turnover_inr": MIN_TURNOVER_INR,
        "selected": sel_syms, "all_metrics": rows,
        "note": "selection is ex-ante tradability only; ranking by return would be data snooping",
    }, indent=2, default=str))
    print(f"\n  wrote {C.DATA_DIR / 'watchlist.json'}  ({len(sel_syms)} symbols)")
    print(f"  wrote {C.ARTIFACTS_DIR / 'universe_screen.json'}")
    print("\nUNIVERSE SCREEN COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
