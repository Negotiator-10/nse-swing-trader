"""
p11c_swing_shadow.py -- PIECE 1 of the stock-exchange (rotation) advancement: the
"waiting stock" what-if tracker (DECISIONS #005 / #008 follow-on).

WHAT IT ANSWERS. When the book is full (4/4) and a fresh signal is DISCARDED by the caps
(SHRIRAMFIN has been skipped every live day), what would that stock have done if we HAD
bought it? This tracks each skipped candidate as an independent hypothetical position and
compares it to the stocks we actually kept -- the evidence needed BEFORE building the real
stock-exchange (rotation) feature, so it is decided by data not a hunch.

HOW (faithful + strictly read-only):
  - The SHADOW book runs through the IDENTICAL engine (p11.online_replay -> p9.step_day)
    with the caps LIFTED and a huge notional cash, so NOTHING is ever skipped: every armed
    signal fills at standard VaR size, each an independent what-if with the SAME entry
    (next open + slippage), SAME ATR stop, SAME trend/max-hold exit. Kill switch OFF (each
    what-if runs its own course, it is not a portfolio backstop).
  - The shadow entries the REAL book never took = the waiting/skipped stocks.
  - It writes ONLY data/paper_shadow.parquet. It NEVER touches paper_state.json / the
    ledger / any production path (the DECISIONS #007 rule: measurement code stays read-only).

TRUST. The caller (p11.live_smoke) has already proven, via the equity-hash gate, that the
book it hands us is the real one; a standalone run additionally self-checks that the
reconstructed real book reproduces the persisted ledger + state before trusting the shadow.

ASCII-only prints (Windows cp1252).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import config as C
import p9_swing_portfolio as P9

SHADOW_COLS = ["decision_date", "kind", "symbol", "status", "entry_date",
               "entry_px", "mark_px", "days", "exit_reason", "ret_pct"]

# caps wide enough that NOTHING is ever skipped -> every armed signal fills
_BIG_CAPS = {"max_positions": 999, "portfolio_risk_budget": 1e15,
             "max_notional_frac": 1e9, "gross_frac": 1e9}


def _ret_pct(entry_px: float, mark_px: float) -> float:
    return (mark_px / entry_px - 1.0) * 100.0


def run_shadow(mkt, budget_fn, era_k: int, real_state, *, persist: bool = True) -> dict:
    """Compute the waiting-stock what-if table against the real book. Returns a summary and
    (if persist) writes data/paper_shadow.parquet. real_state is the REAL BookState."""
    import p11_swing_paper as P11   # lazy: p11.live_smoke imports us, avoid a cycle

    last = len(mkt.dates) - 1
    decision_date = str(pd.Timestamp(mkt.dates[last]).date())

    # symbols the REAL book took (held now or traded) -- everything else the shadow takes
    # is a stock we WAITED on / skipped
    real_syms = {mkt.syms[j] for j in real_state.pos}
    if real_state.trades:
        real_syms |= {str(t["symbol"]) for t in real_state.trades}

    # ---- run the shadow book (lifted caps, huge cash, no kill) ----
    sh = P9.BookState()
    sh.cash = 1e12
    sh.peak = 1e12
    shadow = P11.online_replay(mkt, budget_fn, caps=_BIG_CAPS, kill=None, st=sh,
                               start_k=era_k, finalize=False, use_feed=False)
    sh_closed = pd.DataFrame(shadow["trades"])
    sh_open = {mkt.syms[j]: p for j, p in shadow["state"].pos.items()}

    rows = []
    # WAITING = shadow positions/trades on symbols the real book never held
    if len(sh_closed):
        for _, t in sh_closed.iterrows():
            if str(t["symbol"]) in real_syms:
                continue
            rows.append({"decision_date": decision_date, "kind": "waiting",
                         "symbol": str(t["symbol"]), "status": "closed",
                         "entry_date": str(pd.Timestamp(t["entry_date"]).date()),
                         "entry_px": float(t["entry_px"]), "mark_px": float(t["exit_px"]),
                         "days": int(t["days_held"]), "exit_reason": str(t["reason"]),
                         "ret_pct": _ret_pct(float(t["entry_px"]), float(t["exit_px"]))})
    for s, p in sh_open.items():
        if s in real_syms:
            continue
        j = mkt.syms.index(s)
        mark = float(mkt.Cl_ff[last, j])
        rows.append({"decision_date": decision_date, "kind": "waiting", "symbol": s,
                     "status": "open", "entry_date": str(pd.Timestamp(p["entry_date"]).date()),
                     "entry_px": float(p["entry_px"]), "mark_px": mark,
                     "days": int(last - p["entry_k"]), "exit_reason": "-",
                     "ret_pct": _ret_pct(float(p["entry_px"]), mark)})

    # HOLDS = the stocks we actually kept (current real open positions), same window
    for j, p in real_state.pos.items():
        s = mkt.syms[j]
        mark = float(mkt.Cl_ff[last, j])
        rows.append({"decision_date": decision_date, "kind": "hold", "symbol": s,
                     "status": "open", "entry_date": str(pd.Timestamp(p["entry_date"]).date()),
                     "entry_px": float(p["entry_px"]), "mark_px": mark,
                     "days": int(last - p["entry_k"]), "exit_reason": "-",
                     "ret_pct": _ret_pct(float(p["entry_px"]), mark)})

    df = pd.DataFrame(rows, columns=SHADOW_COLS)
    if persist:
        df.to_parquet(C.PAPER_SHADOW_PATH, index=False)

    wait = df[df["kind"] == "waiting"]
    hold = df[df["kind"] == "hold"]
    summary = {"decision_date": decision_date, "n_waiting": int(len(wait)),
               "waiting_symbols": sorted(wait["symbol"].tolist()),
               "mean_waiting_ret_pct": float(wait["ret_pct"].mean()) if len(wait) else None,
               "mean_hold_ret_pct": float(hold["ret_pct"].mean()) if len(hold) else None}
    if summary["mean_waiting_ret_pct"] is not None and summary["mean_hold_ret_pct"] is not None:
        summary["edge_pct"] = summary["mean_waiting_ret_pct"] - summary["mean_hold_ret_pct"]
    return summary


# ================================================================ standalone entry
def _build_live_market():
    """Rebuild the live-era Market from persisted files (mirrors live_smoke, minus Kite)."""
    import p11_swing_paper as P11
    panel, risk, _mkt_research, budget_fn = P11.setup()
    tape = pd.read_parquet(C.PAPER_LIVE_TAPE_PATH)
    tape = tape[[c for c in panel.columns if c in tape.columns]].copy()
    cutoff = pd.to_datetime(panel["date"]).max()
    live_rows = tape[pd.to_datetime(tape["date"]) > cutoff]
    panel2 = pd.concat([panel, live_rows], ignore_index=True)
    mkt2 = P9.Market(panel2, risk)
    era_k = int(np.argmax(pd.to_datetime(mkt2.dates) >= pd.Timestamp(C.PAPER_ERA_START)))
    return mkt2, budget_fn, era_k


def _selfcheck_real(mkt, budget_fn, era_k) -> P9.BookState:
    """Reconstruct the real book and PROVE it reproduces the persisted ledger + state."""
    import p11_swing_paper as P11
    real = P11.online_replay(mkt, budget_fn, caps=P9.default_caps(), kill=P9.default_kill(),
                             start_k=era_k, finalize=False, use_feed=False)
    st = real["state"]
    led = pd.read_parquet(C.PAPER_LEDGER_PATH) if C.PAPER_LEDGER_PATH.exists() else pd.DataFrame()
    recon = pd.DataFrame(st.trades)
    ok_closed = (len(recon) == len(led))
    if ok_closed and len(led):
        a = recon.sort_values("symbol")["pnl"].to_numpy()
        b = led.sort_values("symbol")["pnl"].to_numpy()
        ok_closed = bool(np.allclose(a, b, atol=1e-4))
    state = json.loads(C.PAPER_STATE_PATH.read_text())
    ok_open = ({s: int(p["shares"]) for s, p in state["pos"].items()}
               == {mkt.syms[j]: int(p["shares"]) for j, p in st.pos.items()})
    print(f"  self-check: closed-match={ok_closed}  open-match={ok_open}")
    if not (ok_closed and ok_open):
        raise SystemExit("  SELF-CHECK FAILED -- shadow not trustworthy; aborting.")
    return st


def main() -> int:
    print("=" * 64)
    print("  PIECE 1 - waiting-stock what-if shadow tracker (standalone)")
    print("=" * 64)
    mkt, budget_fn, era_k = _build_live_market()
    st = _selfcheck_real(mkt, budget_fn, era_k)
    s = run_shadow(mkt, budget_fn, era_k, st, persist=True)
    print(f"  decision {s['decision_date']}: {s['n_waiting']} waiting "
          f"({', '.join(s['waiting_symbols']) or 'none'})")
    if s.get("edge_pct") is not None:
        print(f"  mean return  waiting={s['mean_waiting_ret_pct']:+.2f}%  "
              f"holds={s['mean_hold_ret_pct']:+.2f}%  edge={s['edge_pct']:+.2f}% "
              f"(PRELIMINARY -- gross price return on a handful of days)")
    print(f"  wrote {C.PAPER_SHADOW_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
