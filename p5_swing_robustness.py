"""
Phase 2 (hardening) - Robustness suite for the selected ts_mom swing signal.

A signal that clears one OOS pass can still be a knife-edge fit, a cost mirage, or pure
"be long in a bull" beta. Four honest stress tests (all OUT OF SAMPLE):

  R1 parameter sensitivity : nudge each param one-at-a-time; is the edge a plateau or a
                             lone peak? (a robust edge survives a neighborhood of params)
  R2 cost stress           : raise slippage + the whole cost stack; find the break-even.
  R3 random-entry null      : the key test -- does ts_mom's ENTRY timing beat RANDOM entry
                             with the SAME exit machinery + trade count? (Monte Carlo)
  R4 out-of-universe        : run the FROZEN signal on stocks it was never selected on
                             (NIFTY Next-50 names) -- the best free survivorship proxy.

None of these tunes anything on OOS; they characterize the edge already found. ASCII-only.
Run:  C:/Users/Admin/anaconda3/python.exe p5_swing_robustness.py
Writes: artifacts/p2_robustness.json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import config as C
from p2_swing_signal import atr as atr_fn
from p3_swing_backtest import event_loop, trade_metrics, _boot_ci

BASE = dict(ma_long=200, ret_lb=126, ma_mid=50, stop_mult=3.5, hold_max=25)


# ---------------------------------------------------------------- parametrized ts_mom
def ts_mom_frame(df, ma_long, ret_lb, ma_mid):
    d = df.sort_values("date").reset_index(drop=True).copy()
    d["atr"] = atr_fn(d)
    mal = d["close"].rolling(ma_long, min_periods=ma_long).mean()
    mam = d["close"].rolling(ma_mid, min_periods=ma_mid).mean()
    r = d["close"] / d["close"].shift(ret_lb) - 1.0
    d["entry"] = ((d["close"] > mal) & (r > 0) & (d["close"] > mam) & (d["volume"] > 0)).fillna(False)
    d["exit_trend"] = (d["close"] < mam).fillna(False)
    return d


def run_config(panel, ma_long, ret_lb, ma_mid, stop_mult, hold_max) -> pd.DataFrame:
    warm = max(ma_long, ret_lb) + 5
    tr = []
    for _, g in panel.groupby("symbol"):
        d = ts_mom_frame(g, ma_long, ret_lb, ma_mid)
        tr += event_loop(d["symbol"].iloc[0], d["date"].to_numpy(), d["split"].to_numpy(),
                         d["open"].to_numpy(), d["high"].to_numpy(), d["low"].to_numpy(),
                         d["close"].to_numpy(), d["atr"].to_numpy(),
                         d["entry"].to_numpy(), d["exit_trend"].to_numpy(),
                         stop_mult, hold_max, warm)
    df = pd.DataFrame(tr)
    return df


def oos_mean_bp(trades) -> float:
    t = trades[trades.split == "test"]
    return float(t["net_ret"].mean() * 1e4) if len(t) else float("nan")


# ---------------------------------------------------------------- R1 param sensitivity
def r1_sensitivity(panel) -> dict:
    print("\n=== R1 parameter sensitivity (OOS mean net bp, one-at-a-time) ===")
    grid = {"ma_long": [150, 200, 250], "ret_lb": [63, 126, 189, 252],
            "ma_mid": [40, 50, 60], "stop_mult": [2.5, 3.5, 4.5], "hold_max": [15, 25, 35]}
    out = {}
    n_pos = n_tot = 0
    for k, vals in grid.items():
        row = {}
        for v in vals:
            cfg = dict(BASE); cfg[k] = v
            m = oos_mean_bp(run_config(panel, **cfg))
            row[str(v)] = round(m, 1)
            n_tot += 1; n_pos += int(m > 0)
        out[k] = row
        print(f"  {k:10s} " + "  ".join(f"{vv}:{row[str(vv)]:+.0f}" for vv in vals))
    out["frac_positive"] = n_pos / n_tot
    print(f"  -> {n_pos}/{n_tot} one-at-a-time configs positive OOS")
    return out


# ---------------------------------------------------------------- R2 cost stress
def r2_cost_stress(base_trades) -> dict:
    print("\n=== R2 cost stress (OOS mean net bp) ===")
    oos = base_trades[base_trades.split == "test"]
    gross = oos["gross_ret"].to_numpy()
    cost = oos["cost_frac"].to_numpy()
    out = {}
    for cm in (1.0, 1.5, 2.0):
        row = {}
        for extra_slip_bp in (0, 5, 10, 15, 20):
            net = gross - cm * cost - 2 * extra_slip_bp * 1e-4     # extra slip both sides
            row[f"+{extra_slip_bp}bp_slip"] = round(float(net.mean()) * 1e4, 1)
        out[f"cost_x{cm}"] = row
        print(f"  cost x{cm}: " + "  ".join(f"{k}:{v:+.0f}" for k, v in row.items()))
    # crude break-even extra slippage at cost x1
    be = None
    for s in range(0, 60):
        if (gross - cost - 2 * s * 1e-4).mean() <= 0:
            be = s; break
    out["breakeven_extra_slip_bp_at_cost_x1"] = be
    print(f"  -> break-even at ~+{be}bp extra slippage/side (cost x1)")
    return out


# ---------------------------------------------------------------- R3 random-entry null
def r3_random_null(panel, base_oos_bp, n_mc=200) -> dict:
    print(f"\n=== R3 random-entry null (MC={n_mc}) ===")
    # precompute per-symbol arrays + ts_mom entry COUNT (to match trade frequency)
    syms = []
    for _, g in panel.groupby("symbol"):
        d = ts_mom_frame(g, BASE["ma_long"], BASE["ret_lb"], BASE["ma_mid"])
        warm = max(BASE["ma_long"], BASE["ret_lb"]) + 5
        arr = dict(sym=d["symbol"].iloc[0], dates=d["date"].to_numpy(), split=d["split"].to_numpy(),
                   o=d["open"].to_numpy(), h=d["high"].to_numpy(), l=d["low"].to_numpy(),
                   c=d["close"].to_numpy(), at=d["atr"].to_numpy(),
                   exit_trend=d["exit_trend"].to_numpy(), warm=warm,
                   k_entry=int(d["entry"].to_numpy()[warm:].sum()),
                   tradable=np.where(d["volume"].to_numpy() > 0)[0])
        syms.append(arr)
    rng = np.random.default_rng(C.SEED)
    null_means = []
    for _ in range(n_mc):
        nets = []
        for a in syms:
            elig = a["tradable"][a["tradable"] >= a["warm"]]
            if len(elig) == 0 or a["k_entry"] == 0:
                continue
            pick = rng.choice(elig, size=min(a["k_entry"], len(elig)), replace=False)
            ent = np.zeros(len(a["dates"]), dtype=bool); ent[pick] = True
            tr = event_loop(a["sym"], a["dates"], a["split"], a["o"], a["h"], a["l"], a["c"],
                            a["at"], ent, a["exit_trend"], BASE["stop_mult"], BASE["hold_max"], a["warm"])
            nets += [t["net_ret"] for t in tr if t["split"] == "test"]
        if nets:
            null_means.append(float(np.mean(nets)) * 1e4)
    null_means = np.array(null_means)
    p = float((null_means >= base_oos_bp).mean())
    out = {"base_oos_bp": base_oos_bp, "null_mean_bp": float(null_means.mean()),
           "null_p95_bp": float(np.percentile(null_means, 95)),
           "null_p_ge_base": p, "n_mc": int(len(null_means))}
    print(f"  ts_mom OOS={base_oos_bp:+.1f}bp  null mean={out['null_mean_bp']:+.1f}bp  "
          f"null p95={out['null_p95_bp']:+.1f}bp  p(null>=ts_mom)={p:.3f}")
    print(f"  -> {'ENTRY TIMING adds value (beats random)' if p < 0.05 else 'entry timing NOT distinguishable from random'}")
    return out


# ---------------------------------------------------------------- R4 out-of-universe
NEXT50 = ["DLF", "PIDILITIND", "HAVELLS", "GODREJCP", "SIEMENS", "DABUR", "BANKBARODA",
          "GAIL", "AMBUJACEM", "TVSMOTOR", "VEDL", "ADANIGREEN"]


def r4_out_of_universe() -> dict:
    print("\n=== R4 out-of-universe (frozen ts_mom on NIFTY Next-50 names) ===")
    from p1_swing_data import fetch_daily
    frames = []
    for sym in NEXT50:
        try:
            d = fetch_daily(sym)
        except Exception:
            continue
        if len(d) < C.SWING_MIN_DAYS:
            continue
        n_test = max(1, int(round(len(d) * C.SWING_OOS_FRAC)))
        d["split"] = "train"; d.iloc[-n_test:, d.columns.get_loc("split")] = "test"
        frames.append(d)
    if not frames:
        return {"error": "no out-of-universe data"}
    panel = pd.concat(frames, ignore_index=True)
    tr = run_config(panel, **BASE)
    oos = tr[tr.split == "test"]
    r = oos["net_ret"].to_numpy()
    lo, hi = _boot_ci(r, alpha=0.05)
    per = oos.groupby("symbol")["net_ret"].mean()
    out = {"symbols": sorted(panel["symbol"].unique().tolist()), "n_oos_trades": int(len(r)),
           "oos_mean_bp": float(r.mean() * 1e4), "ci95_bp": [lo * 1e4, hi * 1e4],
           "breadth_pos": f"{int((per > 0).sum())}/{len(per)}"}
    print(f"  symbols={len(per)}  OOS trades={len(r)}  mean={r.mean()*1e4:+.1f}bp  "
          f"CI=[{lo*1e4:+.1f},{hi*1e4:+.1f}]  breadth={out['breadth_pos']}")
    print(f"  -> {'edge GENERALIZES beyond the selected universe' if lo > 0 else 'edge WEAKER/absent out-of-universe (survivorship suspected)'}")
    return out


# ---------------------------------------------------------------- main
def main() -> int:
    print("=" * 66)
    print("  PHASE 2 HARDENING - ts_mom robustness (R1 params / R2 costs / R3 null / R4 OOU)")
    print("=" * 66)
    panel = pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")
    base = run_config(panel, **BASE)
    base_bp = oos_mean_bp(base)
    print(f"  baseline ts_mom OOS mean = {base_bp:+.1f} bp/trade "
          f"(n_oos={int((base.split=='test').sum())})")

    r1 = r1_sensitivity(panel)
    r2 = r2_cost_stress(base)
    r4 = r4_out_of_universe()
    r3 = r3_random_null(panel, base_bp)

    # overall honest read
    robust = (r1["frac_positive"] >= 0.8 and r3["null_p_ge_base"] < 0.05
              and r2["breakeven_extra_slip_bp_at_cost_x1"] is not None
              and r2["breakeven_extra_slip_bp_at_cost_x1"] >= 5
              and isinstance(r4, dict) and r4.get("ci95_bp", [0])[0] > 0)
    verdict = ("ROBUST: edge survives param neighborhood, cost stress, beats random entry, "
               "and GENERALIZES out-of-universe." if robust else
               "PARTIALLY ROBUST / see components: at least one stress weakens the edge -- "
               "documented, not hidden.")
    print(f"\n  VERDICT: {verdict}")

    out = {"baseline_oos_bp": base_bp, "R1_param_sensitivity": r1, "R2_cost_stress": r2,
           "R3_random_null": r3, "R4_out_of_universe": r4, "robust": bool(robust),
           "verdict": verdict, "seed": C.SEED}
    (C.ARTIFACTS_DIR / "p2_robustness.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\n  wrote {C.ARTIFACTS_DIR / 'p2_robustness.json'}")
    print("\nPHASE 2 HARDENING COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
