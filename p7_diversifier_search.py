"""
Phase 2 (diversifier search) - bounded, pre-registered hunt for a REAL diversifier.

A diversifier must clear THREE bars, not one:
  (1) proven stand-alone OOS edge, DEFLATED by the cumulative trial count (len(SPECS));
  (2) low correlation with ts_mom's monthly returns (else it is the same bet);
  (3) the 50/50 combined book must IMPROVE risk-adjusted return (Sharpe up AND drawdown
      shallower) vs ts_mom alone.
Candidates are pre-specified (p2_swing_signal.DIVERSIFIER_CANDIDATES) so this is not
open-ended fishing. Finding none is a valid outcome -> the real diversifier is a
market-neutral long/short sleeve (a separate, bigger build).

ASCII-only. Run:  C:/Users/Admin/anaconda3/python.exe p7_diversifier_search.py
Writes: artifacts/p2_diversifier.json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import config as C
from p2_swing_signal import SPECS, DIVERSIFIER_CANDIDATES
from p3_swing_backtest import trade_metrics, _boot_ci
from p6_swing_combine import run_spec, monthly_returns, perf

TRIALS_TOTAL = len(SPECS)


def evaluate(panel, spec, ts_month) -> dict:
    tr = run_spec(panel, spec)
    oos = tr[tr.split == "test"]
    r = oos["net_ret"].to_numpy()
    if len(r) < 30:
        return {"spec": spec, "n_oos": int(len(r)), "insufficient": True}
    lo95, hi95 = _boot_ci(r, alpha=0.05)
    lo_adj, hi_adj = _boot_ci(r, alpha=0.05 / TRIALS_TOTAL)
    mm = monthly_returns(tr)
    idx = ts_month.index.union(mm.index)
    a = ts_month.reindex(idx, fill_value=0.0); b = mm.reindex(idx, fill_value=0.0)
    corr = float(np.corrcoef(a.values, b.values)[0, 1])
    comb = 0.5 * (a + b)
    pa, pc = perf(a), perf(comb)
    significant = bool(r.mean() > 0 and lo_adj > 0)
    uncorrelated = bool(abs(corr) < 0.35)
    improves = bool(pc["ann_sharpe"] > pa["ann_sharpe"] and pc["max_drawdown_pct"] > pa["max_drawdown_pct"])
    return {"spec": spec, "n_oos": int(len(r)), "oos_mean_bp": float(r.mean() * 1e4),
            "ci95_bp": [lo95 * 1e4, hi95 * 1e4], "ci_deflated_bp": [lo_adj * 1e4, hi_adj * 1e4],
            "corr_to_ts_mom": corr, "combined_sharpe": pc["ann_sharpe"],
            "combined_maxdd_pct": pc["max_drawdown_pct"], "ts_sharpe": pa["ann_sharpe"],
            "ts_maxdd_pct": pa["max_drawdown_pct"],
            "significant_deflated": significant, "uncorrelated": uncorrelated,
            "combining_improves": improves,
            "is_real_diversifier": bool(significant and uncorrelated and improves)}


def main() -> int:
    print("=" * 68)
    print("  PHASE 2 DIVERSIFIER SEARCH - significant + uncorrelated + improves (deflate x%d)" % TRIALS_TOTAL)
    print("=" * 68)
    panel = pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")
    ts_month = monthly_returns(run_spec(panel, "ts_mom"))

    print("\n  %-12s %6s %9s %11s %7s %8s %8s  %s" %
          ("spec", "n_OOS", "OOS_bp", "deflCI_LB", "corr", "combSh", "combDD%", "verdict"))
    results = {}
    winner = None
    for spec in DIVERSIFIER_CANDIDATES:
        e = evaluate(panel, spec, ts_month)
        results[spec] = e
        if e.get("insufficient"):
            print("  %-12s %6d  (insufficient OOS trades)" % (spec, e["n_oos"]))
            continue
        v = ("REAL DIVERSIFIER" if e["is_real_diversifier"] else
             ("sig?%s corr?%s impr?%s" % (e["significant_deflated"], e["uncorrelated"], e["combining_improves"])))
        print("  %-12s %6d %+8.1f %+10.1f %+7.2f %8.2f %8.1f  %s" % (
            spec, e["n_oos"], e["oos_mean_bp"], e["ci_deflated_bp"][0], e["corr_to_ts_mom"],
            e["combined_sharpe"], e["combined_maxdd_pct"], v))
        if e["is_real_diversifier"] and (winner is None or
                                         e["combined_sharpe"] > results[winner]["combined_sharpe"]):
            winner = spec

    print(f"\n  ts_mom alone: Sharpe={perf(ts_month)['ann_sharpe']:.2f}  maxDD={perf(ts_month)['max_drawdown_pct']:.1f}%")
    if winner:
        verdict = f"FOUND a real diversifier: {winner} (significant, uncorrelated, improves the book)."
    else:
        verdict = ("NO proven long-only diversifier in the pre-registered set. Honest conclusion: "
                   "the genuine diversifier is a MARKET-NEUTRAL long/short sleeve (strips the market "
                   "beta ts_mom carries) -- a separate, larger build. Keep ts_mom single-sleeve meanwhile.")
    print(f"\n  VERDICT: {verdict}")

    out = {"trials_total": TRIALS_TOTAL, "candidates": DIVERSIFIER_CANDIDATES,
           "results": results, "winner": winner, "verdict": verdict, "seed": C.SEED}
    (C.ARTIFACTS_DIR / "p2_diversifier.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {C.ARTIFACTS_DIR / 'p2_diversifier.json'}")
    print("\nPHASE 2 DIVERSIFIER SEARCH COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
