"""
Phase 0b - Universe screener: pick a TRADABLE watchlist from the NIFTY 50.

THE BIAS TRAP (why selection must be careful)
---------------------------------------------
Picking the stocks that PERFORMED BEST in the past and then backtesting on them is
survivorship / data-snooping bias -- the backtest looks good for a fake reason. So we
select the universe on EX-ANTE TRADABILITY ONLY:
  * enough history (>= SWING_MIN_DAYS) for a fair walk-forward,
  * liquidity  = median daily traded VALUE (close*volume), so fills are realistic,
  * data quality = few zero-volume bars and few OHLC adjustment artifacts.
We rank by LIQUIDITY (not returns) and take the top N. The strategy's SIGNAL, not the
screener, decides which names to actually hold. This is a legitimate, forward-fair
selection; ranking by past return would not be.

Candidate universe = NIFTY 50 (all liquid, all F&O-eligible). Symbols yfinance cannot
serve, or with too little history (recent listings / demergers), are dropped cleanly.

ASCII-only prints. Run:  C:/Users/Admin/anaconda3/python.exe p0_universe.py
Writes: data/watchlist.json, artifacts/universe_screen.json
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

import config as C
from p1_swing_data import fetch_daily

warnings.filterwarnings("ignore")

# NIFTY 50 constituents (NSE symbols; fetched 2026-07). Recent listings/demergers with
# thin history (e.g. JIOFIN, ETERNAL, TMPV) are dropped by the history filter.
NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
    "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK",
    "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN", "SUNPHARMA", "TCS", "TATACONSUM", "TMPV",
    "TATASTEEL", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

N_SELECT = 20                       # size of the final watchlist
MIN_TURNOVER_INR = 20e7            # >= Rs.20 crore median daily turnover to be tradable
LIQ_WINDOW = 252                    # recent ~1y window for the liquidity estimate


def screen_symbol(sym: str) -> dict | None:
    """Fetch + compute ex-ante tradability metrics for one symbol (None if unavailable)."""
    try:
        d = fetch_daily(sym)
    except Exception as e:
        return {"symbol": sym, "ok": False, "reason": f"fetch_fail:{type(e).__name__}"}
    n = len(d)
    if n < C.SWING_MIN_DAYS:
        return {"symbol": sym, "ok": False, "reason": f"short_history:{n}", "n_days": n}
    recent = d.tail(LIQ_WINDOW)
    turnover = (recent["close"] * recent["volume"]).to_numpy()
    med_turn = float(np.median(turnover))
    zero_vol_frac = float((d["volume"] == 0).mean())
    repaired = int(d.attrs.get("n_ohlc_repaired", 0))
    return {"symbol": sym, "ok": True, "n_days": int(n),
            "start": str(d["date"].min().date()), "end": str(d["date"].max().date()),
            "med_daily_turnover_inr": med_turn,
            "med_daily_turnover_cr": med_turn / 1e7,
            "zero_vol_frac": zero_vol_frac, "n_ohlc_repaired": repaired,
            "liquid_enough": med_turn >= MIN_TURNOVER_INR}


def main() -> int:
    print("=" * 66)
    print("  UNIVERSE SCREEN - NIFTY 50 -> tradable swing watchlist (ex-ante only)")
    print("=" * 66)
    rows = []
    for i, sym in enumerate(NIFTY50, 1):
        r = screen_symbol(sym)
        rows.append(r)
        tag = ("OK  turn=%6.1f cr  hist=%d" % (r["med_daily_turnover_cr"], r["n_days"])
               if r.get("ok") else "DROP %s" % r.get("reason"))
        print(f"  [{i:2d}/{len(NIFTY50)}] {sym:12s} {tag}")

    ok_rows = [r for r in rows if r.get("ok") and r.get("liquid_enough")]
    # RANK BY LIQUIDITY (not returns), take top N
    ok_rows.sort(key=lambda r: r["med_daily_turnover_inr"], reverse=True)
    selected = ok_rows[:N_SELECT]
    sel_syms = [r["symbol"] for r in selected]

    print("\n  --- SELECTED (top %d by median daily turnover) ---" % len(sel_syms))
    for r in selected:
        print(f"    {r['symbol']:12s} turnover={r['med_daily_turnover_cr']:7.1f} cr/day  "
              f"hist={r['n_days']}d  zero_vol={r['zero_vol_frac']*100:.2f}%")
    dropped = [r["symbol"] for r in rows if not (r.get("ok") and r.get("liquid_enough"))]
    print(f"\n  dropped ({len(dropped)}): {', '.join(dropped)}")

    # ---- handoffs ----
    (C.DATA_DIR / "watchlist.json").write_text(json.dumps({
        "selected": sel_syms, "n": len(sel_syms),
        "criteria": "ex-ante tradability: history>=%d, median daily turnover>=Rs.%.0f cr, "
                    "ranked by liquidity (NOT past returns)" % (C.SWING_MIN_DAYS, MIN_TURNOVER_INR / 1e7),
        "candidate_universe": "NIFTY 50", "generated": str(pd.Timestamp.today().date()),
    }, indent=2))
    (C.ARTIFACTS_DIR / "universe_screen.json").write_text(json.dumps({
        "candidate_universe": "NIFTY 50", "n_candidates": len(NIFTY50),
        "n_select": N_SELECT, "min_turnover_inr": MIN_TURNOVER_INR,
        "selected": sel_syms, "all_metrics": rows,
        "note": "selection is ex-ante tradability only; ranking by return would be data snooping",
    }, indent=2, default=str))
    print(f"\n  wrote {C.DATA_DIR / 'watchlist.json'}  ({len(sel_syms)} symbols)")
    print(f"  wrote {C.ARTIFACTS_DIR / 'universe_screen.json'}")
    print("\nUNIVERSE SCREEN COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
