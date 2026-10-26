"""
Phase 5 - live-vs-backtest reconciliation harness.

THE QUESTION THIS EXISTS TO ANSWER
----------------------------------
The backtest says this strategy earns a certain amount per trade, holds for a certain
number of days, and exits in a certain mix of ways. Live paper trading is now producing
real trades under the same rules. **Do they agree?** If live systematically underperforms
the backtest, something in the model is wrong -- optimistic fills, understated costs, or
an edge that does not survive contact with real prices.

WHY IT REPORTS "NOT YET MEANINGFUL" FOR MONTHS
----------------------------------------------
With a handful of trades, live and backtest CANNOT be distinguished: the noise dwarfs any
plausible difference. This harness therefore states its own statistical power explicitly
and refuses to render a verdict until the sample supports one. Running it early is still
useful -- it accumulates the comparison and shows the trend -- but a green light here
before MIN_TRADES_VERDICT trades would be a lie, so it is not offered.

WHAT IT COMPARES (live ledger vs the portfolio book's OOS trades at the CURRENT config)
  1. net return per trade (bp)      - the headline: is the edge showing up?
  2. holding period (days)
  3. win rate
  4. exit-reason mix                - the sharpest early diagnostic (see below)
  5. cost per trade as % of notional

WHY THE EXIT MIX MATTERS EARLY. It needs no return statistics -- only counts -- so it
becomes informative long before P&L does. If the backtest exits mostly on stops but live
exits mostly on trend breaks, the live book is meeting a different market regime, and the
stop-fill assumption (finding #001-c, which is the whole reason Phase 5 exists) cannot even
be measured until live stop exits appear.

SAFETY: read-only. Writes ONLY artifacts/p5_reconcile.json.

Run:  python p12_swing_reconcile.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import config as C
import p9_swing_portfolio as P9

MIN_TRADES_VERDICT = 30      # below this, no verdict is rendered -- only accumulation
MIN_STOPS_FOR_001C = 5       # stop exits needed before the fill-quality question is asked


def _boot_ci(x: np.ndarray, n_boot: int = 5000, seed: int = C.SEED) -> tuple:
    """Bootstrap 95% CI of the mean (the ledger is far too small for a normal assumption)."""
    if len(x) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n_boot, len(x)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def live_trades() -> pd.DataFrame:
    """The live paper ledger, with net return per trade in basis points."""
    if not C.PAPER_LEDGER_PATH.exists():
        return pd.DataFrame()
    t = pd.read_parquet(C.PAPER_LEDGER_PATH)
    if not len(t):
        return t
    t = t.copy()
    notional = t["entry_px"] * t["shares"]
    t["net_bp"] = t["pnl"] / notional * 1e4
    t["cost_pct"] = np.nan          # filled below from the modelled stack
    costs = [P9.delivery_roundtrip_cost(r["entry_px"] * r["shares"],
                                        r["exit_px"] * r["shares"])
             for _, r in t.iterrows()]
    t["cost_inr"] = costs
    t["cost_pct"] = t["cost_inr"] / notional * 100
    return t


def backtest_trades() -> pd.DataFrame:
    """OOS trades from the PORTFOLIO book at the CURRENT config.

    Deliberately not swing_trades.parquet -- that is the Phase-2 signal-level backtest and
    goes stale the moment the config changes. This re-runs the same book the live system
    mirrors, so the comparison is always against what the live rules would have done.
    """
    panel = pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")
    risk = pd.read_parquet(C.DATA_DIR / "swing_risk_panel.parquet")
    mkt = P9.Market(panel, risk)
    fixed = P9.budget_fn_factory(None, combined=False)
    standalone = {s: P9.run_book(mkt, [s], fixed, caps=P9.default_caps(),
                                 kill=P9.default_kill()) for s in P9.SLEEVES}
    weights = P9.sleeve_weights({s: standalone[s]["ret"] for s in P9.SLEEVES})
    fb = P9.budget_fn_factory(weights, combined=True)
    res = P9.run_book(mkt, P9.SLEEVES, fb, caps=P9.default_caps(), kill=P9.default_kill())
    t = pd.DataFrame(res["trades"])
    if not len(t):
        return t
    t = t[t["split"].astype(str) == "test"].copy()      # OOS only: live is out-of-sample
    notional = t["entry_px"] * t["shares"]
    t["net_bp"] = t["pnl"] / notional * 1e4
    return t


def compare(live: pd.DataFrame, bt: pd.DataFrame) -> dict:
    out = {"n_live": int(len(live)), "n_backtest_oos": int(len(bt)),
           "min_trades_for_verdict": MIN_TRADES_VERDICT}
    if not len(live) or not len(bt):
        out["verdict"] = "NO DATA"
        return out

    lo, hi = _boot_ci(live["net_bp"].to_numpy(float))
    out["net_bp"] = {
        "live_mean": float(live["net_bp"].mean()), "live_ci95": [lo, hi],
        "backtest_mean": float(bt["net_bp"].mean()),
        "backtest_within_live_ci": bool(lo <= bt["net_bp"].mean() <= hi)
        if np.isfinite(lo) else None}
    out["days_held"] = {"live_mean": float(live["days_held"].mean()),
                        "backtest_mean": float(bt["days_held"].mean())}
    out["win_rate"] = {"live": float((live["pnl"] > 0).mean()),
                       "backtest": float((bt["pnl"] > 0).mean())}
    lm = live["reason"].value_counts(normalize=True).to_dict()
    bm = bt["reason"].value_counts(normalize=True).to_dict()
    out["exit_mix"] = {"live": {k: float(v) for k, v in lm.items()},
                       "backtest": {k: float(v) for k, v in bm.items()}}
    out["cost_pct_of_notional"] = {"live_mean": float(live["cost_pct"].mean())}

    n_stop_live = int(live["reason"].astype(str).isin(["stop", "stop_gap"]).sum())
    out["stop_exits_live"] = n_stop_live
    out["finding_001c_measurable"] = bool(n_stop_live >= MIN_STOPS_FOR_001C)

    # RIGHT-CENSORING (the trap this harness must not fall into). The live era is young:
    # trades that would run long are still OPEN, so the CLOSED ledger is biased toward
    # short holds, and a max-hold exit is arithmetically impossible until the era itself
    # exceeds MAXHOLD days. Until then the hold-period and exit-mix comparisons are
    # artifacts, not findings, and are labelled as such rather than quietly reported.
    era_days = int(len(pd.bdate_range(C.PAPER_ERA_START, pd.Timestamp.today())))
    maxhold = int(P9.MAXHOLD[P9.SLEEVES[0]])
    out["era_trading_days"] = era_days
    out["maxhold_days"] = maxhold
    out["duration_comparison_censored"] = bool(era_days < maxhold * 2)
    out["maxhold_exit_possible_yet"] = bool(era_days >= maxhold)

    if len(live) < MIN_TRADES_VERDICT:
        out["verdict"] = (f"ACCUMULATING - {len(live)} of {MIN_TRADES_VERDICT} trades. "
                          f"Differences at this sample size are noise; no verdict is "
                          f"rendered by design.")
    else:
        agree = out["net_bp"]["backtest_within_live_ci"]
        out["verdict"] = ("LIVE CONSISTENT with backtest (backtest mean inside the live 95% CI)"
                          if agree else
                          "LIVE DIVERGES from backtest - investigate fills, costs, regime")
    return out


def main() -> int:
    print("=" * 74)
    print("  PHASE 5 - live vs backtest reconciliation")
    print("=" * 74)
    live = live_trades()
    if not len(live):
        print("  no live trades yet -- nothing to reconcile")
        return 0
    print(f"  live ledger: {len(live)} closed trades since {C.PAPER_ERA_START}")
    print("  running the portfolio book at the current config for the OOS baseline...")
    bt = backtest_trades()
    rep = compare(live, bt)

    print(f"\n  {'metric':<26} {'LIVE':>16} {'BACKTEST (OOS)':>18}")
    print("  " + "-" * 62)
    nb = rep["net_bp"]
    print(f"  {'net return / trade (bp)':<26} {nb['live_mean']:>16.1f} {nb['backtest_mean']:>18.1f}")
    print(f"  {'  live 95% CI':<26} {'[%.0f, %.0f]' % tuple(nb['live_ci95']):>16} {'':>18}")
    dh = rep["days_held"]
    print(f"  {'holding period (days)':<26} {dh['live_mean']:>16.1f} {dh['backtest_mean']:>18.1f}")
    wr = rep["win_rate"]
    print(f"  {'win rate':<26} {wr['live']*100:>15.0f}% {wr['backtest']*100:>17.0f}%")
    print(f"  {'trades':<26} {rep['n_live']:>16} {rep['n_backtest_oos']:>18}")

    if rep.get("duration_comparison_censored"):
        print(f"\n  [CENSORED] the live era is only {rep['era_trading_days']} trading days old "
              f"vs a {rep['maxhold_days']}-day max hold.")
        print("             Long trades are still OPEN, so the closed ledger is biased toward")
        print("             SHORT holds, and a max-hold exit is arithmetically impossible yet.")
        print("             Treat the holding-period and exit-mix rows below as ARTIFACTS,")
        print("             not findings, until the era is well past the max hold.")

    print("\n  exit-reason mix (informative long before P&L is):")
    for k in sorted(set(rep["exit_mix"]["live"]) | set(rep["exit_mix"]["backtest"])):
        l = rep["exit_mix"]["live"].get(k, 0.0) * 100
        b = rep["exit_mix"]["backtest"].get(k, 0.0) * 100
        print(f"    {k:<24} {l:>15.0f}% {b:>17.0f}%")

    print(f"\n  live stop exits: {rep['stop_exits_live']} "
          f"(need {MIN_STOPS_FOR_001C} before finding #001-c, the stop-fill assumption, "
          f"can be measured at all)")
    print(f"\n  VERDICT: {rep['verdict']}")

    (C.ARTIFACTS_DIR / "p5_reconcile.json").write_text(json.dumps(rep, indent=2))
    print(f"\n  wrote {(C.ARTIFACTS_DIR / 'p5_reconcile.json').name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
