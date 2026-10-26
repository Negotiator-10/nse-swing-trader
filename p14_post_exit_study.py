"""
Post-exit study: after we SOLD, what did the stock do? (user request 2026-08-30)

THE QUESTION
------------
For every closed trade, track the price for up to 10 trading days AFTER the exit.

    price FELL after we sold  -> the exit saved money
    price ROSE after we sold  -> we sold too early and left money behind

Run on BOTH sets:
  * the LIVE ledger (12 trades) -- what actually happened to this book
  * the BACKTEST book re-run at the current config (~2,400 trades) -- the only sample
    large enough for an answer to mean anything

WHY THE MARKET COLUMN MATTERS
-----------------------------
A stock that rises 3% after we sold, in a week the whole market rose 3%, tells us nothing
about the exit rule. Every post-exit return is therefore also reported RELATIVE to an
equal-weighted index of the same 95 names over the identical window. The relative number is
the one that carries information; the raw number is what it felt like.

TWO TRAPS, NAMED SO THEY ARE NOT WALKED INTO
--------------------------------------------
1. CENSORING. The tape ends 2026-08-27, so a trade closed on the 27th has ZERO days of
   follow-up and one closed on the 20th has about five. Every row reports the days actually
   available, and summaries are computed only over trades with the full window.
2. HINDSIGHT. Judging an exit rule by what happened next is a cousin of the "never rank by
   unrealized P&L" trap (DECISIONS #005). A CORRECT rule still looks wrong on many individual
   trades -- that is what a 33% win rate means. This study can generate a hypothesis; only a
   pre-registered change tested against the +/-0.16 Sharpe noise band (DECISIONS #019) can
   confirm one.

ASCII-only prints. Read-only.
Run:  C:/Users/Admin/anaconda3/python.exe p14_post_exit_study.py
Writes: artifacts/p14_post_exit.json
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

import config as C
import p9_swing_portfolio as P9

warnings.filterwarnings("ignore")

HORIZONS = [1, 3, 5, 10]
FULL_H = 10


# ---------------------------------------------------------------- price universe
def price_panel() -> pd.DataFrame:
    """One continuous close series per symbol: cleaned research tape + frozen live tape."""
    src = (C.CLEAN_TAPE_PATH if (getattr(C, "USE_CLEAN_TAPE", False)
                                 and C.CLEAN_TAPE_PATH.exists())
           else C.DATA_DIR / "swing_daily.parquet")
    res = pd.read_parquet(src)[["date", "symbol", "close"]]
    out = [res]
    if C.PAPER_LIVE_TAPE_PATH.exists():
        out.append(pd.read_parquet(C.PAPER_LIVE_TAPE_PATH)[["date", "symbol", "close"]])
    p = pd.concat(out, ignore_index=True)
    p["date"] = pd.to_datetime(p["date"])
    return (p.drop_duplicates(["symbol", "date"], keep="last")
             .sort_values(["symbol", "date"]).reset_index(drop=True))


def market_index(panel: pd.DataFrame) -> pd.Series:
    """Equal-weighted index of the whole universe: chain the cross-sectional mean of daily
    returns. This is the benchmark each post-exit move is measured against."""
    wide = panel.pivot_table(index="date", columns="symbol", values="close")
    rets = wide.pct_change().mean(axis=1)          # equal weight across whatever traded
    return (1.0 + rets.fillna(0.0)).cumprod()


# ---------------------------------------------------------------- the study
def follow(trades: pd.DataFrame, panel: pd.DataFrame, idx: pd.Series) -> pd.DataFrame:
    """For each closed trade, the stock's move over the next N trading days vs the market."""
    rows = []
    by_sym = {s: g.reset_index(drop=True) for s, g in panel.groupby("symbol")}
    idx_dates = idx.index
    for t in trades.itertuples(index=False):
        g = by_sym.get(t.symbol)
        if g is None:
            continue
        ex = pd.Timestamp(t.exit_date)
        pos = g.index[g["date"] == ex]
        if not len(pos):
            after = g.index[g["date"] > ex]
            if not len(after):
                continue
            pos = [after[0]]
        i = int(pos[0])
        avail = len(g) - 1 - i
        r = {"symbol": t.symbol, "exit_date": str(ex.date()), "reason": t.reason,
             "exit_px": float(t.exit_px), "shares": int(t.shares),
             "trade_pnl": float(t.pnl), "days_available": int(avail)}
        mi = idx_dates.get_indexer([ex], method="nearest")[0]
        for h in HORIZONS:
            if avail >= h:
                px = float(g["close"].iloc[i + h])
                stock = px / float(t.exit_px) - 1.0
                mkt = float(idx.iloc[min(mi + h, len(idx) - 1)] / idx.iloc[mi] - 1.0)
                r[f"ret_{h}d"] = stock
                r[f"mkt_{h}d"] = mkt
                r[f"rel_{h}d"] = stock - mkt
                r[f"left_behind_{h}d"] = float(t.shares) * (px - float(t.exit_px))
            else:
                for k in ("ret", "mkt", "rel", "left_behind"):
                    r[f"{k}_{h}d"] = None
        rows.append(r)
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame, label: str) -> dict:
    """Summary over trades with the FULL window only -- censored rows are excluded, and the
    count that survived is reported so the exclusion is visible."""
    full = df[df["days_available"] >= FULL_H]
    out = {"label": label, "n_trades": int(len(df)), "n_with_full_window": int(len(full))}
    if not len(full):
        out["note"] = "no trade has the full window yet"
        return out
    out["overall"] = {
        "mean_ret_10d": float(full["ret_10d"].mean()),
        "median_ret_10d": float(full["ret_10d"].median()),
        "mean_rel_10d": float(full["rel_10d"].mean()),
        "pct_rose_after": float((full["ret_10d"] > 0).mean() * 100),
        "pct_beat_market_after": float((full["rel_10d"] > 0).mean() * 100),
        "total_left_behind": float(full["left_behind_10d"].sum()),
    }
    by = {}
    for reason, g in full.groupby("reason"):
        by[str(reason)] = {
            "n": int(len(g)),
            "mean_ret_10d": float(g["ret_10d"].mean()),
            "mean_rel_10d": float(g["rel_10d"].mean()),
            "pct_rose_after": float((g["ret_10d"] > 0).mean() * 100),
            "total_left_behind": float(g["left_behind_10d"].sum()),
        }
    out["by_reason"] = by
    return out


def main() -> int:
    print("=" * 64)
    print("  POST-EXIT STUDY - what happened after we sold")
    print("=" * 64)

    panel = price_panel()
    idx = market_index(panel)
    print(f"  price panel: {len(panel):,} bars, {panel['symbol'].nunique()} symbols, "
          f"through {str(panel['date'].max())[:10]}")

    # ---- LIVE ----
    live = pd.read_parquet(C.PAPER_LEDGER_PATH)
    live["exit_date"] = pd.to_datetime(live["exit_date"])
    live_f = follow(live, panel, idx)
    live_s = summarise(live_f, "LIVE ledger")

    # ---- BACKTEST (book re-run at the current config) ----
    print("  re-running the portfolio book for the backtest trade set...")
    src = (C.CLEAN_TAPE_PATH if (getattr(C, "USE_CLEAN_TAPE", False)
                                 and C.CLEAN_TAPE_PATH.exists())
           else C.DATA_DIR / "swing_daily.parquet")
    bt_panel = pd.read_parquet(src)
    risk = pd.read_parquet(C.DATA_DIR / "swing_risk_panel.parquet")
    mkt = P9.Market(bt_panel, risk)
    w = {s: np.ones(len(mkt.dates)) for s in P9.SLEEVES}
    res = P9.run_book(mkt, P9.SLEEVES, P9.budget_fn_factory(w, combined=True),
                      caps=P9.default_caps(), kill=P9.default_kill())
    bt = pd.DataFrame(res["trades"])
    bt["exit_date"] = pd.to_datetime(bt["exit_date"])
    bt = bt[bt["reason"] != "eod"]              # end-of-tape mark-outs are not real exits
    bt_f = follow(bt, panel, idx)
    bt_s = summarise(bt_f, "BACKTEST book")

    # ---- report ----
    for s in (live_s, bt_s):
        print("\n" + "-" * 64)
        print(f"  {s['label']}: {s['n_trades']} closed trades, "
              f"{s['n_with_full_window']} with a full {FULL_H}-day follow-up")
        if "overall" in s:
            o = s["overall"]
            print(f"    mean move after exit      {o['mean_ret_10d']*100:+6.2f}%")
            print(f"    vs market (relative)      {o['mean_rel_10d']*100:+6.2f}%")
            print(f"    rose after we sold        {o['pct_rose_after']:5.1f}% of trades")
            print(f"    beat the market after     {o['pct_beat_market_after']:5.1f}% of trades")
            print(f"    money left behind         Rs.{o['total_left_behind']:,.0f}")
            print("    by exit reason:")
            for r, v in sorted(s["by_reason"].items()):
                print(f"      {r:<9s} n={v['n']:<5d} move {v['mean_ret_10d']*100:+6.2f}%  "
                      f"vs mkt {v['mean_rel_10d']*100:+6.2f}%  rose {v['pct_rose_after']:5.1f}%")
        else:
            print(f"    {s.get('note')}")

    rep = {"horizons": HORIZONS, "full_horizon": FULL_H,
           "panel_end": str(panel["date"].max())[:10],
           "live": {"summary": live_s, "trades": live_f.to_dict("records")},
           "backtest": {"summary": bt_s},
           "seed": C.SEED}
    out = C.ARTIFACTS_DIR / "p14_post_exit.json"
    out.write_text(json.dumps(rep, indent=1, default=str))
    print(f"\n  wrote {out}")
    print("\n  CAVEATS: censored rows excluded from summaries; hindsight cannot confirm a")
    print("  rule change -- only a pre-registered test against the noise band can.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
