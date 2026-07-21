"""
Phase 2 (part 1/2) - Swing signal library: a small set of pre-specified, economically
motivated entry/exit specs, tested honestly (select on IS, confirm on OOS, deflate by
the trial count in p4_signal_search.py).

Each spec augments ONE symbol's sorted daily frame with three causal columns the
backtest consumes:
    entry       : bool -- open a long at the NEXT open
    exit_trend  : bool -- a "signal" exit (distinct from the ATR stop / max-hold)
    atr         : float -- for stop sizing
plus per-spec params (ATR stop multiple, max-hold days) held in the SPECS registry.

THE SPECS (rationale each)
--------------------------
  breakout_mom : close > MA(trend) AND new N-day high.        (momentum breakout; baseline)
  mean_rev     : close > MA200 AND RSI(2) < 10.               (buy oversold dips IN an uptrend)
  ts_mom       : close > MA200 AND 126d return > 0 AND        (longer-horizon trend; slower)
                 close > MA50.
  dual_ma      : MA20 crosses above MA50 AND close > MA200.   (classic trend cross)

NO LOOK-AHEAD: every column at day t uses only bars <= t (a gate re-checks on df[:t+1]).
ASCII-only prints. Run:  C:/Users/Admin/anaconda3/python.exe p2_swing_signal.py
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

import config as C

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------- indicators (all causal)
def atr(df: pd.DataFrame, n: int = None) -> pd.Series:
    n = n or C.SWING_ATR_LEN
    pc = df["close"].shift(1)
    tr = pd.concat([(df["high"] - df["low"]).abs(), (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int) -> pd.Series:
    """Wilder RSI over n periods (causal)."""
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    au = up.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    ad = dn.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    rs = au / ad.replace(0.0, np.nan)
    return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("date").reset_index(drop=True).copy()
    df["atr"] = atr(df)
    df["tradable"] = df["volume"] > 0
    return df


# ---------------------------------------------------------------- spec builders
def _breakout_mom(df: pd.DataFrame) -> pd.DataFrame:
    df = _prep(df)
    ma = df["close"].rolling(C.SWING_TREND_MA, min_periods=C.SWING_TREND_MA).mean()
    ref = df["high"].rolling(C.SWING_BREAKOUT_LOOKBACK, min_periods=C.SWING_BREAKOUT_LOOKBACK).max().shift(1)
    df["entry"] = ((df["close"] > ma) & (df["close"] > ref) & df["tradable"]).fillna(False)
    df["exit_trend"] = (df["close"] < ma).fillna(False)
    return df


def _mean_rev(df: pd.DataFrame) -> pd.DataFrame:
    df = _prep(df)
    ma200 = df["close"].rolling(200, min_periods=200).mean()
    r2 = rsi(df["close"], 2)
    ma_exit = df["close"].rolling(10, min_periods=10).mean()
    df["entry"] = ((df["close"] > ma200) & (r2 < 10.0) & df["tradable"]).fillna(False)
    # exit when the bounce completes (back above short MA) or the uptrend fails
    df["exit_trend"] = ((df["close"] > ma_exit) | (df["close"] < ma200)).fillna(False)
    return df


def _ts_mom(df: pd.DataFrame) -> pd.DataFrame:
    df = _prep(df)
    ma200 = df["close"].rolling(200, min_periods=200).mean()
    ma50 = df["close"].rolling(50, min_periods=50).mean()
    ret126 = df["close"] / df["close"].shift(126) - 1.0
    df["entry"] = ((df["close"] > ma200) & (ret126 > 0.0) & (df["close"] > ma50)
                   & df["tradable"]).fillna(False)
    df["exit_trend"] = (df["close"] < ma50).fillna(False)
    return df


def _dual_ma(df: pd.DataFrame) -> pd.DataFrame:
    df = _prep(df)
    ma20 = df["close"].rolling(20, min_periods=20).mean()
    ma50 = df["close"].rolling(50, min_periods=50).mean()
    ma200 = df["close"].rolling(200, min_periods=200).mean()
    cross_up = (ma20 > ma50) & (ma20.shift(1) <= ma50.shift(1))
    df["entry"] = (cross_up & (df["close"] > ma200) & df["tradable"]).fillna(False)
    df["exit_trend"] = (ma20 < ma50).fillna(False)
    return df


def _mr_rsi(df: pd.DataFrame) -> pd.DataFrame:
    """Deep short-term oversold (RSI2<5) INSIDE a 200-day uptrend; exit on the bounce."""
    df = _prep(df)
    ma200 = df["close"].rolling(200, min_periods=200).mean()
    ma5 = df["close"].rolling(5, min_periods=5).mean()
    r2 = rsi(df["close"], 2)
    df["entry"] = ((df["close"] > ma200) & (r2 < 5.0) & df["tradable"]).fillna(False)
    df["exit_trend"] = ((df["close"] > ma5) | (df["close"] < ma200)).fillna(False)
    return df


def _mr_bb(df: pd.DataFrame) -> pd.DataFrame:
    """Close below the lower Bollinger band (MA20 - 2 sigma) in an uptrend; exit at the mean."""
    df = _prep(df)
    ma200 = df["close"].rolling(200, min_periods=200).mean()
    ma20 = df["close"].rolling(20, min_periods=20).mean()
    sd20 = df["close"].rolling(20, min_periods=20).std()
    df["entry"] = ((df["close"] > ma200) & (df["close"] < ma20 - 2 * sd20) & df["tradable"]).fillna(False)
    df["exit_trend"] = ((df["close"] > ma20) | (df["close"] < ma200)).fillna(False)
    return df


def _mr_down3(df: pd.DataFrame) -> pd.DataFrame:
    """Three consecutive lower closes in an uptrend (short-term pullback); exit on a bounce."""
    df = _prep(df)
    ma200 = df["close"].rolling(200, min_periods=200).mean()
    dn = df["close"] < df["close"].shift(1)
    down3 = dn & dn.shift(1) & dn.shift(2)
    df["entry"] = ((df["close"] > ma200) & down3 & df["tradable"]).fillna(False)
    df["exit_trend"] = ((df["close"] > df["high"].shift(1)) | (df["close"] < ma200)).fillna(False)
    return df


def _vol_squeeze(df: pd.DataFrame) -> pd.DataFrame:
    """Coiled spring: breakout of the 20-day high AFTER a low-volatility squeeze, in uptrend."""
    df = _prep(df)
    ma200 = df["close"].rolling(200, min_periods=200).mean()
    ma20 = df["close"].rolling(20, min_periods=20).mean()
    sd20 = df["close"].rolling(20, min_periods=20).std()
    bbw = (2 * sd20) / ma20
    squeeze = bbw <= bbw.rolling(100, min_periods=100).quantile(0.20)
    hi20 = df["high"].rolling(20, min_periods=20).max().shift(1)
    df["entry"] = (squeeze.shift(1).fillna(False) & (df["close"] > hi20)
                   & (df["close"] > ma200) & df["tradable"]).fillna(False)
    df["exit_trend"] = (df["close"] < ma20).fillna(False)
    return df


def _tom(df: pd.DataFrame) -> pd.DataFrame:
    """Turn-of-month: enter on the last trading day of a month (uptrend), hold a few days.

    LIVE-EDGE FIX (Phase 4): for the NEWEST bar there is no next bar, and comparing
    against NaT made every live day look like turn-of-month (harmless in backtest --
    a final-bar entry never fills -- but wrong live). The trading calendar is public
    information, so for the final bar we approximate the next session with the next
    BUSINESS day: causal, and only month-end-adjacent holidays can mislabel it."""
    df = _prep(df)
    ma200 = df["close"].rolling(200, min_periods=200).mean()
    dt = pd.to_datetime(df["date"])
    nxt = dt.shift(-1).fillna(dt + pd.offsets.BDay(1))  # final bar: next weekday stand-in
    last_td = dt.dt.month != nxt.dt.month              # last trading day of its month
    df["entry"] = (last_td & (df["close"] > ma200) & df["tradable"]).fillna(False)
    df["exit_trend"] = pd.Series(False, index=df.index)   # rely on the (short) max-hold
    return df


SPECS = {
    "breakout_mom": {"build": _breakout_mom, "stop_mult": 2.5, "maxhold": 25, "warmup": 60},
    "mean_rev":     {"build": _mean_rev,     "stop_mult": 3.0, "maxhold": 15, "warmup": 205},
    "ts_mom":       {"build": _ts_mom,       "stop_mult": 3.5, "maxhold": 25, "warmup": 205},
    "dual_ma":      {"build": _dual_ma,      "stop_mult": 3.0, "maxhold": 25, "warmup": 205},
    # mean-reversion family (candidate diversifiers vs ts_mom trend-following)
    "mr_rsi":       {"build": _mr_rsi,       "stop_mult": 3.0, "maxhold": 10, "warmup": 205},
    "mr_bb":        {"build": _mr_bb,        "stop_mult": 3.0, "maxhold": 10, "warmup": 205},
    "mr_down3":     {"build": _mr_down3,     "stop_mult": 3.0, "maxhold": 8,  "warmup": 205},
    # structurally-distinct diversifier candidates
    "vol_squeeze":  {"build": _vol_squeeze,  "stop_mult": 3.0, "maxhold": 20, "warmup": 305},
    "tom":          {"build": _tom,          "stop_mult": 3.0, "maxhold": 5,  "warmup": 205},
}
MR_FAMILY = ["mr_rsi", "mr_bb", "mr_down3"]
DIVERSIFIER_CANDIDATES = ["vol_squeeze", "tom"]


def compute_signals(df: pd.DataFrame, spec: str = "breakout_mom") -> pd.DataFrame:
    """Augment one symbol's frame with entry/exit_trend/atr for the named spec."""
    return SPECS[spec]["build"](df)


# ---------------------------------------------------------------- no-lookahead gate
def _no_lookahead_gate(panel: pd.DataFrame, spec: str, n_probe: int = 120) -> bool:
    rng = np.random.default_rng(C.SEED)
    ok = True
    checked = 0
    for sym, g in panel.groupby("symbol"):
        s = compute_signals(g, spec)
        valid = s.index[s["atr"].notna()][C.SWING_BREAKOUT_LOOKBACK:]
        if len(valid) < 5:
            continue
        for t in rng.choice(valid, size=min(max(n_probe // panel["symbol"].nunique(), 1), len(valid)),
                            replace=False):
            trunc = compute_signals(g.iloc[:t + 1], spec)
            for col in ("entry", "exit_trend", "atr"):
                a, b = s.iloc[t][col], trunc.iloc[-1][col]
                if pd.isna(a) and pd.isna(b):
                    continue
                if float(a) != float(b) and abs(float(a) - float(b)) > 1e-9:
                    ok = False
            checked += 1
    print(f"    [{'PASS' if ok else 'FAIL'}] {spec:12s} causal ({checked} probes)")
    return ok


def main() -> int:
    panel = pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")
    print(f"loaded {len(panel):,} bars, {panel['symbol'].nunique()} symbols")
    print("\n=== PHASE 2 SIGNAL - no-lookahead gate (all specs) ===")
    ok = all(_no_lookahead_gate(panel, s) for s in SPECS)
    print("\n  entry frequency by spec (sanity):")
    for s in SPECS:
        n = sum(int(compute_signals(g, s)["entry"].sum()) for _, g in panel.groupby("symbol"))
        print(f"    {s:12s} {n:5d} entry-days")
    print(f"\n{'ALL SIGNAL SPECS CAUSAL' if ok else 'SIGNAL SELF-TEST FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
