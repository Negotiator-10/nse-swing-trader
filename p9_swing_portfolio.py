"""
Phase 3 (part 2/2) - Portfolio book: VaR-sized, risk-weighted, capped, kill-switched.

WHAT THIS IS
------------
p8 answered "how much can one share lose?" per stock per day. This module runs the BOOK:
a single daily, calendar-synchronized loop that turns the two signal sleeves
(ts_mom primary + tom secondary) into positions, sizes each to a fixed rupee VaR budget,
combines the sleeves by RISK-PARITY (inverse realized vol, walk-forward -- the principled
replacement for Phase 2's hand-tuned 50/50), bounds the book with a portfolio risk/notional/
count cap, and flattens everything on a rolling-drawdown kill switch. Out comes the first
real equity curve: CAGR, annualized Sharpe, and -- the number that actually matters -- the
MAX DRAWDOWN, versus buy&hold and a flat baseline, split IS vs OOS.

REUSE (not rebuilt): the NSE equity-delivery cost model (delivery_roundtrip_cost) and the
next-open-fill / trailing-ATR-stop / trend / max-hold EXIT semantics from p3_swing_backtest;
the causal signal columns from p2_swing_signal.SPECS; the risk snapshots from p8.

EXECUTION MODEL (identical to p3's event_loop, lifted to a portfolio)
--------------------------------------------------------------------
Signals read at a day's CLOSE -> filled at the NEXT open. A trailing ATR stop rests intraday
(gap-through fills at the open, else at the stop). A trend break / max-hold arms a market exit
at the next open. Slippage per side + the full delivery cost stack (STT both sides, exch, GST,
stamp, DP) per round trip. Sizing uses the VaR snapshot as of the decision CLOSE (causal).

GATES
-----
  G3.4 kill switch + cap binding : under an injected synthetic crash the book flattens + halts
        with ZERO post-kill fills; under a deliberately tight cap the portfolio limit BINDS
        (entries are skipped and the concurrent-position ceiling is never exceeded).
  G3.5 reproducibility           : fixed seed -> byte-identical equity path (hash).
  G3.6 portfolio verdict         : risk-parity 2-sleeve book vs ts_mom-alone vs buy&hold --
        does risk-weighting improve Sharpe AND drawdown? (honest verdict either way.)

ASCII-only prints. Run:  C:/Users/Admin/anaconda3/python.exe p9_swing_portfolio.py
Writes: data/swing_equity.parquet, artifacts/p3_portfolio.json
"""
from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config as C
from p2_swing_signal import SPECS, atr as atr_ind, compute_signals
from p3_swing_backtest import (SLIP, delivery_buy_cost, delivery_roundtrip_cost,
                               delivery_sell_cost)
from p8_swing_risk import size_position

warnings.filterwarnings("ignore")
np.random.seed(C.SEED)

TRADING_DAYS = 252.0
SLEEVES = list(C.SLEEVES)
STOP_MULT = {s: SPECS[s]["stop_mult"] for s in SLEEVES}
MAXHOLD = {s: SPECS[s]["maxhold"] for s in SLEEVES}
# Buy-leg cost as a fraction of notional (all buy-side components are proportional, so this
# is exact and constant). Used to size against cash now that the buy leg is charged at
# entry -- DECISIONS #016.
_BUY_COST_FRAC = delivery_buy_cost(1.0)


# ================================================================ cross-sectional score
def xs_score(g: pd.DataFrame, lookback: int = None) -> pd.Series:
    """Risk-adjusted momentum used to RANK competing entry candidates (DECISIONS #018).

        score_t = (close_t / close_{t-L} - 1) / stdev(daily log returns over the last L)

    Causal by construction: every input at day t uses bars <= t, the same information set
    the entry signal itself reads, and the fill still happens at t+1's open.

    L defaults to config.ENTRY_SCORE_LOOKBACK (126) = ts_mom's own momentum horizon, so no
    new lookback is introduced or tuned. Constant scalings (sqrt(252)) are deliberately
    omitted: ranking is invariant to them, and leaving them out keeps the number honest
    about what it is -- an ordering statistic, not an annualized Sharpe.

    Returns NaN where the window is not yet full or realized vol is zero (a dead tape);
    the ranker sorts NaN LAST, so an unscoreable name can never win a contested slot."""
    L = int(lookback or C.ENTRY_SCORE_LOOKBACK)
    c = g["close"].astype(float)
    mom = c / c.shift(L) - 1.0
    sd = np.log(c).diff().rolling(L, min_periods=L).std()
    return mom / sd.replace(0.0, np.nan)


def _rank_key(pend: dict, j: int) -> tuple:
    """Sort key for contested slots: best score first, alphabetical index as the ONLY
    tie-break (keeps the order total and deterministic -- reproducibility gate G3.5)."""
    s = pend[j].get("score", float("nan"))
    s = float(s) if s == s else float("-inf")      # NaN -> last
    return (-s, j)


# ================================================================ market/signal tensors
class Market:
    """Calendar-synchronized numpy tensors [n_days x n_syms] the book loop reads. Absent
    days are NaN; close is also forward-filled (Cl_ff) for continuous mark-to-market."""

    def __init__(self, panel: pd.DataFrame, risk: pd.DataFrame):
        panel = panel.copy()
        panel["date"] = pd.to_datetime(panel["date"])
        risk = risk.copy()
        risk["date"] = pd.to_datetime(risk["date"])
        self.syms = sorted(panel["symbol"].unique())
        self.dates = np.array(sorted(panel["date"].unique()))
        di = {d: k for k, d in enumerate(self.dates)}
        si = {s: j for j, s in enumerate(self.syms)}
        nD, nS = len(self.dates), len(self.syms)

        self.O = np.full((nD, nS), np.nan)
        self.H = np.full((nD, nS), np.nan)
        self.L = np.full((nD, nS), np.nan)
        self.Cl = np.full((nD, nS), np.nan)
        self.at = np.full((nD, nS), np.nan)
        self.split = np.empty((nD, nS), dtype=object)
        self.entry = {s: np.zeros((nD, nS), bool) for s in SLEEVES}
        self.exit = {s: np.zeros((nD, nS), bool) for s in SLEEVES}
        self.vpu = np.full((nD, nS), np.nan)     # var_per_unit at that decision close
        self.trad = np.zeros((nD, nS), bool)     # risk snapshot tradable that close
        # cross-sectional entry score (DECISIONS #018): risk-adjusted momentum at that
        # decision close. NaN until the lookback window fills -> ranks last, never first.
        self.score = np.full((nD, nS), np.nan)

        for sym, g in panel.groupby("symbol"):
            j = si[sym]
            g = g.sort_values("date")
            idx = g["date"].map(di).to_numpy()
            self.O[idx, j] = g["open"].to_numpy()
            self.H[idx, j] = g["high"].to_numpy()
            self.L[idx, j] = g["low"].to_numpy()
            self.Cl[idx, j] = g["close"].to_numpy()
            self.at[idx, j] = atr_ind(g).to_numpy()
            self.split[idx, j] = g["split"].to_numpy()
            self.score[idx, j] = xs_score(g).to_numpy()
            for s in SLEEVES:
                cs = compute_signals(g, s)
                self.entry[s][idx, j] = cs["entry"].to_numpy()
                self.exit[s][idx, j] = cs["exit_trend"].to_numpy()

        for sym, r in risk.groupby("symbol"):
            j = si[sym]
            idx = r["date"].map(di).to_numpy()
            keep = ~pd.isna(idx)
            idx = idx[keep].astype(int)
            self.vpu[idx, j] = r["var_per_unit"].to_numpy()[keep]
            self.trad[idx, j] = r["tradable"].to_numpy()[keep]

        self.Cl_ff = pd.DataFrame(self.Cl).ffill().to_numpy()
        # per-day IS/OOS label: a day is OOS if ANY symbol marks it test (per-symbol splits)
        self.is_oos = np.array([
            "test" if (self.split[k] == "test").any() else "train" for k in range(nD)])


# ================================================================ the shared day-step
# (Phase-4 refactor: the per-day body of run_book, extracted so the BATCH backtest and
#  the LIVE paper trader run the IDENTICAL code -- the reconciliation spine. Verified by
#  re-running p9: all Phase-3 gates PASS and the equity hash is byte-identical.)
@dataclass
class BookState:
    """Everything the book carries overnight. The batch backtest holds it in memory;
    the Phase-4 live paper trader persists it to disk between daily runs."""
    cash: float = C.INITIAL_CAPITAL_INR
    pos: dict = field(default_factory=dict)         # j -> open-position dict
    pend_entry: dict = field(default_factory=dict)  # j -> armed entry for the next open
    pend_exit: dict = field(default_factory=dict)   # j -> exit reason for the next open
    peak: float = C.INITIAL_CAPITAL_INR
    halted: bool = False
    halt_until: int = -1
    started: bool = False
    n_skips: int = 0
    skip_reasons: dict = field(default_factory=dict)
    max_concurrent: int = 0
    n_fills: int = 0
    fills_during_halt: int = 0
    trades: list = field(default_factory=list)
    last_equity: float = float("nan")
    # Per-candidate skip log (DECISIONS #005 prerequisite): every armed entry the caps
    # DISCARDED, with the incumbents it lost to. Diagnostic only -- never read by the
    # decision path, so it cannot move the equity hash. The live path persists the
    # live-era slice; the batch backtest fills it in memory and drops it.
    skip_log: list = field(default_factory=list)


@dataclass
class DayCtx:
    """One day's market context: everything step_day may read about day k."""
    k: int               # day counter (monotone; live mode keeps its own counter)
    date: object         # the day's date (trade-record key)
    syms: list           # symbol names, index-aligned with the arrays below
    sleeves: list        # active sleeves in priority order
    o: np.ndarray        # today's opens          [nS]
    l: np.ndarray        # today's lows           [nS]
    c: np.ndarray        # today's closes         [nS]
    clf: np.ndarray      # forward-filled closes  [nS] (mark-to-market)
    atr: np.ndarray      # ATR at today's close   [nS]
    entry: dict          # sleeve -> bool[nS] entry signal at this close
    exit_sig: dict       # sleeve -> bool[nS] trend-exit signal at this close
    vpu: np.ndarray      # var_per_unit risk snapshot at this close [nS]
    trad: np.ndarray     # risk snapshot tradable flag [nS]
    score: np.ndarray    # cross-sectional entry score at this close [nS] (#018)
    split: np.ndarray    # train/test label [nS]
    caps: dict
    kill: dict | None
    budget_fn: object    # (sleeve, k) -> per-position risk budget (Rs.)


def _do_exit(st: BookState, j: int, k: int, date, exit_px: float, reason: str) -> None:
    """Close position j at exit_px: cash in the proceeds net of the SELL-leg cost and
    append the closed-trade record.

    DECISIONS #016: only the SELL leg is charged here -- the BUY leg was already deducted
    at entry, as a real broker does. The trade's reported pnl still nets the FULL round
    trip, so per-trade P&L and lifetime cost are unchanged; what changed is that NAV is no
    longer flattered by unbooked buy-side costs while a position is open."""
    p = st.pos.pop(j)
    buy_val = p["shares"] * p["entry_px"]
    sell_val = p["shares"] * exit_px
    sell_cost = delivery_sell_cost(sell_val)
    cost = delivery_buy_cost(buy_val) + sell_cost      # full round trip, for the record
    st.cash += sell_val - sell_cost
    st.trades.append({"symbol": p["symbol"], "sleeve": p["sleeve"],
                      "entry_date": p["entry_date"], "exit_date": date,
                      "entry_px": p["entry_px"], "exit_px": float(exit_px),
                      "shares": int(p["shares"]), "reason": reason,
                      "days_held": int(k - p["entry_k"]),
                      "split": p["entry_split"],
                      "pnl": float(sell_val - buy_val - cost)})


def step_day(st: BookState, cx: DayCtx) -> tuple[float, float]:
    """THE shared day-step: sections (A)-(F) of the book for exactly one day.
    Mutates st in place; returns (close equity, invested value). Batch (run_book) and
    live (p11_swing_paper) both call this -- one implementation, zero drift."""
    k = cx.k
    halt_now = st.halted and k < st.halt_until

    # (A) pending market EXITS at today's open (incl kill-flatten)
    for j in list(st.pend_exit):
        if j in st.pos and np.isfinite(cx.o[j]):
            _do_exit(st, j, k, cx.date, cx.o[j] * (1 - SLIP), st.pend_exit.pop(j))
        elif j not in st.pos:
            st.pend_exit.pop(j, None)

    # (B) pending ENTRIES at today's open, subject to caps.
    # ORDER MATTERS: slots/risk/cash are consumed as we go, so whoever is served first wins
    # the scarce slot. Until #018 this order was the symbol index = ALPHABETICAL. Now the
    # armed candidates are served best-score-first (config.ENTRY_RANKING="xs_score");
    # "alpha" restores the legacy order byte-for-byte.
    order = list(st.pend_entry)
    if C.ENTRY_RANKING == "xs_score":
        order.sort(key=lambda jj: _rank_key(st.pend_entry, jj))
    for j in order:
        pe = st.pend_entry.pop(j)
        if j in st.pos or not np.isfinite(cx.o[j]):
            continue
        entry_px = cx.o[j] * (1 + SLIP)
        eq_ref = st.last_equity if np.isfinite(st.last_equity) else st.cash
        shares = size_position(pe["vpu"], entry_px, eq_ref, pe["budget"])
        if len(st.pos) >= cx.caps["max_positions"]:
            shares = 0; reason = "max_positions"
        else:
            reason = "risk_budget"
        open_risk = sum(p["shares"] * p["vpu"] for p in st.pos.values())
        room = cx.caps["portfolio_risk_budget"] - open_risk
        shares = min(shares, int(np.floor(room / pe["vpu"]))) if pe["vpu"] > 0 else 0
        inv_now = sum(p["shares"] * cx.clf[jj] for jj, p in st.pos.items())
        gross_room = cx.caps["gross_frac"] * eq_ref - inv_now
        shares = min(shares, int(np.floor(max(gross_room, 0) / entry_px)))
        # cash must cover the notional AND the buy-side cost now charged at entry (#016)
        shares = min(shares, int(np.floor(max(st.cash, 0) / (entry_px * (1 + _BUY_COST_FRAC)))))
        if shares <= 0:
            st.n_skips += 1
            st.skip_reasons[reason] = st.skip_reasons.get(reason, 0) + 1
            st.skip_log.append({
                "date": cx.date, "symbol": cx.syms[j], "sleeve": pe["sleeve"],
                "reason": reason, "budget": float(pe["budget"]),
                "vpu": float(pe["vpu"]), "atr_sig": float(pe["atr_sig"]),
                "score": float(pe.get("score", float("nan"))),
                "incumbents": ";".join(sorted(cx.syms[jj] for jj in st.pos))})
            continue
        # DECISIONS #016: charge the BUY leg now, as a real broker does, instead of
        # deferring the whole round trip to the exit (which flattered NAV while open).
        st.cash -= shares * entry_px + delivery_buy_cost(shares * entry_px)
        st.pos[j] = {"symbol": cx.syms[j], "shares": shares, "entry_px": entry_px,
                     "entry_k": k, "entry_date": cx.date, "entry_split": str(cx.split[j]),
                     "stop": entry_px - STOP_MULT[pe["sleeve"]] * pe["atr_sig"],
                     "atr_sig": pe["atr_sig"], "sleeve": pe["sleeve"], "vpu": pe["vpu"]}
        st.n_fills += 1
        if halt_now:
            st.fills_during_halt += 1

    # (C) intraday trailing-stop for positions open before today
    for j in list(st.pos):
        p = st.pos[j]
        if k <= p["entry_k"] or not np.isfinite(cx.l[j]):
            continue
        if cx.o[j] <= p["stop"]:
            _do_exit(st, j, k, cx.date, cx.o[j] * (1 - SLIP), "stop_gap")
        elif cx.l[j] <= p["stop"]:
            _do_exit(st, j, k, cx.date, p["stop"] * (1 - SLIP), "stop")

    # (D) at close: trail stops up; arm signal/max-hold exits for the next open
    for j, p in st.pos.items():
        if np.isfinite(cx.atr[j]):
            p["stop"] = max(p["stop"], cx.c[j] - STOP_MULT[p["sleeve"]] * cx.atr[j])
        held = k - p["entry_k"]
        hit_trend = bool(cx.exit_sig[p["sleeve"]][j])
        if (hit_trend or held >= MAXHOLD[p["sleeve"]]) and j not in st.pend_exit:
            st.pend_exit[j] = "trend" if hit_trend else "maxhold"

    # mark-to-market equity at close
    inv_k = sum(p["shares"] * cx.clf[j] for j, p in st.pos.items())
    eq = st.cash + inv_k
    st.started = st.started or (len(st.pos) > 0) or (k > 0 and st.cash != C.INITIAL_CAPITAL_INR)
    st.max_concurrent = max(st.max_concurrent, len(st.pos))

    # (E) kill switch on close equity
    if cx.kill is not None:
        if st.started:
            st.peak = max(st.peak, eq)
        if (not st.halted) and st.started and eq < st.peak * (1 - cx.kill["dd_threshold"]):
            for j in list(st.pos):
                st.pend_exit[j] = "kill"
            st.halted = True
            st.halt_until = k + cx.kill["cooldown_days"]
        elif st.halted and k >= st.halt_until:
            st.halted = False
            st.peak = eq
    halt_now = st.halted and k < st.halt_until

    # (F) arm new entries on fresh signals (unless halted); one position per symbol
    if not halt_now:
        for j in range(len(cx.syms)):
            if j in st.pos or j in st.pend_entry or j in st.pend_exit:
                continue
            if not cx.trad[j] or not (cx.vpu[j] > 0) or not np.isfinite(cx.atr[j]):
                continue
            for s in cx.sleeves:
                if cx.entry[s][j]:
                    st.pend_entry[j] = {"sleeve": s, "vpu": float(cx.vpu[j]),
                                        "atr_sig": float(cx.atr[j]),
                                        "budget": cx.budget_fn(s, k),
                                        # captured at the decision close, so tomorrow's
                                        # ranking cannot peek at tomorrow's tape (#018)
                                        "score": float(cx.score[j])}
                    break

    st.last_equity = eq
    return float(eq), float(inv_k)


# ================================================================ the book loop
def run_book(mkt: Market, sleeves, budget_fn, *, caps, kill, price_mult=None,
             mtm_mult=None, seed=C.SEED) -> dict:
    """One daily portfolio simulation.

    sleeves    : which signal sleeves are active (priority order; one position per symbol).
    budget_fn  : (sleeve, k) -> per-position 99% horizon-loss budget (rupees) at decision k.
    caps       : dict(max_positions, portfolio_risk_budget, max_notional_frac, gross_frac).
    kill       : dict(dd_threshold, cooldown_days) or None to disable.
    price_mult : optional [nD] multiplier on ALL of O/H/L/Cl (a full synthetic crash; note the
                 per-position ATR stops will flatten on the gap, so this exercises the STOPS).
    mtm_mult   : optional [nD] multiplier on the marking close ONLY (Cl/Clf), leaving O/H/L
                 intact so intraday stops do NOT pre-empt -- isolates the KILL SWITCH's
                 flatten+halt path for Gate G3.4a.

    Returns equity path, realized daily returns, trades, skip log, and cap/kill telemetry.
    """
    O, Hh, Ll, Cl, Clf, at = mkt.O, mkt.H, mkt.L, mkt.Cl, mkt.Cl_ff, mkt.at
    if price_mult is not None:
        m = price_mult[:, None]
        O, Hh, Ll, Cl, Clf = O * m, Hh * m, Ll * m, Cl * m, Clf * m
    if mtm_mult is not None:
        mm = mtm_mult[:, None]
        Cl, Clf = Cl * mm, Clf * mm      # mark-to-market shock only (stops read O/L, untouched)
    nD, nS = Cl.shape

    st = BookState()
    equity = np.full(nD, np.nan)
    invested_path = np.zeros(nD)
    for k in range(nD):
        cx = DayCtx(k=k, date=mkt.dates[k], syms=mkt.syms, sleeves=sleeves,
                    o=O[k], l=Ll[k], c=Cl[k], clf=Clf[k], atr=at[k],
                    entry={s: mkt.entry[s][k] for s in sleeves},
                    exit_sig={s: mkt.exit[s][k] for s in sleeves},
                    vpu=mkt.vpu[k], trad=mkt.trad[k], split=mkt.split[k],
                    score=mkt.score[k], caps=caps, kill=kill, budget_fn=budget_fn)
        equity[k], invested_path[k] = step_day(st, cx)

    # close residual positions at the last available close (mark-out)
    for j in list(st.pos):
        _do_exit(st, j, nD - 1, mkt.dates[nD - 1], Clf[nD - 1, j], "eod")
    equity[nD - 1] = st.cash
    cash = st.cash
    trades, n_skips, skip_reasons = st.trades, st.n_skips, st.skip_reasons
    max_concurrent, n_fills = st.max_concurrent, st.n_fills
    fills_during_halt = st.fills_during_halt

    eq = pd.Series(equity).ffill().fillna(C.INITIAL_CAPITAL_INR).to_numpy()
    ret = np.zeros(nD)
    ret[1:] = eq[1:] / eq[:-1] - 1.0
    return {"equity": eq, "ret": ret, "trades": trades, "n_skips": n_skips,
            "skip_reasons": skip_reasons, "max_concurrent": max_concurrent,
            "n_fills": n_fills, "fills_during_halt": fills_during_halt,
            "invested_path": invested_path}


# ================================================================ sleeve risk-parity weights
def sleeve_weights(rets: dict) -> dict:
    """Walk-forward inverse-vol (risk-parity) weights per day from each sleeve's standalone
    realized daily returns. Uses ONLY returns strictly before day k (causal). 50/50 until
    SLEEVE_MIN_HISTORY days exist; re-estimated every SLEEVE_REWEIGHT_EVERY days."""
    names = list(rets)
    nD = len(rets[names[0]])
    W = {s: np.full(nD, 1.0 / len(names)) for s in names}
    cur = {s: 1.0 / len(names) for s in names}
    win, every, minh = C.SLEEVE_VOL_WINDOW, C.SLEEVE_REWEIGHT_EVERY, C.SLEEVE_MIN_HISTORY
    for k in range(nD):
        if k >= minh and (k % every == 0):
            lo = max(0, k - win)
            vols = {}
            for s in names:
                x = rets[s][lo:k]                   # strictly before k
                x = x[np.isfinite(x)]
                vols[s] = float(np.std(x)) if len(x) > 5 else np.nan
            if all(np.isfinite(v) and v > 1e-9 for v in vols.values()):
                inv = {s: 1.0 / vols[s] for s in names}
                tot = sum(inv.values())
                cur = {s: inv[s] / tot for s in names}
        for s in names:
            W[s][k] = cur[s]
    return W


# ================================================================ metrics + baselines
def metrics(eq: np.ndarray, ret: np.ndarray, dates, mask=None) -> dict:
    if mask is None:
        mask = np.ones(len(eq), bool)
    e = eq[mask]
    r = ret[mask]
    if len(e) < 3:
        return {"n_days": int(len(e))}
    n = len(e)
    yrs = n / TRADING_DAYS
    cagr = (e[-1] / e[0]) ** (1.0 / yrs) - 1.0 if e[0] > 0 and e[-1] > 0 else float("nan")
    sd = float(np.std(r))
    sharpe = float(np.mean(r) / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else float("nan")
    peak = np.maximum.accumulate(e)
    maxdd = float((e / peak - 1.0).min())
    return {"n_days": int(n), "years": float(yrs), "cagr": float(cagr),
            "ann_sharpe": sharpe, "max_drawdown": maxdd,
            "total_return": float(e[-1] / e[0] - 1.0), "end_equity": float(e[-1])}


def buy_hold_book(mkt: Market) -> dict:
    """Equal-weight buy&hold across the watchlist: invest capital equally at each symbol's
    first available open, one delivery round trip, mark-to-market daily to the end."""
    nD, nS = mkt.Cl.shape
    per_sym = C.INITIAL_CAPITAL_INR / nS
    shares = np.zeros(nS)
    entry_val = np.zeros(nS)
    started = np.zeros(nS, bool)
    equity = np.full(nD, np.nan)
    for k in range(nD):
        for j in range(nS):
            if not started[j] and np.isfinite(mkt.O[k, j]) and mkt.O[k, j] > 0:
                shares[j] = per_sym / (mkt.O[k, j] * (1 + SLIP))
                entry_val[j] = shares[j] * mkt.O[k, j] * (1 + SLIP)
                started[j] = True
        mtm = sum(shares[j] * mkt.Cl_ff[k, j] for j in range(nS) if started[j])
        idle = per_sym * (~started).sum()
        equity[k] = mtm + idle
    # subtract one round-trip cost per symbol at the end (approx exit)
    end_cost = sum(delivery_roundtrip_cost(entry_val[j], shares[j] * mkt.Cl_ff[-1, j])
                   for j in range(nS) if started[j])
    equity[-1] -= end_cost
    eq = pd.Series(equity).ffill().fillna(C.INITIAL_CAPITAL_INR).to_numpy()
    ret = np.zeros(nD); ret[1:] = eq[1:] / eq[:-1] - 1.0
    return {"equity": eq, "ret": ret}


def exposure_turnover(res: dict, mkt: Market) -> dict:
    tr = pd.DataFrame(res["trades"])
    yrs = len(mkt.dates) / TRADING_DAYS
    buy_val = float((tr["shares"] * tr["entry_px"]).sum()) if len(tr) else 0.0
    mean_eq = float(np.mean(res["equity"]))
    exposure = float(np.mean(res["invested_path"] / res["equity"]))
    return {"n_trades": int(len(tr)),
            "turnover_x_per_yr": float(buy_val / mean_eq / yrs) if mean_eq > 0 else float("nan"),
            "win_rate": float((tr["pnl"] > 0).mean()) if len(tr) else float("nan"),
            "mean_exposure": exposure,
            "max_concurrent": int(res["max_concurrent"]), "n_skips": int(res["n_skips"])}


# ================================================================ default cap/kill configs
def default_caps():
    return {"max_positions": C.MAX_CONCURRENT_POSITIONS,
            "portfolio_risk_budget": C.PORTFOLIO_RISK_BUDGET_INR,
            "max_notional_frac": C.MAX_POSITION_NOTIONAL_FRAC,
            "gross_frac": C.MAX_GROSS_EXPOSURE_FRAC}


def default_kill():
    return {"dd_threshold": C.KILL_DD_THRESHOLD, "cooldown_days": C.KILL_COOLDOWN_DAYS}


# ================================================================ gates
def _hash_eq(eq: np.ndarray) -> str:
    return hashlib.sha256(np.round(eq, 6).tobytes()).hexdigest()


def run_gates(mkt, res_ts, res_combo, weights, panel=None) -> tuple[bool, dict]:
    print("\n" + "=" * 64)
    print("  PHASE 3 PORTFOLIO GATES")
    print("=" * 64)
    ok = True
    rep = {}

    def check(name, cond, detail=""):
        nonlocal ok
        st = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"  [{st}] {name}  {detail}")
        return bool(cond)

    # ---- G3.4a kill switch under FAULT INJECTION ----
    # The per-position ATR stops are the FIRST line of defense: a full price crash trips the
    # stops (gap-fills) and flattens the book before the portfolio kill switch ever sees the
    # close -- so to isolate and prove the KILL SWITCH's own flatten+halt path we inject a
    # mark-to-market-ONLY shock (close prices) at the max-exposure OOS day, deep enough to
    # breach the 20% drawdown line while intraday O/L (hence the stops) stay intact. We assert
    # the switch (1) issues 'kill' flatten exits, (2) halts, and (3) takes ZERO fills while halted.
    nD = len(mkt.dates)
    oos_mask = mkt.is_oos == "test"
    expo = res_combo["invested_path"] / np.maximum(res_combo["equity"], 1.0)
    expo = np.where(oos_mask, expo, -1.0)
    kstar = int(np.argmax(expo))                 # OOS day of highest fractional exposure
    kstar = min(max(kstar, 1), nD - 40)
    shock = 1.0 - (C.KILL_DD_THRESHOLD + 0.10) / max(expo[kstar], 1e-6)   # breach DD by ~10pts
    shock = max(shock, 0.02)
    mm = np.ones(nD)
    mm[kstar:kstar + 5] = shock                  # 5-day marked crash (positions stay open)
    fb = budget_fn_factory(weights, combined=True)
    crash = run_book(mkt, SLEEVES, fb, caps=default_caps(), kill=default_kill(), mtm_mult=mm)
    kill_exits = int(sum(t["reason"] == "kill" for t in crash["trades"]))
    kill_fired = crash["fills_during_halt"] == 0 and kill_exits > 0
    rep["G3.4a_killswitch"] = {"fills_during_halt": int(crash["fills_during_halt"]),
                               "kill_exits": kill_exits, "crash_at": str(mkt.dates[kstar])[:10],
                               "exposure_at_crash": float(expo[kstar]), "mtm_shock": float(shock)}
    check("G3.4a kill switch flattens ('kill' exits) + halts, ZERO fills during halt",
          kill_fired, f"  kill_exits={kill_exits} fills_during_halt={crash['fills_during_halt']} "
          f"expo={expo[kstar]*100:.0f}% shock={shock:.2f}")

    # ---- G3.4b portfolio cap BINDS under a deliberately tight cap ----
    tight = {"max_positions": 2, "portfolio_risk_budget": C.PER_POSITION_RISK_BUDGET_INR * 1.5,
             "max_notional_frac": C.MAX_POSITION_NOTIONAL_FRAC, "gross_frac": 0.30}
    tb = budget_fn_factory(weights, combined=True)
    capped = run_book(mkt, SLEEVES, tb, caps=tight, kill=None)
    binds = capped["n_skips"] > 0 and capped["max_concurrent"] <= tight["max_positions"]
    rep["G3.4b_capbinding"] = {"n_skips": int(capped["n_skips"]),
                               "skip_reasons": capped["skip_reasons"],
                               "max_concurrent": int(capped["max_concurrent"]),
                               "cap": tight["max_positions"]}
    check("G3.4b portfolio cap binds (entries skipped, concurrent <= cap)", binds,
          f"  skips={capped['n_skips']} max_concurrent={capped['max_concurrent']} "
          f"(cap={tight['max_positions']})")

    # ---- G3.5 reproducibility: identical equity path on a rerun ----
    fb2 = budget_fn_factory(weights, combined=True)
    rerun = run_book(mkt, SLEEVES, fb2, caps=default_caps(), kill=default_kill())
    same = _hash_eq(rerun["equity"]) == _hash_eq(res_combo["equity"])
    rep["G3.5"] = {"hash": _hash_eq(res_combo["equity"]), "identical": bool(same)}
    check("G3.5 reproducibility (rerun book -> byte-identical equity path)", same,
          f"  sha256={rep['G3.5']['hash'][:16]}...")

    # ---- G3.6 does risk-weighting improve Sharpe AND drawdown vs ts_mom-alone? ----
    oos = mkt.is_oos == "test"
    m_ts = metrics(res_ts["equity"], res_ts["ret"], mkt.dates, oos)
    m_cb = metrics(res_combo["equity"], res_combo["ret"], mkt.dates, oos)
    better_sharpe = m_cb["ann_sharpe"] >= m_ts["ann_sharpe"]
    better_dd = m_cb["max_drawdown"] >= m_ts["max_drawdown"]     # less negative = better
    rep["G3.6"] = {"oos_ts_mom_alone": m_ts, "oos_risk_parity_combo": m_cb,
                   "risk_parity_improves_sharpe": bool(better_sharpe),
                   "risk_parity_improves_drawdown": bool(better_dd)}
    if len(SLEEVES) == 1:
        # Single sleeve (tom retired, DECISIONS #013): the "combo" IS ts_mom-alone, so the
        # comparison is vacuous by construction. Say so rather than printing a self-
        # congratulatory "improves BOTH" that compares a book against itself.
        verdict = (f"single-sleeve book ({SLEEVES[0]}): comparison is degenerate -- combo == "
                   f"standalone. Kept as a live check that no second sleeve is silently "
                   f"active; restore SLEEVES=['ts_mom','tom'] to re-open the comparison")
    else:
        verdict = ("risk-parity 2-sleeve improves BOTH Sharpe and drawdown vs ts_mom-alone"
                   if (better_sharpe and better_dd) else
                   "risk-parity 2-sleeve does NOT dominate ts_mom-alone on both axes "
                   "(see numbers) -- honest finding: tom's marginal edge does not clearly "
                   "earn its risk allocation; ts_mom-alone remains the default")
    rep["G3.6"]["verdict"] = verdict
    # G3.6 is a REPORT gate: it always 'passes' (records the honest verdict), never loosened.
    check("G3.6 portfolio comparison computed (honest verdict recorded)", True,
          f"  ts_mom Sharpe={m_ts['ann_sharpe']:.2f} DD={m_ts['max_drawdown']*100:.1f}% | "
          f"combo Sharpe={m_cb['ann_sharpe']:.2f} DD={m_cb['max_drawdown']*100:.1f}%")
    print(f"\n  VERDICT: {verdict}")

    # ---- G3.7 cross-sectional entry ranking (DECISIONS #018) ----
    # (a) the score must be CAUSAL: recomputing it on a truncated tape must reproduce the
    #     full-panel value at that same day. Same probe pattern as p2's no-lookahead gate.
    if panel is not None:
        rng = np.random.default_rng(C.SEED)
        probes = mism = 0
        for sym, g in panel.groupby("symbol"):
            g = g.sort_values("date").reset_index(drop=True)
            full = xs_score(g)
            valid = full.index[full.notna()]
            if len(valid) < 5:
                continue
            for t in rng.choice(valid, size=min(4, len(valid)), replace=False):
                a = float(full.iloc[t])
                b = float(xs_score(g.iloc[:t + 1]).iloc[-1])
                probes += 1
                if not (abs(a - b) <= 1e-9 * max(1.0, abs(a))):
                    mism += 1
        rep["G3.7a_score_causal"] = {"probes": int(probes), "mismatches": int(mism),
                                     "lookback": int(C.ENTRY_SCORE_LOOKBACK)}
        check("G3.7a entry score is causal (truncated recompute == full panel)", mism == 0,
              f"  probes={probes} mismatches={mism} L={C.ENTRY_SCORE_LOOKBACK}")

    # (b) the ranker must be a TOTAL, score-descending order with a deterministic tie-break.
    _probe = {5: {"score": 1.0}, 2: {"score": 3.0}, 9: {"score": float("nan")},
              7: {"score": 3.0}, 1: {"score": -2.0}}
    _got = sorted(_probe, key=lambda jj: _rank_key(_probe, jj))
    ordered_ok = _got == [2, 7, 5, 1, 9]      # 3.0 ties break by index; NaN last
    rep["G3.7b_rank_order"] = {"probe_order": _got, "expected": [2, 7, 5, 1, 9]}
    check("G3.7b ranker orders by score desc, index tie-break, NaN last", ordered_ok,
          f"  got={_got}")

    # (c) does the ranking actually BIND, and what does it cost/earn? Run the SAME book
    #     under the legacy alphabetical order and under the score order. If the universe
    #     is small enough that signals never contend, the two are identical and the gate
    #     says so honestly rather than claiming an improvement that is not there.
    _prev = C.ENTRY_RANKING
    try:
        C.ENTRY_RANKING = "alpha"
        alpha_book = run_book(mkt, SLEEVES, budget_fn_factory(weights, combined=True),
                              caps=default_caps(), kill=default_kill())
        C.ENTRY_RANKING = "xs_score"
        score_book = run_book(mkt, SLEEVES, budget_fn_factory(weights, combined=True),
                              caps=default_caps(), kill=default_kill())
    finally:
        C.ENTRY_RANKING = _prev
    oos_m = mkt.is_oos == "test"
    m_alpha = metrics(alpha_book["equity"], alpha_book["ret"], mkt.dates, oos_m)
    m_score = metrics(score_book["equity"], score_book["ret"], mkt.dates, oos_m)
    binds = _hash_eq(alpha_book["equity"]) != _hash_eq(score_book["equity"])
    rep["G3.7c_ranking_effect"] = {
        "oos_alphabetical": m_alpha, "oos_xs_score": m_score,
        "ranking_changes_book": bool(binds),
        "n_trades_alpha": len(alpha_book["trades"]), "n_trades_score": len(score_book["trades"]),
        "hash_alpha": _hash_eq(alpha_book["equity"]),
        "hash_score": _hash_eq(score_book["equity"]),
        "active": C.ENTRY_RANKING}
    # REPORT gate: records the honest comparison either way, never loosened.
    check("G3.7c ranking comparison computed (alphabetical vs cross-sectional)", True,
          f"  alpha Sharpe={m_alpha['ann_sharpe']:.2f} CAGR={m_alpha['cagr']*100:.2f}% "
          f"DD={m_alpha['max_drawdown']*100:.1f}% | score Sharpe={m_score['ann_sharpe']:.2f} "
          f"CAGR={m_score['cagr']*100:.2f}% DD={m_score['max_drawdown']*100:.1f}% | "
          f"binds={binds}")

    rep["all_passed"] = ok
    print(f"\n{'ALL PHASE 3 PORTFOLIO GATES PASSED' if ok else 'PORTFOLIO GATE FINDING'}\n")
    return ok, rep


# ================================================================ budget schedules
def budget_fn_factory(weights, combined: bool):
    """Per-position budget. Standalone: fixed PER_POSITION_RISK_BUDGET. Combined risk-parity:
    scale by 2*weight_sleeve[k] so equal weights reproduce the standalone budget and the
    higher-weighted sleeve sizes larger (portfolio cap still bounds the aggregate)."""
    base = C.PER_POSITION_RISK_BUDGET_INR
    if not combined:
        return lambda s, k: base
    n = len(SLEEVES)
    # clamp k to the weights history: live days beyond the tape CARRY FORWARD the last
    # monthly weight (identical to the between-re-estimates behavior; still causal)
    return lambda s, k: base * (n * weights[s][min(k, len(weights[s]) - 1)])


# ================================================================ main
def main() -> int:
    print("=" * 64)
    print("  PHASE 3 - portfolio book (VaR-sized, risk-parity, capped, kill switch)")
    print(f"  capital=Rs.{C.INITIAL_CAPITAL_INR:,.0f}  per_pos_budget=Rs.{C.PER_POSITION_RISK_BUDGET_INR:,.0f}"
          f"  portfolio_budget=Rs.{C.PORTFOLIO_RISK_BUDGET_INR:,.0f}  max_pos={C.MAX_CONCURRENT_POSITIONS}")
    print("=" * 64)
    _src = (C.CLEAN_TAPE_PATH if (getattr(C, "USE_CLEAN_TAPE", False)
                                  and C.CLEAN_TAPE_PATH.exists())
            else C.DATA_DIR / "swing_daily.parquet")
    panel = pd.read_parquet(_src)
    print(f"  tape: {_src.name}" + ("  (CLEANED - DECISIONS #019)"
                                    if _src == C.CLEAN_TAPE_PATH else "  (original)"))
    risk = pd.read_parquet(C.DATA_DIR / "swing_risk_panel.parquet")
    print(f"  loaded {len(panel):,} bars, {len(risk):,} risk snapshots")
    mkt = Market(panel, risk)
    print(f"  calendar: {len(mkt.dates)} days {str(mkt.dates[0])[:10]}..{str(mkt.dates[-1])[:10]}, "
          f"{len(mkt.syms)} symbols")

    # ---- standalone sleeves (for risk-parity weighting + the ts_mom-alone benchmark) ----
    fixed = budget_fn_factory(None, combined=False)
    standalone = {}
    for s in SLEEVES:
        standalone[s] = run_book(mkt, [s], fixed, caps=default_caps(), kill=default_kill())
        m = metrics(standalone[s]["equity"], standalone[s]["ret"], mkt.dates)
        print(f"  sleeve {s:8s}: {len(standalone[s]['trades']):4d} trades  "
              f"CAGR={m['cagr']*100:5.1f}%  Sharpe={m['ann_sharpe']:.2f}  DD={m['max_drawdown']*100:5.1f}%")

    # ---- walk-forward risk-parity sleeve weights ----
    weights = sleeve_weights({s: standalone[s]["ret"] for s in SLEEVES})
    wavg = {s: float(np.mean(weights[s])) for s in SLEEVES}
    print(f"  risk-parity mean sleeve weights: " + ", ".join(f"{s}={wavg[s]:.2f}" for s in SLEEVES))

    # ---- the combined risk-parity book ----
    fb = budget_fn_factory(weights, combined=True)
    combo = run_book(mkt, SLEEVES, fb, caps=default_caps(), kill=default_kill())
    et = exposure_turnover(combo, mkt)
    print(f"  COMBINED book: {et['n_trades']} trades, turnover {et['turnover_x_per_yr']:.1f}x/yr, "
          f"win {et['win_rate']*100:.0f}%, mean exposure {et['mean_exposure']*100:.0f}%, "
          f"max concurrent {et['max_concurrent']}, skips {et['n_skips']}")

    # ---- baselines ----
    bh = buy_hold_book(mkt)

    ok, rep = run_gates(mkt, standalone["ts_mom"], combo, weights, panel=panel)

    # ---- full-period + IS/OOS metric table (context) ----
    oos = mkt.is_oos == "test"
    ins = ~oos
    tbl = {
        "combo_full": metrics(combo["equity"], combo["ret"], mkt.dates),
        "combo_IS": metrics(combo["equity"], combo["ret"], mkt.dates, ins),
        "combo_OOS": metrics(combo["equity"], combo["ret"], mkt.dates, oos),
        "ts_mom_alone_OOS": metrics(standalone["ts_mom"]["equity"], standalone["ts_mom"]["ret"], mkt.dates, oos),
        "buy_hold_OOS": metrics(bh["equity"], bh["ret"], mkt.dates, oos),
        "buy_hold_full": metrics(bh["equity"], bh["ret"], mkt.dates),
    }
    print("\n  --- OOS scoreboard (the honest headline) ---")
    for name in ["combo_OOS", "ts_mom_alone_OOS", "buy_hold_OOS"]:
        m = tbl[name]
        print(f"    {name:18s} CAGR={m['cagr']*100:6.1f}%  Sharpe={m['ann_sharpe']:5.2f}  "
              f"maxDD={m['max_drawdown']*100:6.1f}%  totRet={m['total_return']*100:6.1f}%")

    # ---- persist equity + report ----
    out = C.DATA_DIR / "swing_equity.parquet"
    # one equity column per ACTIVE sleeve (tom may be retired -- DECISIONS #013), so this
    # does not KeyError when SLEEVES shrinks
    eqcols = {"date": mkt.dates, "is_oos": mkt.is_oos, "equity_combo": combo["equity"],
              "equity_buyhold": bh["equity"]}
    for s in SLEEVES:
        eqcols[f"equity_{s}"] = standalone[s]["equity"]
    eqdf = pd.DataFrame(eqcols)
    eqdf.to_parquet(out, index=False)
    print(f"\n  wrote {out}")

    meta = {
        "config": {"capital": C.INITIAL_CAPITAL_INR, "per_pos_budget": C.PER_POSITION_RISK_BUDGET_INR,
                   "portfolio_budget": C.PORTFOLIO_RISK_BUDGET_INR, "max_positions": C.MAX_CONCURRENT_POSITIONS,
                   "kill_dd": C.KILL_DD_THRESHOLD, "horizon_days": C.RISK_HORIZON_DAYS,
                   "sleeve_method": C.SLEEVE_WEIGHT_METHOD},
        "sleeve_weights_mean": wavg,
        "combined_exposure_turnover": et,
        "metrics": tbl,
        "gates": rep, "seed": C.SEED,
        "versions": {"numpy": np.__version__, "pandas": pd.__version__},
    }
    (C.ARTIFACTS_DIR / "p3_portfolio.json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"  wrote {C.ARTIFACTS_DIR / 'p3_portfolio.json'}")

    # ---- smoke: reload equity, recompute OOS Sharpe, must match ----
    rr = pd.read_parquet(out)
    ret = np.zeros(len(rr)); e = rr["equity_combo"].to_numpy(); ret[1:] = e[1:] / e[:-1] - 1.0
    m2 = metrics(e, ret, rr["date"].to_numpy(), (rr["is_oos"] == "test").to_numpy())
    smoke = abs(m2["ann_sharpe"] - tbl["combo_OOS"]["ann_sharpe"]) < 1e-9
    print(f"  [{'PASS' if smoke else 'FAIL'}] smoke: reload equity, OOS Sharpe reproduces")

    print(f"\n{'PHASE 3 COMPLETE - ALL GATES PASSED' if ok else 'PHASE 3 COMPLETE - GATE FINDING (see report)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
