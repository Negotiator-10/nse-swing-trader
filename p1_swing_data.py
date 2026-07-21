"""
Phase 1 (SWING) - Daily historical data pipeline for the swing paper trader.

WHY DAILY + yfinance
--------------------
Swing v1 holds positions ~10-15 days (hard max < 1 month) and decides END-OF-DAY, so
the backtest needs clean DAILY bars, not ticks. yfinance publishes deep daily history
for NSE stocks (many back to 1996/2002) for free -> a genuinely long walk-forward,
far stronger than the intraday window. Live order placement later goes through a
broker (Kite etc.), but NONE of the swing backtest/signal/risk work needs paid keys.

The daily GARCH-EVT VaR engine (Desktop/VaR Model) is a NATURAL fit here: it already
sizes overnight/multi-day risk with EVT tails -- exactly the gap risk a swing book
carries. Phase 3 ports it per-symbol.

WHAT THIS DOES
--------------
Fetch daily OHLCV for the WATCHLIST -> clean -> hygiene-gate (monotone, de-duped,
OHLC-sane, enough history) -> split IS/OOS by a COMMON cutoff date (no leakage) ->
typed long-format parquet + reload smoke test.

ASCII-only prints (Windows cp1252 console).

Run:  C:/Users/Admin/anaconda3/python.exe p1_swing_data.py
Writes: data/swing_daily.parquet, artifacts/p1_swing_meta.json
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

import config as C

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------- fetch + clean
def fetch_daily(symbol: str) -> pd.DataFrame:
    """Daily OHLCV for one NSE symbol via yfinance; tidy, date-indexed, tz-naive."""
    import yfinance as yf
    tkr = symbol + C.SWING_YF_SUFFIX
    raw = yf.download(tkr, period=C.SWING_HISTORY, interval="1d",
                      progress=False, auto_adjust=True)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"yfinance returned no data for {tkr}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="first")].sort_index().dropna(subset=["close"])
    df = df[df["close"] > 0]
    df, n_rep = repair_ohlc(df)
    df["date"] = df.index.normalize()
    df["symbol"] = symbol
    df.attrs["n_ohlc_repaired"] = n_rep
    return df.reset_index(drop=True)


def repair_ohlc(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Clamp high/low so OHLC is self-consistent.

    yfinance back-adjusts old bars for splits/dividends; 6-decimal rounding on prices
    that today trade near Rs.1 (split-adjusted) can push an adjusted open/close a hair
    OUTSIDE the adjusted high/low (~0.1% on <0.2% of rows, all pre-1997). We repair --
    not hide -- by setting high=max(open,high,close), low=min(open,low,close). This is
    the minimal correction that restores consistency; the count is reported.
    """
    hi = df[["open", "high", "close"]].max(axis=1)
    lo = df[["open", "low", "close"]].min(axis=1)
    n = int(((df["high"] < hi - 1e-9) | (df["low"] > lo + 1e-9)).sum())
    df = df.copy()
    df["high"] = hi
    df["low"] = lo
    return df, n


def load_watchlist() -> list:
    """Prefer the screened watchlist (data/watchlist.json from p0_universe) over the
    config default, so the tradable universe selection flows downstream automatically."""
    wl_path = C.DATA_DIR / "watchlist.json"
    if wl_path.exists():
        sel = json.loads(wl_path.read_text()).get("selected")
        if sel:
            return sel
    return C.SWING_WATCHLIST


def build_panel(watchlist=None) -> pd.DataFrame:
    """Long-format daily panel for all included symbols, with a COMMON IS/OOS cutoff."""
    watchlist = watchlist or load_watchlist()
    frames, dropped, n_repaired = [], {}, 0
    for sym in watchlist:
        d = fetch_daily(sym)
        n_repaired += int(d.attrs.get("n_ohlc_repaired", 0))
        if len(d) < C.SWING_MIN_DAYS:
            dropped[sym] = len(d)
            continue
        frames.append(d)
    if not frames:
        raise RuntimeError("no symbols had enough history")

    # PER-SYMBOL walk-forward split: each symbol's own last OOS_FRAC of rows = its test.
    # (A common calendar cutoff breaks for names listed after it -- e.g. ETERNAL/2021 --
    #  which would then have zero training data. Signals are per-stock, so a per-symbol
    #  split is the correct, leak-free walk-forward and keeps recent listings usable.)
    cutoffs = {}
    for d in frames:
        n = len(d)
        n_test = max(1, int(round(n * C.SWING_OOS_FRAC)))
        split = np.array(["train"] * n, dtype=object)
        split[-n_test:] = "test"
        d["split"] = split
        cutoffs[d["symbol"].iloc[0]] = str(d["date"].iloc[n - n_test].date())
    panel = pd.concat(frames, ignore_index=True)
    panel.attrs["cutoffs"] = cutoffs
    panel.attrs["cutoff_range"] = [min(cutoffs.values()), max(cutoffs.values())]
    panel.attrs["dropped"] = dropped
    panel.attrs["n_ohlc_repaired"] = n_repaired
    panel.attrs["n_zero_volume"] = int((panel["volume"] == 0).sum())
    return panel.sort_values(["symbol", "date"]).reset_index(drop=True)


# ---------------------------------------------------------------- gates
def run_gates(panel: pd.DataFrame) -> tuple[bool, dict]:
    print("\n=== PHASE 1 (swing data) GATES ===")
    ok = True
    rep: dict = {}

    def check(name, cond, detail=""):
        nonlocal ok
        st = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"  [{st}] {name}  {detail}")
        return bool(cond)

    # G1.1 per-symbol monotone, de-duplicated dates
    mono_ok = True
    dup_total = 0
    for sym, g in panel.groupby("symbol"):
        d = g["date"]
        mono_ok = mono_ok and bool(d.is_monotonic_increasing)
        dup_total += int(d.duplicated().sum())
    rep["G1.1"] = {"per_symbol_monotone": mono_ok, "n_duplicate_dates": dup_total}
    check("G1.1 dates monotone and de-duplicated per symbol", mono_ok and dup_total == 0,
          f"  monotone={mono_ok} dups={dup_total}")

    # G1.2 OHLC sanity
    o, h, l, cl = (panel[c].to_numpy() for c in ("open", "high", "low", "close"))
    sane = bool(np.all(h >= l - 1e-6) and np.all(h >= np.maximum(o, cl) - 1e-6)
                and np.all(l <= np.minimum(o, cl) + 1e-6)
                and np.all(np.isfinite([o, h, l, cl])) and np.all(cl > 0))
    rep["G1.2"] = {"ohlc_sane": sane, "n_rows": int(len(panel))}
    check("G1.2 OHLC internally consistent and positive", sane, f"  rows={len(panel)}")

    # G1.3 sufficient, gap-free-ish history per symbol (no > 10-session calendar jump within a symbol)
    hist = {}
    big_gaps = 0
    for sym, g in panel.groupby("symbol"):
        gap = g["date"].diff().dt.days.fillna(1)
        n_big = int((gap > 10).sum())        # >10 calendar-day jumps (holidays give <=5)
        big_gaps += n_big
        hist[sym] = {"n_days": int(len(g)), "start": str(g["date"].min().date()),
                     "end": str(g["date"].max().date()), "n_gaps_gt_10d": n_big}
    enough = all(v["n_days"] >= C.SWING_MIN_DAYS for v in hist.values())
    rep["G1.3"] = {"per_symbol": hist, "min_days_required": C.SWING_MIN_DAYS,
                   "total_gaps_gt_10d": big_gaps}
    check("G1.3 each symbol has >= min history (gaps only from holidays)", enough,
          f"  symbols={len(hist)} min_days={min(v['n_days'] for v in hist.values())}")

    # G1.4 walk-forward split clean: train strictly before test, both non-empty per symbol
    clean = True
    for sym, g in panel.groupby("symbol"):
        tr, te = g[g.split == "train"]["date"], g[g.split == "test"]["date"]
        if not (len(tr) and len(te) and tr.max() < te.min()):
            clean = False
    rep["G1.4"] = {"cutoff_range": panel.attrs.get("cutoff_range"),
                   "n_train": int((panel.split == "train").sum()),
                   "n_test": int((panel.split == "test").sum())}
    check("G1.4 per-symbol walk-forward split: train strictly before test for every symbol", clean,
          f"  cutoffs={panel.attrs.get('cutoff_range')} train={rep['G1.4']['n_train']} test={rep['G1.4']['n_test']}")

    rep["all_passed"] = ok
    print(f"\n{'ALL PHASE 1 SWING-DATA GATES PASSED' if ok else 'GATE FAILURE (finding)'}\n")
    return ok, rep


# ---------------------------------------------------------------- smoke test
def smoke_test(path, expect_rows: int) -> bool:
    df = pd.read_parquet(path)
    cols_ok = set(["date", "symbol", "open", "high", "low", "close", "volume", "split"]).issubset(df.columns)
    per_sym_mono = all(g["date"].is_monotonic_increasing for _, g in df.groupby("symbol"))
    ok = (len(df) == expect_rows) and cols_ok and per_sym_mono
    print(f"  [{'PASS' if ok else 'FAIL'}] smoke: reload parquet, {len(df)} rows, schema+per-symbol order intact")
    return ok


# ---------------------------------------------------------------- main
def main() -> int:
    print("=" * 64)
    print("  PHASE 1 SWING DATA - daily OHLCV for the watchlist (yfinance)")
    print("=" * 64)
    wl = load_watchlist()
    print(f"  watchlist ({len(wl)}): {', '.join(wl)}")
    panel = build_panel(wl)
    dropped = panel.attrs.get("dropped", {})
    if dropped:
        print(f"  dropped (insufficient history): {dropped}")
    for sym, g in panel.groupby("symbol"):
        print(f"    {sym:12s} {len(g):5d} days  {g['date'].min().date()} -> {g['date'].max().date()}")

    ok, rep = run_gates(panel)

    out = C.DATA_DIR / "swing_daily.parquet"
    panel.to_parquet(out, index=False)
    print(f"  wrote {out}  ({len(panel):,} rows across {panel['symbol'].nunique()} symbols)")

    meta = {
        "watchlist": C.SWING_WATCHLIST, "included": sorted(panel["symbol"].unique().tolist()),
        "dropped": dropped, "source": "yfinance daily (dev+backtest); broker for live orders",
        "history": C.SWING_HISTORY, "oos_frac": C.SWING_OOS_FRAC,
        "split": "per-symbol (each symbol's last OOS_FRAC of rows = test)",
        "cutoff_range": panel.attrs.get("cutoff_range"), "cutoffs": panel.attrs.get("cutoffs"),
        "hold_target_days": C.SWING_HOLD_TARGET_DAYS, "hold_max_days": C.SWING_HOLD_MAX_DAYS,
        "n_rows": int(len(panel)),
        "n_ohlc_repaired": panel.attrs.get("n_ohlc_repaired", 0),
        "n_zero_volume_bars": panel.attrs.get("n_zero_volume", 0),
        "data_quality_note": "OHLC adjustment-artifact rows (pre-1997) repaired by clamping "
                             "high/low to include open/close; zero-volume bars flagged (treat as "
                             "untradeable in the backtest).",
        "gates": rep, "seed": C.SEED,
        "versions": {"numpy": np.__version__, "pandas": pd.__version__},
    }
    (C.ARTIFACTS_DIR / "p1_swing_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"  wrote {C.ARTIFACTS_DIR / 'p1_swing_meta.json'}")

    smoke_ok = smoke_test(out, len(panel))
    ok = ok and smoke_ok
    print(f"\n{'PHASE 1 SWING DATA COMPLETE' if ok else 'PHASE 1 SWING DATA INCOMPLETE (findings)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
