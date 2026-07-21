"""
Phase 3 (addendum) - Chart pack for the risk-controlled book.

Six charts, one PNG, all read from the Phase-3 handoffs (plus one deterministic
recompute of the book to recover the paths p9 does not persist: exposure, sleeve
weights, per-trade P&L). The recompute is seeded and is ASSERTED byte-close to the
saved data/swing_equity.parquet -- a mini reconciliation, so the charts provably
show the same book the gates validated.

  1. Equity curves (log)     combo vs ts_mom-alone vs tom-alone vs buy&hold, OOS marked
  2. Drawdown (underwater)   combo vs buy&hold -- THE chart for this book
  3. Exposure                fraction of equity invested (the ~15% story)
  4. Sleeve risk-parity weights over time (ts_mom vs tom)
  5. Cross-sectional 99%/12d VaR through time (median + IQR band)
  6. Per-trade P&L histogram (wins/losses split at zero)

Colors follow the validated reference palette (categorical slots in fixed order,
color follows the ENTITY across charts; diverging blue/red only for P&L polarity).

ASCII-only prints. Run:  C:/Users/Admin/anaconda3/python.exe p9b_swing_charts.py
Writes: artifacts/p3_charts.png
"""
from __future__ import annotations

import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

import config as C
import p9_swing_portfolio as P9

warnings.filterwarnings("ignore")
np.random.seed(C.SEED)

# ---------------------------------------------------------------- palette (validated reference)
SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASE = "#c3c2b7"
# categorical slots, fixed order; color follows the ENTITY across every chart
COL_COMBO = "#2a78d6"     # slot 1 blue   -> the combined book
COL_TSMOM = "#1baf7a"     # slot 2 aqua   -> ts_mom sleeve
COL_TOM = "#eda100"       # slot 3 yellow -> tom sleeve
COL_BH = "#008300"        # slot 4 green  -> buy & hold
SEQ_MID = "#256abf"       # sequential blue (VaR line)
SEQ_LIGHT = "#9ec5f4"     # sequential blue light (VaR band)
DIV_POS = "#2a78d6"       # diverging: gains
DIV_NEG = "#e34948"       # diverging: losses


def style_ax(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=11, fontweight="bold", loc="left", pad=10)
    ax.grid(True, color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASE)
    ax.tick_params(colors=MUTED, labelsize=8)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_color(INK2)


def main() -> int:
    print("=" * 64)
    print("  PHASE 3 CHART PACK")
    print("=" * 64)

    # ---------------- recompute the book (deterministic) + reconcile vs saved ----------------
    panel = pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")
    risk = pd.read_parquet(C.DATA_DIR / "swing_risk_panel.parquet")
    saved = pd.read_parquet(C.DATA_DIR / "swing_equity.parquet")
    print("  rebuilding the book (seeded; reconciled against swing_equity.parquet)...")
    mkt = P9.Market(panel, risk)
    fixed = P9.budget_fn_factory(None, combined=False)
    standalone = {s: P9.run_book(mkt, [s], fixed, caps=P9.default_caps(), kill=P9.default_kill())
                  for s in P9.SLEEVES}
    weights = P9.sleeve_weights({s: standalone[s]["ret"] for s in P9.SLEEVES})
    fb = P9.budget_fn_factory(weights, combined=True)
    combo = P9.run_book(mkt, P9.SLEEVES, fb, caps=P9.default_caps(), kill=P9.default_kill())

    err = float(np.max(np.abs(combo["equity"] - saved["equity_combo"].to_numpy())))
    recon = err < 1e-6
    print(f"  [{'PASS' if recon else 'FAIL'}] recompute reconciles with saved equity "
          f"(max abs err = {err:.2e})")
    assert recon, "chart recompute does not match the gated book -- refusing to draw"

    dates = pd.to_datetime(mkt.dates)
    oos_start = dates[int(np.argmax(mkt.is_oos == "test"))]
    trades = pd.DataFrame(combo["trades"])
    exposure = combo["invested_path"] / np.maximum(combo["equity"], 1.0)

    # cross-sectional VaR through time (tradable snapshots only)
    tr = risk[risk["tradable"]].copy()
    tr["date"] = pd.to_datetime(tr["date"])
    vq = tr.groupby("date")["var_frac"].quantile([0.25, 0.50, 0.75]).unstack()

    # ---------------- figure ----------------
    fig, axes = plt.subplots(3, 2, figsize=(16, 15), dpi=130)
    fig.patch.set_facecolor(PAGE)
    fig.subplots_adjust(hspace=0.42, wspace=0.22, left=0.06, right=0.97, top=0.93, bottom=0.05)

    series = [("Combined book", saved["equity_combo"], COL_COMBO),
              ("ts_mom alone", saved["equity_ts_mom"], COL_TSMOM),
              ("tom alone", saved["equity_tom"], COL_TOM),
              ("Buy & hold", saved["equity_buyhold"], COL_BH)]

    # (1) equity curves, log scale
    ax = axes[0, 0]
    style_ax(ax, "Equity curves (log scale) - Rs.10 lakh start")
    for name, eq, col in series:
        ax.plot(dates, eq.to_numpy(), color=col, linewidth=2.0, label=name)
        ax.annotate(f" {name}", xy=(dates[-1], float(eq.iloc[-1])), color=col,
                    fontsize=8, fontweight="bold", va="center")
    ax.set_yscale("log")
    ax.axvline(oos_start, color=MUTED, linewidth=1.0, linestyle="--")
    ax.annotate(" OOS ->", xy=(oos_start, ax.get_ylim()[1]), color=MUTED, fontsize=8, va="top")
    ax.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=INK2)
    ax.margins(x=0.10)

    # (2) drawdown (underwater)
    ax = axes[0, 1]
    style_ax(ax, "Drawdown from peak (underwater) - the book's defining chart")
    for name, eq, col in ((series[0][0], series[0][1], COL_COMBO),
                          (series[3][0], series[3][1], COL_BH)):
        e = eq.to_numpy()
        dd = e / np.maximum.accumulate(e) - 1.0
        ax.plot(dates, dd * 100, color=col, linewidth=2.0, label=name)
        ax.fill_between(dates, dd * 100, 0, color=col, alpha=0.12, linewidth=0)
    ax.axvline(oos_start, color=MUTED, linewidth=1.0, linestyle="--")
    ax.set_ylabel("drawdown (%)", color=INK2, fontsize=9)
    ax.legend(loc="lower left", fontsize=8, frameon=False, labelcolor=INK2)

    # (3) exposure
    ax = axes[1, 0]
    style_ax(ax, "Invested fraction of equity (63d mean) - why the book lags a bull market")
    exp_smooth = pd.Series(exposure).rolling(63, min_periods=1).mean().to_numpy()
    ax.plot(dates, exp_smooth * 100, color=COL_COMBO, linewidth=2.0)
    ax.fill_between(dates, exp_smooth * 100, 0, color=COL_COMBO, alpha=0.12, linewidth=0)
    mean_exp = float(np.mean(exposure))
    ax.axhline(mean_exp * 100, color=MUTED, linewidth=1.0, linestyle=":")
    ax.annotate(f" mean {mean_exp*100:.0f}%", xy=(dates[0], mean_exp * 100),
                color=INK2, fontsize=8, va="bottom")
    ax.axvline(oos_start, color=MUTED, linewidth=1.0, linestyle="--")
    ax.set_ylabel("% of equity invested", color=INK2, fontsize=9)
    ax.set_ylim(0, 100)

    # (4) sleeve risk-parity weights
    ax = axes[1, 1]
    style_ax(ax, "Risk-parity sleeve weights (walk-forward, monthly re-estimate)")
    w_ts = weights["ts_mom"]
    ax.fill_between(dates, 0, w_ts * 100, color=COL_TSMOM, alpha=0.55, linewidth=0)
    ax.fill_between(dates, w_ts * 100, 100, color=COL_TOM, alpha=0.55, linewidth=0)
    ax.plot(dates, w_ts * 100, color=SURFACE, linewidth=2.0)   # 2px surface gap between fills
    mid = len(dates) // 2
    ax.annotate("ts_mom", xy=(dates[mid], float(w_ts[mid]) * 50), color=INK,
                fontsize=9, fontweight="bold", ha="center")
    ax.annotate("tom", xy=(dates[mid], float(w_ts[mid]) * 100 + (100 - float(w_ts[mid]) * 100) / 2),
                color=INK, fontsize=9, fontweight="bold", ha="center")
    ax.axvline(oos_start, color=MUTED, linewidth=1.0, linestyle="--")
    ax.set_ylabel("weight (%)", color=INK2, fontsize=9)
    ax.set_ylim(0, 100)

    # (5) cross-sectional VaR through time
    ax = axes[2, 0]
    style_ax(ax, "99% / 12-day VaR per share, across the 20 stocks (median + IQR)")
    ax.fill_between(vq.index, vq[0.25] * 100, vq[0.75] * 100, color=SEQ_LIGHT,
                    alpha=0.6, linewidth=0, label="interquartile range")
    ax.plot(vq.index, vq[0.50] * 100, color=SEQ_MID, linewidth=2.0, label="median stock")
    ax.axvline(oos_start, color=MUTED, linewidth=1.0, linestyle="--")
    ax.set_ylabel("VaR (% of share price)", color=INK2, fontsize=9)
    ax.legend(loc="upper right", fontsize=8, frameon=False, labelcolor=INK2)

    # (6) per-trade P&L histogram (diverging: polarity around zero)
    ax = axes[2, 1]
    style_ax(ax, "Per-trade P&L, combined book (n=%d, win rate %.0f%%)"
             % (len(trades), 100 * float((trades["pnl"] > 0).mean())))
    pnl = trades["pnl"].to_numpy()
    lo, hi = np.percentile(pnl, [1, 99])
    bins = np.linspace(lo, hi, 61)
    ax.hist(pnl[pnl <= 0], bins=bins, color=DIV_NEG, alpha=0.85, edgecolor=SURFACE,
            linewidth=0.4, label="losses")
    ax.hist(pnl[pnl > 0], bins=bins, color=DIV_POS, alpha=0.85, edgecolor=SURFACE,
            linewidth=0.4, label="wins")
    ax.axvline(0, color=BASE, linewidth=1.2)
    ax.axvline(float(np.mean(pnl)), color=INK2, linewidth=1.2, linestyle=":")
    ax.annotate(f" mean Rs.{np.mean(pnl):,.0f}", xy=(float(np.mean(pnl)), ax.get_ylim()[1]),
                color=INK2, fontsize=8, va="top")
    ax.set_xlabel("P&L per closed trade (Rs., 1st-99th pct)", color=INK2, fontsize=9)
    ax.legend(loc="upper right", fontsize=8, frameon=False, labelcolor=INK2)

    for row in axes:
        for ax in row:
            ax.xaxis.set_major_locator(mdates.YearLocator(5))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axes[2, 1].xaxis.set_major_locator(plt.MaxNLocator(6))
    axes[2, 1].xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x/1000:,.0f}k"))

    fig.suptitle("nse_paper_v1 - Phase 3 risk-controlled book\n"
                 "VaR-sized (GARCH-EVT), risk-parity ts_mom+tom, capped, kill-switched | "
                 "dashed line = OOS start", color=INK, fontsize=13, fontweight="bold")

    out = C.ARTIFACTS_DIR / "p3_charts.png"
    fig.savefig(out, facecolor=fig.get_facecolor())
    print(f"  wrote {out}")
    print("\nCHART PACK COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
