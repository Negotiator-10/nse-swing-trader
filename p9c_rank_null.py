"""
Slot-allocation NULL TEST (DECISIONS #019 follow-up). Read-only; writes one JSON report.

THE QUESTION
------------
~37 candidates qualify on a typical day and 6 slots exist, so a rule must choose. Two rules
have been measured on the CLEANED tape:

    alphabetical (accidental, no possible edge)  OOS Sharpe 0.72 / CAGR 6.43%
    cross-sectional score (#018)                 OOS Sharpe 0.39 / CAGR 2.81%

Ranking LOST to an arbitrary rule. The tempting conclusion -- "alphabetical is better" --
is almost certainly wrong, because alphabetical cannot contain information about future
returns. The likely truth is that slot choice is HIGH VARIANCE and neither rule has
demonstrated skill.

This test settles it. Run the same book many times with a RANDOM slot order and build the
null distribution of outcomes. Then ask where each real rule falls in it:

  * if both sit inside the null band -> slot choice is noise; #018 is unsupported, and so
    is any claim that alphabetical is good.
  * if the score sits in the RIGHT tail -> ranking has genuine skill and its single bad
    draw on this tape was luck.
  * if the score sits in the LEFT tail -> ranking is actively harmful, which would be a
    real and surprising finding worth chasing.

Everything else -- signals, sizing, caps, kill switch, costs -- is held identical. The ONLY
thing varying is which candidate gets served first when slots are scarce.

ASCII-only prints.  Run:  C:/Users/Admin/anaconda3/python.exe p9c_rank_null.py
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

import config as C
import p9_swing_portfolio as P9

warnings.filterwarnings("ignore")

N_DRAWS = 25          # random orderings to sample


def _book(mkt, weights, key_fn=None):
    """Run one book. key_fn=None -> whatever config.ENTRY_RANKING says."""
    if key_fn is None:
        return P9.run_book(mkt, P9.SLEEVES, P9.budget_fn_factory(weights, combined=True),
                           caps=P9.default_caps(), kill=P9.default_kill())
    orig = P9._rank_key
    P9._rank_key = key_fn
    try:
        return P9.run_book(mkt, P9.SLEEVES, P9.budget_fn_factory(weights, combined=True),
                           caps=P9.default_caps(), kill=P9.default_kill())
    finally:
        P9._rank_key = orig


def main() -> int:
    print("=" * 64)
    print("  SLOT-ALLOCATION NULL TEST -- is picking 6 of ~37 skill or noise?")
    print("=" * 64)

    src = (C.CLEAN_TAPE_PATH if (getattr(C, "USE_CLEAN_TAPE", False)
                                 and C.CLEAN_TAPE_PATH.exists())
           else C.DATA_DIR / "swing_daily.parquet")
    panel = pd.read_parquet(src)
    risk = pd.read_parquet(C.DATA_DIR / "swing_risk_panel.parquet")
    print(f"  tape: {src.name}   {len(panel):,} bars, {len(risk):,} risk snapshots")

    mkt = P9.Market(panel, risk)
    oos = mkt.is_oos == "test"
    weights = {"ts_mom": np.ones(len(mkt.dates))}

    def metrics(res):
        m = P9.metrics(res["equity"], res["ret"], mkt.dates, oos)
        return m["ann_sharpe"], m["cagr"], m["max_drawdown"]

    # ---- the two real rules ----
    prev = C.ENTRY_RANKING
    try:
        C.ENTRY_RANKING = "alpha"
        s_alpha = metrics(_book(mkt, weights))
        C.ENTRY_RANKING = "xs_score"
        s_score = metrics(_book(mkt, weights))
    finally:
        C.ENTRY_RANKING = prev
    print(f"\n  alphabetical      Sharpe {s_alpha[0]:+.3f}  CAGR {s_alpha[1]*100:6.2f}%  DD {s_alpha[2]*100:6.1f}%")
    print(f"  cross-sectional   Sharpe {s_score[0]:+.3f}  CAGR {s_score[1]*100:6.2f}%  DD {s_score[2]*100:6.1f}%")

    # ---- the null: random slot order ----
    print(f"\n  building the null distribution ({N_DRAWS} random orderings)...")
    C.ENTRY_RANKING = "xs_score"          # engages the sort; the key itself is randomised
    rows = []
    try:
        for d in range(N_DRAWS):
            # One fixed random priority per SYMBOL, drawn once per draw. Within a draw the
            # ordering is arbitrary but STABLE -- exactly like alphabetical, minus any
            # alphabetical structure. Drawing fresh numbers per call would instead shuffle
            # the order every day, which is a different (and easier) experiment.
            prio = np.random.default_rng(C.SEED + 1000 * d).random(len(mkt.syms))

            def rk(pend, j, _p=prio):
                return (float(_p[j]), j)

            sh, cg, dd = metrics(_book(mkt, weights, key_fn=rk))
            rows.append({"draw": d, "sharpe": sh, "cagr": cg, "maxdd": dd})
            print(f"    draw {d + 1:2d}/{N_DRAWS}  Sharpe {sh:+.3f}  CAGR {cg * 100:6.2f}%")
    finally:
        C.ENTRY_RANKING = prev

    sh = np.array([r["sharpe"] for r in rows])
    cg = np.array([r["cagr"] for r in rows])

    def pctile(v, arr):
        return float((arr < v).mean() * 100)

    rep = {
        "n_draws": N_DRAWS, "tape": src.name,
        "alphabetical": {"sharpe": s_alpha[0], "cagr": s_alpha[1], "maxdd": s_alpha[2],
                         "sharpe_pctile_in_null": pctile(s_alpha[0], sh)},
        "cross_sectional": {"sharpe": s_score[0], "cagr": s_score[1], "maxdd": s_score[2],
                            "sharpe_pctile_in_null": pctile(s_score[0], sh)},
        "null": {"sharpe_mean": float(sh.mean()), "sharpe_sd": float(sh.std()),
                 "sharpe_min": float(sh.min()), "sharpe_max": float(sh.max()),
                 "cagr_mean": float(cg.mean()), "cagr_sd": float(cg.std()),
                 "cagr_min": float(cg.min()), "cagr_max": float(cg.max())},
        "draws": rows, "seed": C.SEED,
    }

    print("\n" + "=" * 64)
    print("  RESULT")
    print("=" * 64)
    print(f"  null (random order):  Sharpe mean {sh.mean():+.3f}  sd {sh.std():.3f}  "
          f"range [{sh.min():+.3f}, {sh.max():+.3f}]")
    print(f"                        CAGR   mean {cg.mean()*100:5.2f}%  sd {cg.std()*100:.2f}%  "
          f"range [{cg.min()*100:5.2f}%, {cg.max()*100:5.2f}%]")
    print(f"\n  alphabetical    Sharpe {s_alpha[0]:+.3f} -> {rep['alphabetical']['sharpe_pctile_in_null']:5.1f}th percentile of the null")
    print(f"  cross-sectional Sharpe {s_score[0]:+.3f} -> {rep['cross_sectional']['sharpe_pctile_in_null']:5.1f}th percentile of the null")

    spread = sh.max() - sh.min()
    print(f"\n  Sharpe spread from slot ORDER ALONE: {spread:.3f}")
    print("  (everything else -- signal, sizing, caps, costs -- was identical in every run)")

    out = C.ARTIFACTS_DIR / "p9c_rank_null.json"
    out.write_text(json.dumps(rep, indent=1, default=str))
    print(f"\n  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
