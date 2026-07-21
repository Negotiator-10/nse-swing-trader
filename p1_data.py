"""
Phase 1 (part 1/2) - Historical intraday data pipeline for the NSE paper trader.

WHY yfinance (for now) and Kite (later)
---------------------------------------
The live paper trader will read Zerodha Kite websocket data (Phase 4+). But the
BACKTEST that decides whether a signal has edge only needs clean historical intraday
bars, and Kite history requires the paid plan + keys. yfinance publishes the NIFTY
(^NSEI) and BANKNIFTY (^NSEBANK) intraday index series for free, so we build and
HONESTLY GATE the backtest/signal/risk on real data now, and swap in Kite for the
live feed once keys exist. The index is a faithful intraday proxy for its
near-month future (they track within a small, slowly-varying basis).

LIMITATION (stated, not buried): yfinance intraday history is short (5m ~60 days).
That is enough to build the machinery and a first honest gate; a longer walk-forward
comes from Kite history later. We record the exact window in the handoff.

WHAT THIS MODULE DOES
---------------------
Fetch -> convert UTC to IST -> keep only the 09:15-15:30 session grid -> hygiene-gate
(monotone, de-duped, OHLC-sane, ~75 bars/session) -> split into in-sample / out-of-
sample by DATE (no intraday leakage) -> write a typed parquet + reload smoke test.

ASCII-only prints (Windows cp1252 console).

Run:  C:/Users/Admin/anaconda3/python.exe p1_data.py
Writes: data/{underlying}_{interval}.parquet, artifacts/p1_data_meta.json
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

import config as C

warnings.filterwarnings("ignore")

INTERVAL = "5m"                 # primary backtest grid (yfinance allows ~60d at 5m)
PERIOD = "60d"
OOS_FRAC = 0.30                 # last 30% of trading DAYS held out (walk-forward split)
BARS_PER_SESSION = 75          # 09:15-15:30 IST = 6h15m / 5m = 75 bars
YF_TICKER = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}


# ---------------------------------------------------------------- fetch + clean
def fetch_intraday(underlying: str, interval: str = INTERVAL, period: str = PERIOD) -> pd.DataFrame:
    """Fetch intraday bars, return a tidy IST-localized session-only OHLCV frame."""
    import yfinance as yf
    tkr = YF_TICKER[underlying]
    raw = yf.download(tkr, period=period, interval=interval, progress=False, auto_adjust=True)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"yfinance returned no data for {tkr}")
    # flatten possible MultiIndex columns (yfinance returns (field, ticker))
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()

    # to IST; yfinance intraday index is tz-aware UTC
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert(C.TIMEZONE)
    df.index.name = "ts_ist"

    # keep only the regular session grid [09:15, 15:30)
    t = df.index.time
    import datetime as _dt
    o = _dt.time(*map(int, C.MARKET_OPEN.split(":")))
    c = _dt.time(*map(int, C.MARKET_CLOSE.split(":")))
    df = df[(t >= o) & (t < c)]

    df = df[~df.index.duplicated(keep="first")].sort_index()
    df["date"] = df.index.normalize().tz_localize(None).date.astype(str)
    df["underlying"] = underlying
    return df.reset_index()


# ---------------------------------------------------------------- split
def date_split(df: pd.DataFrame, oos_frac: float = OOS_FRAC) -> pd.DataFrame:
    """Label each row train/test by DATE so no session straddles the boundary."""
    dates = sorted(df["date"].unique())
    n_oos = max(1, int(round(len(dates) * oos_frac)))
    oos_dates = set(dates[-n_oos:])
    df = df.copy()
    df["split"] = np.where(df["date"].isin(oos_dates), "test", "train")
    return df


# ---------------------------------------------------------------- gates
def run_gates(df: pd.DataFrame) -> tuple[bool, dict]:
    print("\n=== PHASE 1 (data) GATES ===")
    ok = True
    rep: dict = {}

    def check(name, cond, detail=""):
        nonlocal ok
        st = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"  [{st}] {name}  {detail}")
        return bool(cond)

    ts = pd.to_datetime(df["ts_ist"])

    # G1.1 monotone, de-duplicated timestamps
    mono = bool(ts.is_monotonic_increasing)
    dup = int(ts.duplicated().sum())
    rep["G1.1"] = {"monotone": mono, "n_duplicates": dup}
    check("G1.1 timestamps monotone and de-duplicated", mono and dup == 0,
          f"  monotone={mono} dups={dup}")

    # G1.2 OHLC sanity: high>=max(open,close), low<=min(open,close), high>=low, all>0
    o, h, l, cl = (df[c].to_numpy() for c in ("open", "high", "low", "close"))
    sane = bool(np.all(h >= l - 1e-9) and np.all(h >= np.maximum(o, cl) - 1e-9)
                and np.all(l <= np.minimum(o, cl) + 1e-9) and np.all(np.isfinite([o, h, l, cl]))
                and np.all(cl > 0))
    rep["G1.2"] = {"ohlc_sane": sane, "n_bars": int(len(df))}
    check("G1.2 OHLC internally consistent and positive", sane, f"  n_bars={len(df)}")

    # G1.3 session-grid completeness: most sessions have ~BARS_PER_SESSION bars
    per_day = df.groupby("date").size()
    full = int((per_day == BARS_PER_SESSION).sum())
    frac_full = full / len(per_day)
    within_1 = int((per_day.between(BARS_PER_SESSION - 1, BARS_PER_SESSION)).sum())
    rep["G1.3"] = {"n_sessions": int(len(per_day)), "n_full_sessions": full,
                   "frac_full": float(frac_full), "min_bars": int(per_day.min()),
                   "max_bars": int(per_day.max()), "sessions_within_1_bar": within_1}
    check("G1.3 sessions align to the NSE 75-bar grid (>=80% full, none over-full)",
          frac_full >= 0.80 and int(per_day.max()) <= BARS_PER_SESSION,
          f"  full={full}/{len(per_day)} bars/day in [{per_day.min()},{per_day.max()}]")

    # G1.4 walk-forward split is clean: train dates strictly precede test dates
    tr = df[df.split == "train"]["date"]
    te = df[df.split == "test"]["date"]
    clean_split = bool(len(tr) and len(te) and max(tr) < min(te))
    rep["G1.4"] = {"n_train_rows": int(len(tr)), "n_test_rows": int(len(te)),
                   "train_dates": [str(min(tr)), str(max(tr))] if len(tr) else [],
                   "test_dates": [str(min(te)), str(max(te))] if len(te) else []}
    check("G1.4 walk-forward split: all train dates strictly before all test dates",
          clean_split, f"  train<= {max(tr) if len(tr) else '-'} < test>= {min(te) if len(te) else '-'}")

    rep["all_passed"] = ok
    print(f"\n{'ALL PHASE 1 DATA GATES PASSED' if ok else 'GATE FAILURE (finding)'}\n")
    return ok, rep


# ---------------------------------------------------------------- smoke test
def smoke_test(path, expect_rows: int) -> bool:
    df = pd.read_parquet(path)
    ts = pd.to_datetime(df["ts_ist"])
    ok = (len(df) == expect_rows and ts.is_monotonic_increasing
          and set(["open", "high", "low", "close", "volume", "date", "split"]).issubset(df.columns))
    print(f"  [{'PASS' if ok else 'FAIL'}] smoke: reload parquet, {len(df)} rows, schema+order intact")
    return ok


# ---------------------------------------------------------------- main
def main() -> int:
    underlying = C.UNDERLYING
    print("=" * 64)
    print(f"  PHASE 1 DATA - {underlying} intraday {INTERVAL} (yfinance dev source)")
    print("=" * 64)
    df = fetch_intraday(underlying)
    df = date_split(df)
    print(f"  fetched {len(df):,} bars over {df['date'].nunique()} sessions "
          f"[{df['date'].min()} .. {df['date'].max()}] IST")

    ok, rep = run_gates(df)

    out = C.DATA_DIR / f"{underlying}_{INTERVAL}.parquet"
    df.to_parquet(out, index=False)
    print(f"  wrote {out}  ({len(df):,} rows)")

    meta = {
        "underlying": underlying, "yf_ticker": YF_TICKER[underlying],
        "interval": INTERVAL, "period": PERIOD, "source": "yfinance (dev); Kite for live",
        "proxy_note": "index series is an intraday proxy for the near-month future",
        "n_bars": int(len(df)), "n_sessions": int(df["date"].nunique()),
        "date_range": [str(df["date"].min()), str(df["date"].max())],
        "oos_frac": OOS_FRAC, "bars_per_session": BARS_PER_SESSION,
        "gates": rep, "seed": C.SEED,
        "versions": {"numpy": np.__version__, "pandas": pd.__version__},
    }
    (C.ARTIFACTS_DIR / "p1_data_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"  wrote {C.ARTIFACTS_DIR / 'p1_data_meta.json'}")

    smoke_ok = smoke_test(out, len(df))
    ok = ok and smoke_ok
    print(f"\n{'PHASE 1 DATA COMPLETE' if ok else 'PHASE 1 DATA INCOMPLETE (findings recorded)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
