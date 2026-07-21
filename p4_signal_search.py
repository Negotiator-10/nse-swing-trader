"""
Phase 2 (iteration) - Honest multi-signal search.

THE DISCIPLINE (so we don't overfit the OOS set)
------------------------------------------------
Trying signals until one passes OUT OF SAMPLE is just overfitting the hold-out. Instead:
  1. Pre-specify a SMALL set of economically motivated specs (p2_swing_signal.SPECS).
  2. SELECT the best spec using IN-SAMPLE (train) trades ONLY.
  3. CONFIRM that single selected spec ONCE on OOS (test) trades.
  4. DEFLATE for the trial count: with T specs screened, the honest bar is a
     Bonferroni-adjusted CI (alpha/T), not a naive 95% CI.
All specs' OOS numbers are printed for TRANSPARENCY, but the VERDICT rests only on the
IS-selected spec's deflated OOS result. A no-edge outcome is a valid finding.

ASCII-only prints. Run:  C:/Users/Admin/anaconda3/python.exe p4_signal_search.py
Writes: artifacts/p2_signal_search.json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import config as C
from p2_swing_signal import SPECS
from p3_swing_backtest import (backtest_symbol, trade_metrics, _boot_ci,
                               buy_hold_baseline)

MIN_IS_TRADES = 50
MIN_OOS_TRADES = 30


def run_spec(panel: pd.DataFrame, spec: str) -> pd.DataFrame:
    tr = []
    for _, g in panel.groupby("symbol"):
        tr.extend(backtest_symbol(g, spec))
    df = pd.DataFrame(tr)
    if len(df):
        df["entry_date"] = pd.to_datetime(df["entry_date"])
    return df


def main() -> int:
    print("=" * 66)
    print("  PHASE 2 SIGNAL SEARCH - select on IS, confirm on OOS, deflate by trials")
    print("=" * 66)
    panel = pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")
    specs = list(SPECS.keys())
    T = len(specs)

    results = {}
    print("\n  per-spec metrics (IS selects; OOS shown for transparency):")
    print("    %-13s %6s %10s | %6s %10s %10s" %
          ("spec", "n_IS", "IS_bp", "n_OOS", "OOS_bp", "OOS_CI_LB"))
    for spec in specs:
        tr = run_spec(panel, spec)
        is_m = trade_metrics(tr[tr.split == "train"]) if len(tr) else {"n": 0}
        oos_m = trade_metrics(tr[tr.split == "test"]) if len(tr) else {"n": 0}
        results[spec] = {"is": is_m, "oos": oos_m, "n_total": int(len(tr))}
        print("    %-13s %6d %+9.1f | %6d %+9.1f %+9.1f" % (
            spec, is_m.get("n", 0), is_m.get("mean_net_ret_bp", float("nan")),
            oos_m.get("n", 0), oos_m.get("mean_net_ret_bp", float("nan")),
            oos_m.get("ci95_mean_bp", [float("nan")])[0]))

    # ---- SELECT on IS only (best IS mean net return, with enough IS trades) ----
    eligible = [s for s in specs if results[s]["is"].get("n", 0) >= MIN_IS_TRADES]
    selected = max(eligible, key=lambda s: results[s]["is"]["mean_net_ret_bp"]) if eligible else None
    print(f"\n  IS-selected spec = {selected}  "
          f"(IS mean {results[selected]['is']['mean_net_ret_bp']:+.1f}bp)" if selected else
          "\n  no spec had enough IS trades")

    # ---- CONFIRM selected on OOS with a DEFLATED (Bonferroni) CI ----
    verdict = "NO-EDGE"
    conf = {}
    if selected:
        tr = run_spec(panel, selected)
        oos = tr[tr.split == "test"]
        r = oos["net_ret"].to_numpy()
        alpha_adj = 0.05 / T
        lo95, hi95 = _boot_ci(r, alpha=0.05)
        lo_adj, hi_adj = _boot_ci(r, alpha=alpha_adj)
        # naive p (one-sided): fraction of bootstrap means <= 0
        rng = np.random.default_rng(C.SEED)
        bmeans = r[rng.integers(0, len(r), size=(8000, len(r)))].mean(axis=1)
        p_one_sided = float((bmeans <= 0).mean())
        passes = (len(r) >= MIN_OOS_TRADES and r.mean() > 0 and lo_adj > 0)
        verdict = "EDGE (deflated)" if passes else "NO-EDGE"
        conf = {"selected": selected, "n_oos": int(len(r)), "trials": T,
                "oos_mean_bp": float(r.mean() * 1e4), "ci95_bp": [lo95 * 1e4, hi95 * 1e4],
                "alpha_bonferroni": alpha_adj, "ci_deflated_bp": [lo_adj * 1e4, hi_adj * 1e4],
                "p_one_sided": p_one_sided, "passes_deflated": bool(passes)}
        print(f"\n  OOS confirm ({selected}): mean={r.mean()*1e4:+.1f}bp  "
              f"95%CI=[{lo95*1e4:+.1f},{hi95*1e4:+.1f}]  "
              f"deflated({T}x)CI=[{lo_adj*1e4:+.1f},{hi_adj*1e4:+.1f}]  p1={p_one_sided:.3f}")

    bh = buy_hold_baseline(panel, "test")
    print(f"  context: OOS buy&hold avg/stock = {bh['mean_net_ret_pct']:+.1f}%")

    print(f"\n  VERDICT: {verdict} on the pre-specified swing signal set "
          f"({'ship this signal for the paper plumbing test' if verdict.startswith('EDGE') else 'none clears the deflated OOS bar'})")

    out = {
        "protocol": "select-on-IS, confirm-on-OOS, Bonferroni-deflate by n_specs",
        "n_specs": T, "specs": specs, "per_spec": results,
        "selected": selected, "confirmation": conf,
        "buy_hold_oos": bh, "verdict": verdict, "seed": C.SEED,
    }
    (C.ARTIFACTS_DIR / "p2_signal_search.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {C.ARTIFACTS_DIR / 'p2_signal_search.json'}")
    print("\nPHASE 2 SIGNAL SEARCH COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
