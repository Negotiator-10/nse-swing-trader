"""
Phase 4 (part 1/2) - Daily-bar feed layer: run-mode resolution + feed discipline
+ a REPLAY source and a LIVE (Kite) source behind one interface.

THE EOD ANALOG OF A TICK FEED-HANDLER
-------------------------------------
integrated_mm_v2's phase7 FeedHandler enforced tick discipline (sequence ids,
staleness, reconnects). A swing system has the same failure modes at daily scale:
a vendor re-sends yesterday's bar (duplicate), returns bars out of date order,
or silently stops updating (a broken token looks exactly like a holiday). The
DailyFeedHandler makes every one of those LOUD instead of silently corrupting
the book:

  * accept  : a bar strictly NEWER than the last accepted bar for that symbol
  * reject  : same-date re-send (duplicate) or an older date (out-of-order)
  * holiday : no new bar today is NOT an error -> "no decision" (count it)
  * dead-man: newest accepted bar older than FEED_STALE_MAX_DAYS calendar days
              -> the feed is broken, not a long weekend -> HALT trading

RUN MODES (one code path, stamped in every artifact)
----------------------------------------------------
  REPLAY - iterate data/swing_daily.parquet one date at a time (offline, no keys).
  LIVE   - after 15:30 IST on a trading day: pull recent daily candles per symbol
           from Kite Connect (paid data plan). Secrets are read from the ENVIRONMENT
           by NAME only; this module checks PRESENCE as a boolean and never
           reads/prints a value. NO real orders exist anywhere in this system.

ASCII-only prints. Run (self-test):  C:/Users/Admin/anaconda3/python.exe p10_swing_feed.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config as C


# ================================================================ run-mode resolution
def resolve_run_mode() -> tuple[str, dict]:
    """Pick REPLAY or LIVE without ever reading secret VALUES (presence-only booleans).

    LIVE needs BOTH the api key and the daily access token in the environment AND an
    explicit RUN_MODE=LIVE opt-in (so a stray key never silently arms the live path)."""
    has_key = bool(os.environ.get(C.KITE_API_KEY_ENV))
    has_tok = bool(os.environ.get(C.KITE_ACCESS_TOKEN_ENV))
    want = os.environ.get("RUN_MODE", C.RUN_MODE_DEFAULT).upper()
    info = {"keys_present": has_key, "token_present": has_tok, "requested": want}
    if want == "LIVE" and has_key and has_tok:
        info["note"] = "LIVE armed: Kite key+token present in env (values never read here)."
        return "LIVE", info
    if want == "LIVE":
        info["note"] = ("LIVE requested but key/token missing from env -> falling back to "
                        "REPLAY. Run kite_login.py, then load secrets.env into the shell.")
    else:
        info["note"] = "REPLAY (default). Set RUN_MODE=LIVE + env secrets to arm the live pull."
    return "REPLAY", info


# ================================================================ bars + discipline
@dataclass
class DailyBar:
    symbol: str
    date: object          # datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class FeedStats:
    n_accepted: int = 0
    n_duplicates: int = 0          # same-date re-send -> rejected
    n_out_of_order: int = 0        # older-than-last date -> rejected
    n_no_bar_days: int = 0         # symbol had no bar on a decision day (holiday/suspension)
    n_dead_man_checks: int = 0
    dead_man_tripped: bool = False
    last_date: dict = field(default_factory=dict)   # symbol -> newest accepted date


class DailyFeedHandler:
    """Feed discipline for daily bars, per symbol. A violation is counted and the bar
    REJECTED -- corrupt input can never advance the book."""

    def __init__(self):
        self.s = FeedStats()

    def push(self, bar: DailyBar) -> bool:
        last = self.s.last_date.get(bar.symbol)
        if last is not None and bar.date == last:
            self.s.n_duplicates += 1
            return False
        if last is not None and bar.date < last:
            self.s.n_out_of_order += 1
            return False
        ok = (np.isfinite([bar.open, bar.high, bar.low, bar.close]).all()
              and bar.high >= bar.low > 0)
        if not ok:                                   # malformed OHLC -> treat as no bar
            self.s.n_no_bar_days += 1
            return False
        self.s.last_date[bar.symbol] = bar.date
        self.s.n_accepted += 1
        return True

    def note_no_bar(self, symbol: str) -> None:
        """A decision day on which this symbol produced no bar (holiday/suspension)."""
        self.s.n_no_bar_days += 1

    def is_dead(self, today: dt.date) -> bool:
        """Dead-man switch: True when the NEWEST bar across all symbols is older than
        FEED_STALE_MAX_DAYS calendar days -- a broken feed, not a long weekend."""
        self.s.n_dead_man_checks += 1
        if not self.s.last_date:
            return False
        newest = max(self.s.last_date.values())
        dead = (today - newest).days > C.FEED_STALE_MAX_DAYS
        if dead:
            self.s.dead_man_tripped = True
        return dead


# ================================================================ REPLAY source
def replay_day_stream(panel: pd.DataFrame):
    """Yield (date, {symbol: DailyBar}) for every calendar date in the historical tape,
    in order -- the offline stand-in for the daily Kite pull."""
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    for date, g in panel.sort_values("date").groupby("date", sort=True):
        bars = {r.symbol: DailyBar(symbol=r.symbol, date=date.date(),
                                   open=float(r.open), high=float(r.high),
                                   low=float(r.low), close=float(r.close),
                                   volume=float(r.volume))
                for r in g.itertuples()}
        yield date, bars


# ================================================================ LIVE (Kite) source
class LiveKiteSource:
    """Pull recent DAILY candles per watchlist symbol from Kite Connect.

    Reads credentials from the environment at call time (never stores/prints them).
    Data-only: this class contains no order-placement code whatsoever."""

    def __init__(self, symbols: list[str]):
        from kiteconnect import KiteConnect     # imported lazily: REPLAY needs no SDK
        api_key = os.environ.get(C.KITE_API_KEY_ENV)
        token = os.environ.get(C.KITE_ACCESS_TOKEN_ENV)
        if not api_key or not token:
            raise RuntimeError("Kite key/token missing from environment (run kite_login.py)")
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(token)
        self.symbols = symbols
        self._tokens = self._resolve_tokens(symbols)

    def _resolve_tokens(self, symbols) -> dict:
        """tradingsymbol -> instrument_token from the NSE instruments dump."""
        instruments = self.kite.instruments(C.KITE_EXCHANGE_EQ)
        by_sym = {i["tradingsymbol"]: i["instrument_token"] for i in instruments
                  if i.get("instrument_type") == "EQ"}
        tokens, missing = {}, []
        for s in symbols:
            if s in by_sym:
                tokens[s] = by_sym[s]
            else:
                missing.append(s)
        if missing:
            raise RuntimeError(f"instrument tokens not found on NSE for: {missing}")
        return tokens

    def fetch_daily(self, lookback_days: int = None) -> dict:
        """{symbol: DataFrame(date,open,high,low,close,volume)} of recent daily candles."""
        lookback = lookback_days or C.KITE_HISTORICAL_LOOKBACK_DAYS
        to_d = dt.date.today()
        from_d = to_d - dt.timedelta(days=int(lookback * 1.6))  # calendar cushion
        out = {}
        for s in self.symbols:
            candles = self.kite.historical_data(self._tokens[s], from_d, to_d,
                                                C.KITE_INTERVAL)
            df = pd.DataFrame(candles)
            if len(df) == 0:
                out[s] = df
                continue
            df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
            out[s] = df[["date", "open", "high", "low", "close", "volume"]]
        return out

    def latest_bars(self) -> dict:
        """{symbol: DailyBar} for each symbol's newest daily candle."""
        raw = self.fetch_daily()
        bars = {}
        for s, df in raw.items():
            if len(df) == 0:
                continue
            r = df.iloc[-1]
            bars[s] = DailyBar(symbol=s, date=r["date"].date(), open=float(r["open"]),
                               high=float(r["high"]), low=float(r["low"]),
                               close=float(r["close"]), volume=float(r["volume"]))
        return bars


# ================================================================ fault injection (for G4.2)
def fault_injection_report(panel: pd.DataFrame) -> dict:
    """Prove each detector FIRES: duplicate, out-of-order, malformed bar, holiday
    no-bar, dead-man on a stale gap. Returns the per-fault booleans for the gate."""
    fh = DailyFeedHandler()
    sym = str(panel["symbol"].iloc[0])
    g = panel[panel["symbol"] == sym].sort_values("date").head(10).reset_index(drop=True)

    def mk(i, **over):
        r = g.iloc[i]
        d = dict(symbol=sym, date=pd.to_datetime(r["date"]).date(), open=float(r["open"]),
                 high=float(r["high"]), low=float(r["low"]), close=float(r["close"]),
                 volume=float(r["volume"]))
        d.update(over)
        return DailyBar(**d)

    ok_fresh = fh.push(mk(0)) and fh.push(mk(1))              # clean advance
    dup_rejected = not fh.push(mk(1))                          # same date re-sent
    ooo_rejected = not fh.push(mk(0))                          # older date
    bad_rejected = not fh.push(mk(2, low=-5.0))                # malformed OHLC
    n_before = fh.s.n_no_bar_days
    fh.note_no_bar(sym)                                        # holiday = counted, no error
    holiday_counted = fh.s.n_no_bar_days == n_before + 1
    last_d = fh.s.last_date[sym]
    dead_ok = fh.is_dead(last_d + dt.timedelta(days=C.FEED_STALE_MAX_DAYS + 2))
    alive_ok = True
    fh2 = DailyFeedHandler()
    fh2.push(mk(0))
    alive_ok = not fh2.is_dead(fh2.s.last_date[sym] + dt.timedelta(days=2))  # weekend != dead
    return {"fresh_accepted": bool(ok_fresh), "duplicate_rejected": bool(dup_rejected),
            "out_of_order_rejected": bool(ooo_rejected), "malformed_rejected": bool(bad_rejected),
            "holiday_counted_not_error": bool(holiday_counted),
            "dead_man_trips_on_stale_gap": bool(dead_ok),
            "weekend_does_not_trip_dead_man": bool(alive_ok),
            "stats": {"accepted": fh.s.n_accepted, "duplicates": fh.s.n_duplicates,
                      "out_of_order": fh.s.n_out_of_order, "no_bar": fh.s.n_no_bar_days}}


# ================================================================ self-test main
def main() -> int:
    print("=" * 64)
    print("  PHASE 4 FEED LAYER - self-test")
    print("=" * 64)
    mode, info = resolve_run_mode()
    print(f"  run_mode = {mode}")
    print(f"  {info['note']}")
    panel = pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")

    rep = fault_injection_report(panel)
    ok = all(v for k, v in rep.items() if k != "stats")
    for k, v in rep.items():
        if k != "stats":
            print(f"  [{'PASS' if v else 'FAIL'}] {k}")

    # replay-source smoke: stream 5 days, all bars accepted in order
    fh = DailyFeedHandler()
    n_days = 0
    for date, bars in replay_day_stream(panel[panel["symbol"].isin(["RELIANCE", "TCS"])]):
        for b in bars.values():
            fh.push(b)
        n_days += 1
        if n_days >= 5:
            break
    smoke = fh.s.n_accepted > 0 and fh.s.n_duplicates == 0 and fh.s.n_out_of_order == 0
    print(f"  [{'PASS' if smoke else 'FAIL'}] replay stream smoke ({fh.s.n_accepted} bars, "
          f"0 violations)")

    (C.ARTIFACTS_DIR / "p4_feed_selftest.json").write_text(json.dumps(
        {"run_mode": mode, "mode_info": info, "fault_injection": rep,
         "replay_smoke": bool(smoke)}, indent=2, default=str))
    print(f"  wrote {C.ARTIFACTS_DIR / 'p4_feed_selftest.json'}")
    print(f"\n{'FEED LAYER SELF-TEST PASSED' if ok and smoke else 'FEED SELF-TEST FINDING'}")
    return 0 if ok and smoke else 1


if __name__ == "__main__":
    raise SystemExit(main())
