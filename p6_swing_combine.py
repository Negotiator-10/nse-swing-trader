"""
Phase 2 (diversification) - Find a second, UNCORRELATED signal and test combining it.

GOAL
----
ts_mom is a trend-follower: it loses in choppy/range regimes (2018, 2022). A mean-
reversion signal that profits in exactly those regimes -- i.e. LOW correlation with
ts_mom -- diversifies the book and should lift risk-adjusted return even if its
stand-alone edge is smaller.

DISCIPLINE (unchanged)
----------------------
Pre-specified MR family (mr_rsi / mr_bb / mr_down3). SELECT best on IS, CONFIRM once on
OOS, DEFLATE by the TOTAL trial count across the whole program (len(SPECS)=7). Then:
  * correlation of the two signals' MONTHLY OOS return streams (the diversification test),
  * a simple equal-weight COMBINED book vs ts_mom-alone: OOS Sharpe, max drawdown, worst
    month. A diversifier must IMPROVE risk-adjusted return, not just add trades.
A "no useful diversifier" outcome is a valid finding.

ASCII-only. Run:  C:/Users/Admin/anaconda3/python.exe p6_swing_combine.py
Writes: artifacts/p2_combine.json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import config as C
from p2_swing_signal import SPECS, MR_FAMILY
from p3_swing_backtest import backtest_symbol, trade_metrics, _boot_ci

TRIALS_TOTAL = len(SPECS)          # honest deflation across every signal ever screened


def run_spec(panel, spec) -> pd.DataFrame:
    tr = []
    for _, g in panel.groupby("symbol"):
        tr.extend(backtest_symbol(g, spec))
    df = pd.DataFrame(tr)
    if len(df):
        df["exit_date"] = pd.to_datetime(df["exit_date"])
        df["entry_date"] = pd.to_datetime(df["entry_date"])
    return df


def monthly_returns(trades) -> pd.Series:
    """Equal-weight average per-trade net return by EXIT month; 0 in months with no trades."""
    oos = trades[trades.split == "test"].copy()
    if len(oos) == 0:
        return pd.Series(dtype=float)
    oos["m"] = oos["exit_date"].dt.to_period("M")
    s = oos.groupby("m")["net_ret"].mean()
    idx = pd.period_range(s.index.min(), s.index.max(), freq="M")
    return s.reindex(idx, fill_value=0.0)


def perf(series: pd.Series) -> dict:
    """Monthly-return series -> annualized Sharpe, max drawdown, worst month, mean."""
    if len(series) < 6:
        return {"n_months": len(series)}
    mu, sd = series.mean(), series.std(ddof=1)
    sharpe = float(mu / sd * np.sqrt(12)) if sd > 0 else float("nan")
    cum = (1 + series).cumprod()
    dd = float((cum / cum.cummax() - 1).min())
    return {"n_months": int(len(series)), "mean_monthly_bp": float(mu * 1e4),
            "ann_sharpe": sharpe, "max_drawdown_pct": dd * 100,
            "worst_month_bp": float(series.min() * 1e4), "total_return_pct": float((cum.iloc[-1] - 1) * 100)}


def main() -> int:
    print("=" * 66)
    print("  PHASE 2 DIVERSIFICATION - second signal + combine (deflate x%d)" % TRIALS_TOTAL)
    print("=" * 66)
    panel = pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")

    # ---- MR family: metrics, select on IS ----
    print("\n  mean-reversion family (IS selects; OOS shown):")
    print("    %-10s %6s %9s | %6s %9s %9s" % ("spec", "n_IS", "IS_bp", "n_OOS", "OOS_bp", "CI_LB"))
    mr_res = {}
    for spec in MR_FAMILY:
        tr = run_spec(panel, spec)
        ism = trade_metrics(tr[tr.split == "train"])
        oosm = trade_metrics(tr[tr.split == "test"])
        mr_res[spec] = {"is": ism, "oos": oosm}
        print("    %-10s %6d %+8.1f | %6d %+8.1f %+8.1f" % (
            spec, ism.get("n", 0), ism.get("mean_net_ret_bp", float("nan")),
            oosm.get("n", 0), oosm.get("mean_net_ret_bp", float("nan")),
            oosm.get("ci95_mean_bp", [float("nan")])[0]))
    eligible = [s for s in MR_FAMILY if mr_res[s]["is"].get("n", 0) >= 50]
    mr_best = max(eligible, key=lambda s: mr_res[s]["is"]["mean_net_ret_bp"]) if eligible else None

    # ---- confirm MR best on OOS, deflated by total trials ----
    mr_trades = run_spec(panel, mr_best) if mr_best else pd.DataFrame()
    conf = {}
    if mr_best:
        r = mr_trades[mr_trades.split == "test"]["net_ret"].to_numpy()
        lo95, hi95 = _boot_ci(r, alpha=0.05)
        lo_adj, hi_adj = _boot_ci(r, alpha=0.05 / TRIALS_TOTAL)
        conf = {"selected": mr_best, "n_oos": int(len(r)), "oos_mean_bp": float(r.mean() * 1e4),
                "ci95_bp": [lo95 * 1e4, hi95 * 1e4], "ci_deflated_bp": [lo_adj * 1e4, hi_adj * 1e4],
                "passes_deflated": bool(len(r) >= 30 and r.mean() > 0 and lo_adj > 0)}
        print(f"\n  MR selected = {mr_best}: OOS {r.mean()*1e4:+.1f}bp  95%CI[{lo95*1e4:+.1f},{hi95*1e4:+.1f}]  "
              f"deflated({TRIALS_TOTAL}x)CI[{lo_adj*1e4:+.1f},{hi_adj*1e4:+.1f}]  "
              f"{'PASS' if conf['passes_deflated'] else 'no stand-alone edge'}")

    # ---- diversification: correlation of monthly OOS returns vs ts_mom ----
    ts_tr = run_spec(panel, "ts_mom")
    ts_m = monthly_returns(ts_tr)
    div = {}
    if mr_best:
        mr_m = monthly_returns(mr_trades)
        idx = ts_m.index.union(mr_m.index)
        a = ts_m.reindex(idx, fill_value=0.0); b = mr_m.reindex(idx, fill_value=0.0)
        corr = float(np.corrcoef(a.values, b.values)[0, 1])
        comb = 0.5 * (a + b)                          # equal-weight combined book
        div = {"corr_monthly": corr,
               "ts_mom": perf(a), "mr_best": perf(b), "combined_5050": perf(comb)}
        print(f"\n  monthly-return correlation (ts_mom vs {mr_best}) = {corr:+.2f}")
        print("    %-14s %8s %8s %10s %10s" % ("book", "Sharpe", "meanBP", "maxDD%", "worstBP"))
        for k in ("ts_mom", "mr_best", "combined_5050"):
            p = div[k]
            print("    %-14s %8.2f %+8.0f %10.1f %+10.0f" % (
                k, p.get("ann_sharpe", float("nan")), p.get("mean_monthly_bp", float("nan")),
                p.get("max_drawdown_pct", float("nan")), p.get("worst_month_bp", float("nan"))))
        improves = (div["combined_5050"]["ann_sharpe"] > div["ts_mom"]["ann_sharpe"] and
                    div["combined_5050"]["max_drawdown_pct"] > div["ts_mom"]["max_drawdown_pct"])
        div["combining_improves_risk_adjusted"] = bool(improves)

    verdict = ("USEFUL DIVERSIFIER: combined book improves Sharpe AND drawdown vs ts_mom alone."
               if div.get("combining_improves_risk_adjusted") else
               "NOT A CLEAR DIVERSIFIER on this sample: keep ts_mom as the single sleeve (valid finding).")
    print(f"\n  VERDICT: {verdict}")

    out = {"trials_total": TRIALS_TOTAL, "mr_family": mr_res, "mr_confirmation": conf,
           "diversification": div, "verdict": verdict, "seed": C.SEED}
    (C.ARTIFACTS_DIR / "p2_combine.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {C.ARTIFACTS_DIR / 'p2_combine.json'}")
    print("\nPHASE 2 DIVERSIFICATION COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
