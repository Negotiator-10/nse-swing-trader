"""
Phase 4 (part 2/2) - The EOD paper-trading harness: online loop + paper fills +
state persistence + the gate suite. TWO run modes, ONE code path.

THE RECONCILIATION SPINE (why this is trustworthy)
--------------------------------------------------
The batch backtest (p9.run_book) and this ONLINE harness call the IDENTICAL
p9.step_day -- one implementation of the book's day, zero drift possible. Gate G4.1
then *proves* it: replaying the historical tape one day at a time reproduces the
batch equity path and trade list byte-for-byte. Once that identity holds, feeding
the same step a LIVE Kite bar instead of a tape row is a data swap, not new logic.

WHAT "PAPER FILL" MEANS HERE
----------------------------
A decision at today's close arms an entry/exit; it FILLS at the next session's open
with SLIPPAGE_BPS slippage and the full NSE delivery cost stack (STT both sides,
exchange txn, GST, stamp, DP) -- the exact accounting Phase 2 validated against a
hand calculation. NO real order exists anywhere in this system; going live with real
orders is a later, user-gated phase under SEBI's retail-algo framework.

STATE PERSISTENCE (a once-a-day process must survive restarts)
--------------------------------------------------------------
The book's overnight state (cash, open positions, armed entries/exits, kill-switch
state, counters) round-trips to data/paper_state.json; closed trades append to
data/paper_ledger.parquet. Gate G4.3 kills the loop mid-history, reloads, resumes,
and demands the result be identical to an uninterrupted run.

GATES
-----
  G4.1 reconciliation : online replay == batch book, byte-for-byte (equity + trades)
  G4.2 feed discipline: every fault detector fires (dup/out-of-order/malformed/
                        holiday/dead-man) -- via p10.fault_injection_report
  G4.3 paper-fill + persistence: cost accounting matches a hand calc; kill/reload/
                        resume mid-run == uninterrupted run
  G4.4 risk safety    : in the ONLINE loop -- tight cap binds (skips, concurrent<=cap);
                        injected MTM crash -> kill flatten+halt, ZERO fills during halt
  G4.5 reproducibility+hygiene: rerun -> byte-identical equity; run-mode stamped;
                        secrets checked by PRESENCE only
  G4.6 LIVE smoke     : (trading day + key) pull real EOD bars, validate freshness &
                        source consistency, run ONE decision, record intended paper
                        actions, persist state. No key -> SKIPPED, stamped.

ASCII-only prints. Run:  C:/Users/Admin/anaconda3/python.exe p11_swing_paper.py
Writes: artifacts/p4_paper.json, data/paper_state.json, data/paper_ledger.parquet
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import warnings

import numpy as np
import pandas as pd

import config as C
import p8_swing_risk as P8
import p9_swing_portfolio as P9
import p10_swing_feed as P10
from p3_swing_backtest import delivery_roundtrip_cost

warnings.filterwarnings("ignore")
np.random.seed(C.SEED)

SLEEVES = P9.SLEEVES


# ================================================================ online loop
def make_ctx(mkt: P9.Market, k: int, sleeves, budget_fn, caps, kill,
             mtm_mult: float = 1.0) -> P9.DayCtx:
    """Build one day's context from the market tensors (identical to run_book's)."""
    c_row, clf_row = mkt.Cl[k], mkt.Cl_ff[k]
    if mtm_mult != 1.0:
        c_row, clf_row = c_row * mtm_mult, clf_row * mtm_mult
    return P9.DayCtx(k=k, date=mkt.dates[k], syms=mkt.syms, sleeves=sleeves,
                     o=mkt.O[k], l=mkt.L[k], c=c_row, clf=clf_row, atr=mkt.at[k],
                     entry={s: mkt.entry[s][k] for s in sleeves},
                     exit_sig={s: mkt.exit[s][k] for s in sleeves},
                     vpu=mkt.vpu[k], trad=mkt.trad[k], split=mkt.split[k],
                     caps=caps, kill=kill, budget_fn=budget_fn)


def online_replay(mkt: P9.Market, budget_fn, *, caps, kill, st: P9.BookState = None,
                  start_k: int = 0, end_k: int = None, mtm_mult=None,
                  finalize: bool = True, use_feed: bool = True) -> dict:
    """Drive step_day one day at a time -- the ONLINE mirror of p9.run_book.

    st/start_k support resuming from persisted state (the live pattern). mtm_mult is
    the per-day mark-to-market fault-injection multiplier (G4.4). finalize replicates
    run_book's end-of-tape residual close-out so G4.1 can compare byte-for-byte."""
    nD = len(mkt.dates)
    end_k = nD if end_k is None else end_k
    st = st if st is not None else P9.BookState()
    fh = P10.DailyFeedHandler()
    equity = np.full(end_k - start_k, np.nan)
    invested = np.zeros(end_k - start_k)
    for k in range(start_k, end_k):
        if use_feed:                      # date discipline on the day's bars
            date_d = pd.Timestamp(mkt.dates[k]).date()
            for j, sym in enumerate(mkt.syms):
                if np.isfinite(mkt.O[k, j]):
                    fh.push(P10.DailyBar(symbol=sym, date=date_d, open=float(mkt.O[k, j]),
                                         high=float(mkt.H[k, j]), low=float(mkt.L[k, j]),
                                         close=float(mkt.Cl[k, j]),
                                         volume=0.0))
                else:
                    fh.note_no_bar(sym)
        mm = float(mtm_mult[k]) if mtm_mult is not None else 1.0
        cx = make_ctx(mkt, k, SLEEVES, budget_fn, caps, kill, mtm_mult=mm)
        equity[k - start_k], invested[k - start_k] = P9.step_day(st, cx)
    if finalize:
        for j in list(st.pos):
            P9._do_exit(st, j, end_k - 1, mkt.dates[end_k - 1], mkt.Cl_ff[end_k - 1, j], "eod")
        equity[-1] = st.cash
    return {"equity": equity, "invested": invested, "state": st, "feed": fh.s,
            "trades": st.trades}


# ================================================================ state persistence
def save_state(st: P9.BookState, k_next: int, mkt_syms: list, path=None) -> None:
    """Serialize the overnight book state to JSON (symbol-keyed, restart-safe)."""
    path = path or C.PAPER_STATE_PATH

    def ser_pos(p):
        q = dict(p)
        q["entry_date"] = str(pd.Timestamp(p["entry_date"]).date())
        return q

    doc = {"k_next": int(k_next), "cash": float(st.cash), "peak": float(st.peak),
           "halted": bool(st.halted), "halt_until": int(st.halt_until),
           "started": bool(st.started), "n_skips": int(st.n_skips),
           "skip_reasons": st.skip_reasons, "max_concurrent": int(st.max_concurrent),
           "n_fills": int(st.n_fills), "fills_during_halt": int(st.fills_during_halt),
           "last_equity": float(st.last_equity),
           "pos": {mkt_syms[j]: ser_pos(p) for j, p in st.pos.items()},
           "pend_entry": {mkt_syms[j]: pe for j, pe in st.pend_entry.items()},
           "pend_exit": {mkt_syms[j]: r for j, r in st.pend_exit.items()},
           "saved_utc": dt.datetime.utcnow().isoformat(), "seed": C.SEED}
    path.write_text(json.dumps(doc, indent=2, default=str))


def load_state(mkt_syms: list, path=None) -> tuple[P9.BookState, int]:
    """Reload the overnight state; symbol keys re-map to current tensor indices."""
    path = path or C.PAPER_STATE_PATH
    doc = json.loads(path.read_text())
    si = {s: j for j, s in enumerate(mkt_syms)}
    st = P9.BookState()
    st.cash = doc["cash"]; st.peak = doc["peak"]; st.halted = doc["halted"]
    st.halt_until = doc["halt_until"]; st.started = doc["started"]
    st.n_skips = doc["n_skips"]; st.skip_reasons = dict(doc["skip_reasons"])
    st.max_concurrent = doc["max_concurrent"]; st.n_fills = doc["n_fills"]
    st.fills_during_halt = doc["fills_during_halt"]; st.last_equity = doc["last_equity"]
    for sym, p in doc["pos"].items():
        q = dict(p)
        q["entry_date"] = np.datetime64(p["entry_date"])
        q["shares"] = int(p["shares"]); q["entry_k"] = int(p["entry_k"])
        st.pos[si[sym]] = q
    st.pend_entry = {si[s]: pe for s, pe in doc["pend_entry"].items()}
    st.pend_exit = {si[s]: r for s, r in doc["pend_exit"].items()}
    return st, int(doc["k_next"])


TRADE_COLS = ["symbol", "sleeve", "entry_date", "exit_date", "entry_px", "exit_px",
              "shares", "reason", "days_held", "split", "pnl"]


def trades_df(trades: list) -> pd.DataFrame:
    if not trades:            # a fresh live-era book has a VALID empty ledger (typed schema)
        return pd.DataFrame(columns=TRADE_COLS)
    df = pd.DataFrame(trades)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    return df.sort_values(["exit_date", "symbol", "entry_date"]).reset_index(drop=True)


# ================================================================ setup (shared inputs)
def setup():
    panel = pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")
    risk = pd.read_parquet(C.DATA_DIR / "swing_risk_panel.parquet")
    mkt = P9.Market(panel, risk)
    fixed = P9.budget_fn_factory(None, combined=False)
    standalone = {s: P9.run_book(mkt, [s], fixed, caps=P9.default_caps(),
                                 kill=P9.default_kill()) for s in SLEEVES}
    weights = P9.sleeve_weights({s: standalone[s]["ret"] for s in SLEEVES})
    budget_fn = P9.budget_fn_factory(weights, combined=True)
    return panel, risk, mkt, budget_fn


# ================================================================ gates
def _hash(eq: np.ndarray) -> str:
    return hashlib.sha256(np.round(np.asarray(eq, float), 6).tobytes()).hexdigest()


def run_gates(panel, mkt, budget_fn, run_mode) -> tuple[bool, dict, dict]:
    print("\n" + "=" * 64)
    print(f"  PHASE 4 GATES  (run_mode={run_mode}; gate suite always runs in REPLAY)")
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

    caps, kill = P9.default_caps(), P9.default_kill()

    # ---- G4.1 reconciliation spine: online replay == batch book ----
    batch = P9.run_book(mkt, SLEEVES, budget_fn, caps=caps, kill=kill)
    online = online_replay(mkt, budget_fn, caps=caps, kill=kill)
    eq_err = float(np.max(np.abs(online["equity"] - batch["equity"])))
    tb, to = trades_df(batch["trades"]), trades_df(online["trades"])
    same_trades = len(tb) == len(to) and tb.equals(to)
    g41 = eq_err < 1e-9 and same_trades
    rep["G4.1"] = {"max_equity_err": eq_err, "n_trades_batch": int(len(tb)),
                   "n_trades_online": int(len(to)), "trades_identical": bool(same_trades),
                   "equity_hash": _hash(online["equity"])}
    check("G4.1 reconciliation: online replay == batch book (equity + trades)", g41,
          f"  eq_err={eq_err:.1e} trades {len(to)}/{len(tb)} identical={same_trades}")

    # ---- G4.2 feed discipline: every detector fires under fault injection ----
    fi = P10.fault_injection_report(panel)
    g42 = all(v for k, v in fi.items() if k != "stats")
    live_feed_clean = (online["feed"].n_duplicates == 0 and online["feed"].n_out_of_order == 0)
    rep["G4.2"] = {"fault_injection": fi, "replay_stream_clean": bool(live_feed_clean)}
    check("G4.2 feed discipline: dup/ooo/malformed/holiday/dead-man detectors all fire",
          g42 and live_feed_clean,
          f"  clean replay stream: {online['feed'].n_accepted:,} bars accepted")

    # ---- G4.3 paper-fill accounting + state persistence across a restart ----
    # (a) cost accounting hand-check (same construction as Phase 2's G2.1)
    shares, epx, xpx = 100, 500.0, 520.0
    cost = delivery_roundtrip_cost(shares * epx, shares * xpx)
    dc = C.COSTS_EQUITY_DELIVERY
    hand = (dc["stt_pct_each_side"] * shares * (epx + xpx)
            + dc["exch_txn_pct"] * shares * (epx + xpx)
            + dc["sebi_pct"] * shares * (epx + xpx)
            + dc["stamp_buy_pct"] * shares * epx
            + dc["gst_pct"] * (dc["exch_txn_pct"] + dc["sebi_pct"]) * shares * (epx + xpx)
            + dc["dp_charge_sell_inr"])
    acct_ok = abs(cost - hand) < 1e-9
    # (b) kill the loop mid-history, persist, reload, resume -> identical to uninterrupted.
    # Round-trip through a TEMP path, NOT the production state file: this gate runs on
    # every REPLAY invocation, and writing C.PAPER_STATE_PATH here would clobber the LIVE
    # book whenever the suite runs without the live leg (found 2026-07-22).
    half = len(mkt.dates) // 2
    tmp_state = C.DATA_DIR / "_gate_resume_tmp.json"
    part1 = online_replay(mkt, budget_fn, caps=caps, kill=kill, end_k=half, finalize=False)
    save_state(part1["state"], half, mkt.syms, path=tmp_state)
    led1 = trades_df(part1["trades"])
    st2, k_next = load_state(mkt.syms, path=tmp_state)
    tmp_state.unlink(missing_ok=True)
    part2 = online_replay(mkt, budget_fn, caps=caps, kill=kill, st=st2, start_k=k_next,
                          finalize=True, use_feed=False)
    resumed_eq = np.concatenate([part1["equity"], part2["equity"]])
    resumed_tr = pd.concat([led1, trades_df(part2["state"].trades)], ignore_index=True)
    resumed_tr = resumed_tr.sort_values(["exit_date", "symbol", "entry_date"]).reset_index(drop=True)
    eq_err_r = float(np.max(np.abs(resumed_eq - online["equity"])))
    same_tr_r = len(resumed_tr) == len(to) and resumed_tr.equals(to)
    g43 = acct_ok and eq_err_r < 1e-9 and same_tr_r
    rep["G4.3"] = {"cost_hand_check_err": float(abs(cost - hand)),
                   "restart_at_k": int(half), "resume_equity_err": eq_err_r,
                   "resume_trades_identical": bool(same_tr_r)}
    check("G4.3 paper-fill accounting + kill/reload/resume == uninterrupted", g43,
          f"  acct_err={abs(cost-hand):.1e} resume_eq_err={eq_err_r:.1e} trades_ident={same_tr_r}")

    # ---- G4.4 risk safety in the ONLINE loop ----
    tight = {"max_positions": 2, "portfolio_risk_budget": C.PER_POSITION_RISK_BUDGET_INR * 1.5,
             "max_notional_frac": C.MAX_POSITION_NOTIONAL_FRAC, "gross_frac": 0.30}
    capped = online_replay(mkt, budget_fn, caps=tight, kill=None, use_feed=False)
    cap_binds = (capped["state"].n_skips > 0
                 and capped["state"].max_concurrent <= tight["max_positions"])
    # injected MTM crash at the max-exposure OOS day (same construction as Phase-3 G3.4a)
    oos_mask = mkt.is_oos == "test"
    expo = online["invested"] / np.maximum(online["equity"], 1.0)
    expo = np.where(oos_mask, expo, -1.0)
    kstar = min(max(int(np.argmax(expo)), 1), len(mkt.dates) - 40)
    shock = max(1.0 - (C.KILL_DD_THRESHOLD + 0.10) / max(expo[kstar], 1e-6), 0.02)
    mm = np.ones(len(mkt.dates)); mm[kstar:kstar + 5] = shock
    crash = online_replay(mkt, budget_fn, caps=caps, kill=kill, mtm_mult=mm, use_feed=False)
    kill_exits = int(sum(t["reason"] == "kill" for t in crash["trades"]))
    kill_ok = kill_exits > 0 and crash["state"].fills_during_halt == 0
    g44 = cap_binds and kill_ok
    rep["G4.4"] = {"tight_cap": {"n_skips": int(capped["state"].n_skips),
                                 "max_concurrent": int(capped["state"].max_concurrent)},
                   "kill": {"kill_exits": kill_exits,
                            "fills_during_halt": int(crash["state"].fills_during_halt),
                            "crash_at": str(mkt.dates[kstar])[:10], "mtm_shock": float(shock)}}
    check("G4.4 online risk safety: cap binds; injected crash -> kill flatten+halt, 0 halt fills",
          g44, f"  skips={capped['state'].n_skips} conc<={capped['state'].max_concurrent} "
          f"kill_exits={kill_exits} halt_fills={crash['state'].fills_during_halt}")

    # ---- G4.5 reproducibility + run-mode/secrets hygiene ----
    rerun = online_replay(mkt, budget_fn, caps=caps, kill=kill, use_feed=False)
    h1, h2 = _hash(online["equity"]), _hash(rerun["equity"])
    mode2, info2 = P10.resolve_run_mode()
    g45 = (h1 == h2) and isinstance(info2["keys_present"], bool)
    rep["G4.5"] = {"equity_hash": h1, "rerun_identical": h1 == h2,
                   "run_mode_stamped": mode2,
                   "secrets_presence_only": {"keys_present": info2["keys_present"],
                                             "token_present": info2["token_present"]}}
    check("G4.5 reproducibility (byte-identical rerun) + presence-only secrets + mode stamp",
          g45, f"  sha256={h1[:16]}... identical={h1 == h2}")

    rep["all_passed_replay"] = ok
    return ok, rep, {"online": online, "batch": batch}


# ================================================================ point-in-time tape
# DECISIONS #002: live-era Kite bars are FROZEN at first-seen. A later Kite restatement
# is LOGGED as a finding but NEVER applied -- so a past decision cannot be silently
# rewritten and replay is reproducible (the bitemporal discipline real desks use).
REV_TOL = 1e-6   # price revision tolerance (rupees); below this = float noise, not a change

_TAPE_COLS = ["open", "high", "low", "close", "volume", "date", "symbol", "split",
              "first_seen_utc"]


def load_live_tape() -> pd.DataFrame:
    """The frozen first-seen live-era bars (empty frame if none yet)."""
    if C.PAPER_LIVE_TAPE_PATH.exists():
        t = pd.read_parquet(C.PAPER_LIVE_TAPE_PATH)
        t["date"] = pd.to_datetime(t["date"])
        return t
    return pd.DataFrame(columns=_TAPE_COLS)


def reconcile_live_bars(fresh, kite_hist, last_panel_date, tape):
    """Freeze-first-seen reconciliation. For every Kite bar newer than the research tape:
      - if that (symbol, date) is already frozen -> KEEP the frozen values, and if Kite
        now differs, record a REVISION (logged, never applied);
      - else -> take Kite's values and FREEZE them (first-seen).
    Returns (rows_for_panel, revisions, updated_tape). rows_for_panel always carries the
    FROZEN value, so the book replays identically run to run."""
    frozen = {(r.symbol, pd.Timestamp(r.date)): r for r in tape.itertuples()}
    now_utc = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows, revisions, appended = [], [], []
    for s in fresh:
        newer = kite_hist[s][kite_hist[s]["date"] > pd.Timestamp(last_panel_date)]
        for r in newer.itertuples():
            key = (s, pd.Timestamp(r.date))
            fr = frozen.get(key)
            if fr is not None:
                for fld in ("open", "high", "low", "close"):
                    fv, kv = float(getattr(fr, fld)), float(getattr(r, fld))
                    if abs(kv - fv) > REV_TOL:
                        revisions.append({"symbol": s, "date": pd.Timestamp(r.date),
                                          "field": fld, "frozen": fv, "kite": kv,
                                          "diff_bp": round((kv / fv - 1) * 1e4, 2)
                                          if fv else float("nan"),
                                          "detected_utc": now_utc})
                rows.append({"open": float(fr.open), "high": float(fr.high),
                             "low": float(fr.low), "close": float(fr.close),
                             "volume": int(fr.volume), "date": pd.Timestamp(r.date),
                             "symbol": s, "split": "test"})
            else:
                row = {"open": float(r.open), "high": float(r.high), "low": float(r.low),
                       "close": float(r.close), "volume": int(r.volume),
                       "date": pd.Timestamp(r.date), "symbol": s, "split": "test"}
                rows.append(row)
                appended.append({**row, "first_seen_utc": now_utc})
    tape2 = (pd.concat([tape, pd.DataFrame(appended)], ignore_index=True)
             if appended else tape.copy())
    return rows, revisions, tape2


def log_revisions(revisions: list) -> int:
    """Append NEW restatements to the durable revisions log (loud on console).

    Content-deduped by (symbol, date, field, kite-value): a persistent restatement --
    Kite reporting the same changed value on every later run -- is recorded ONCE. A
    FURTHER settlement to a new value is a new row (genuinely new information). Returns
    the count actually appended."""
    if not revisions:
        return 0
    old = (pd.read_parquet(C.PAPER_REVISIONS_PATH)
           if C.PAPER_REVISIONS_PATH.exists() else pd.DataFrame())
    seen = set()
    if len(old):
        seen = {(r.symbol, str(pd.Timestamp(r.date))[:10], r.field, round(float(r.kite), 4))
                for r in old.itertuples()}
    fresh = [r for r in revisions
             if (r["symbol"], str(r["date"])[:10], r["field"], round(r["kite"], 4)) not in seen]
    if not fresh:
        return 0
    new = pd.DataFrame(fresh)
    new["date"] = pd.to_datetime(new["date"])
    pd.concat([old, new], ignore_index=True).to_parquet(C.PAPER_REVISIONS_PATH, index=False)
    print(f"  [REVISION] Kite restated {len(fresh)} frozen live-era value(s) "
          f"-- KEPT the first-seen values, logged the change:")
    for r in fresh[:20]:
        print(f"    {r['symbol']:<11} {str(r['date'])[:10]} {r['field']:<5} "
              f"first-seen {r['frozen']:.4f} -> Kite {r['kite']:.4f} "
              f"({r['diff_bp']:+.1f}bp)")
    return len(fresh)


# ================================================================ LIVE leg (G4.6)
def live_smoke(panel, mkt, budget_fn) -> dict:
    """Pull real EOD bars from Kite, validate, run ONE decision on today's completed
    bar, record the intended paper actions, persist state. Data-only; no orders."""
    watch = sorted(panel["symbol"].unique())
    src = P10.LiveKiteSource(watch)
    bars = src.latest_bars()
    fh = P10.DailyFeedHandler()
    today = dt.date.today()

    # PARTIAL-BAR GUARD (2026-07-20 finding: a mis-fired timer ran the decision at
    # 09:30 on the day's RUNNING candle). A bar dated TODAY is only complete after
    # the 15:30 close; before 15:45 local we refuse to decide on it -- an EOD system
    # must never see an unfinished day. (Weekends/holidays: today's date never
    # appears as a bar date, so this guard is inert there.)
    now = dt.datetime.now()
    if any(b.date == today for b in bars.values()) and now.time() < dt.time(15, 45):
        return {"verdict": ("PARTIAL BAR REFUSED: newest bar is dated today and local "
                            "time is before 15:45 IST -- the daily candle is still "
                            "forming. Rerun after the close."),
                "n_symbols": len(watch), "local_time": now.strftime("%H:%M")}

    # freshness + schema per symbol
    last_panel_date = pd.to_datetime(panel["date"]).max().date()
    fresh, stale = [], []
    kite_hist = src.fetch_daily()
    for s in watch:
        b = bars.get(s)
        if b is None or not fh.push(b):
            stale.append(s)
            continue
        age = (today - b.date).days
        (fresh if age <= 1 else stale).append(s)
    dead = fh.is_dead(today)

    # SOURCE-CONSISTENCY (DECISIONS #002 fix): compare Kite against the FROZEN LIVE TAPE
    # on the most recent OVERLAPPING live-era date -- NOT the research tape's stale last
    # date (which never changes, so the old check was blind to the live era and reported
    # 0.0bp forever). This proves Kite still tracks the same series (wrong token/symbol
    # would blow past 2%); genuine day-to-day restatements are caught separately by the
    # freeze-first-seen reconciliation below and logged as revisions.
    tape = load_live_tape()
    src_diff_bp = {}
    for s in fresh:
        kh = kite_hist[s]
        ts = tape[tape["symbol"] == s]
        common = pd.Timestamp(last_panel_date)
        if len(ts):
            cand = sorted(set(ts["date"]) & set(kh["date"]))
            if cand:
                common = cand[-1]
        pref = ts[ts["date"] == common] if len(ts) else pd.DataFrame()
        base = (float(pref["close"].iloc[0]) if len(pref)
                else float(panel[(panel["symbol"] == s)
                                 & (pd.to_datetime(panel["date"]) == common)]["close"].iloc[0])
                if len(panel[(panel["symbol"] == s)
                             & (pd.to_datetime(panel["date"]) == common)]) else None)
        kc = kh[kh["date"] == common]
        if base and len(kc):
            src_diff_bp[s] = (float(kc["close"].iloc[0]) / base - 1.0) * 1e4
    diffs = np.array(list(src_diff_bp.values())) if src_diff_bp else np.array([np.nan])
    src_ok = np.isfinite(diffs).all() and np.nanmax(np.abs(diffs)) < 200.0  # <2% => same series

    result = {"n_symbols": len(watch), "n_fresh": len(fresh), "n_stale": len(stale),
              "stale_symbols": stale, "dead_man": bool(dead),
              "bar_date": str(bars[fresh[0]].date) if fresh else None,
              "source_diff_bp": {k: round(v, 1) for k, v in src_diff_bp.items()},
              "source_consistent_lt_2pct": bool(src_ok)}
    if not fresh or dead:
        result["verdict"] = "NO DECISION: no fresh bars (holiday?) or dead-man tripped"
        return result

    # ---- extend the tape: FREEZE-FIRST-SEEN reconciliation (DECISIONS #002). Every Kite
    # bar newer than the research tape is frozen the first time it is seen; a later
    # restatement is logged, never applied -- so replay is reproducible and a past
    # decision is never silently rewritten. The tape may lag by days (research data not
    # refreshed); we decide on the newest frozen bar. ----
    new_rows, revisions, tape2 = reconcile_live_bars(fresh, kite_hist, last_panel_date, tape)
    if not new_rows:
        result["verdict"] = ("NO NEW BAR: Kite's latest bar == tape's last date "
                             "(run after 15:30 on a trading day)")
        return result
    if len(tape2) != len(tape):
        tape2.to_parquet(C.PAPER_LIVE_TAPE_PATH, index=False)
    n_logged = log_revisions(revisions)
    result["n_revisions_detected"] = len(revisions)
    result["n_revisions_newly_logged"] = n_logged
    result["revisions"] = [{k: (str(v) if k == "date" else v) for k, v in r.items()}
                           for r in revisions[:20]]

    panel2 = pd.concat([panel, pd.DataFrame(new_rows)], ignore_index=True)
    # Walk-forward risk snapshots for EVERY new bar date, PERSISTED -- so each day's
    # decision lineage (tradability + sizing) survives into every later run's era
    # replay. Without this, a previously-decided day loses its snapshot and its armed
    # entries silently vanish (the 2026-07-20 finding). Already-persisted (symbol,
    # date) pairs are skipped, so steady state is ~one fit per symbol per day.
    risk = pd.read_parquet(C.DATA_DIR / "swing_risk_panel.parquet")
    have = set(zip(risk["symbol"], pd.to_datetime(risk["date"])))
    extra = []
    for s in sorted({r["symbol"] for r in new_rows}):
        g = panel2[panel2["symbol"] == s].sort_values("date").reset_index(drop=True)
        close = g["close"].to_numpy(float)
        gdates = pd.to_datetime(g["date"])
        for p in np.where(gdates > pd.Timestamp(last_panel_date))[0]:
            if (s, gdates.iloc[p]) in have:
                continue
            snap = P8.snapshot_asof(close[: p + 1], p - 1)   # decision at close of day p
            if snap is None:
                continue
            snap["symbol"] = s
            snap["date"] = gdates.iloc[p]
            snap["split"] = "test"
            extra.append(snap)
    risk2 = pd.concat([risk, pd.DataFrame(extra)], ignore_index=True)
    risk2["date"] = pd.to_datetime(risk2["date"])
    risk2 = risk2.drop_duplicates(subset=["symbol", "date"], keep="first").reset_index(drop=True)
    risk2.to_parquet(C.DATA_DIR / "swing_risk_panel.parquet", index=False)

    mkt2 = P9.Market(panel2, risk2)
    # LIVE-ERA BOOK: starts FLAT at INITIAL_CAPITAL on PAPER_ERA_START (the user's real
    # budget) -- earlier history is warmup/evidence, not the live book's P&L.
    era_k = int(np.argmax(pd.to_datetime(mkt2.dates) >= pd.Timestamp(C.PAPER_ERA_START)))
    online = online_replay(mkt2, budget_fn, caps=P9.default_caps(), kill=P9.default_kill(),
                           start_k=era_k, finalize=False, use_feed=False)
    st = online["state"]
    k_last = len(mkt2.dates) - 1
    save_state(st, k_last + 1, mkt2.syms)
    led = trades_df(st.trades)
    led.to_parquet(C.PAPER_LEDGER_PATH, index=False)

    result.update({
        "decision_date": str(pd.Timestamp(mkt2.dates[k_last]).date()),
        "n_new_bars": len(new_rows),
        "n_fresh_risk_snapshots": len(extra),
        "open_positions": {mkt2.syms[j]: {"shares": int(p["shares"]),
                                          "sleeve": p["sleeve"],
                                          "entry_px": round(float(p["entry_px"]), 2)}
                           for j, p in st.pos.items()},
        "armed_entries_next_open": {mkt2.syms[j]: {"sleeve": pe["sleeve"],
                                                   "budget_inr": round(float(pe["budget"]), 0)}
                                    for j, pe in st.pend_entry.items()},
        "armed_exits_next_open": {mkt2.syms[j]: r for j, r in st.pend_exit.items()},
        "equity_close": round(float(st.last_equity), 2),
        "state_persisted": str(C.PAPER_STATE_PATH.name),
        "ledger_rows": int(len(led)),
        "verdict": "LIVE SMOKE OK: real EOD bar -> one decision -> paper actions recorded",
    })
    return result


# ================================================================ main
def main() -> int:
    print("=" * 64)
    print("  PHASE 4 - EOD paper-trading harness (REPLAY gate suite + LIVE leg)")
    print("=" * 64)
    run_mode, mode_info = P10.resolve_run_mode()
    print(f"  run_mode = {run_mode}")
    print(f"  {mode_info['note']}")
    print("  loading tape + risk panel, building market tensors + sleeve weights...")
    panel, risk, mkt, budget_fn = setup()

    ok, rep, runs = run_gates(panel, mkt, budget_fn, run_mode)

    # ---- G4.6: the LIVE leg (or a stamped SKIP) ----
    if run_mode == "LIVE":
        print("\n  G4.6 LIVE smoke: pulling real EOD bars from Kite (data-only)...")
        try:
            g46 = live_smoke(panel, mkt, budget_fn)
            g46_pass = "OK" in g46.get("verdict", "")
        except Exception as e:
            g46 = {"verdict": f"LIVE FAILED: {type(e).__name__}: {e}"}
            g46_pass = False
        rep["G4.6"] = g46
        st46 = "PASS" if g46_pass else "FAIL"
        print(f"  [{st46}] G4.6 {g46['verdict']}")
        ok = ok and g46_pass
    else:
        rep["G4.6"] = {"verdict": "SKIPPED: no key/token in env (REPLAY run). "
                                  "Rerun with RUN_MODE=LIVE + secrets loaded after 15:30 IST."}
        print(f"\n  [SKIP] G4.6 {rep['G4.6']['verdict']}")

    # ---- refresh the dashboard after a LIVE decision (report failure never fails the run) ----
    if run_mode == "LIVE":
        try:
            import p11b_swing_report
            print(f"  dashboard refreshed: {p11b_swing_report.build()}")
        except Exception as e:
            print(f"  [warn] dashboard refresh failed: {type(e).__name__}: {e}")

    # ---- persist the live-era bootstrap: a FLAT book at INITIAL_CAPITAL on era start ----
    # GUARD (DECISIONS #007): this REPLAY-only path overwrites the live book + ledger. It
    # is a first-init / deliberate-reset tool, but a stray REPLAY run (e.g. checking the
    # hash) must NOT wipe an ongoing live book. Refuse if an existing state was already
    # saved on/after the era start, unless FORCE_REPLAY_BOOTSTRAP=1 is set explicitly.
    if run_mode != "LIVE":
        existing_live = False
        if C.PAPER_STATE_PATH.exists():
            try:
                saved = json.loads(C.PAPER_STATE_PATH.read_text()).get("saved_utc", "")
                existing_live = str(saved)[:10] >= C.PAPER_ERA_START
            except Exception:
                existing_live = False
        force = os.environ.get("FORCE_REPLAY_BOOTSTRAP") == "1"
        if existing_live and not force:
            print(f"  [skip] live-era bootstrap: an existing live book (saved {saved[:10]}) "
                  f"is preserved. Set FORCE_REPLAY_BOOTSTRAP=1 to reset it deliberately.")
        else:
            era_k = int(np.argmax(pd.to_datetime(mkt.dates) >= pd.Timestamp(C.PAPER_ERA_START)))
            boot = online_replay(mkt, budget_fn, caps=P9.default_caps(), kill=P9.default_kill(),
                                 start_k=era_k, finalize=False, use_feed=False)
            save_state(boot["state"], len(mkt.dates), mkt.syms)
            trades_df(boot["state"].trades).to_parquet(C.PAPER_LEDGER_PATH, index=False)
            print(f"  live-era bootstrap persisted: flat Rs.{C.INITIAL_CAPITAL_INR:,.0f} book "
                  f"stepped from {C.PAPER_ERA_START} (era_k={era_k}); "
                  f"state={C.PAPER_STATE_PATH.name} ledger={C.PAPER_LEDGER_PATH.name}")

    meta = {"phase": 4, "run_mode": run_mode, "mode_info": mode_info,
            "gates": rep, "seed": C.SEED,
            "config": {"stale_max_days": C.FEED_STALE_MAX_DAYS,
                       "kite_interval": C.KITE_INTERVAL,
                       "lookback_days": C.KITE_HISTORICAL_LOOKBACK_DAYS},
            "versions": {"numpy": np.__version__, "pandas": pd.__version__}}
    out = C.ARTIFACTS_DIR / "p4_paper.json"
    out.write_text(json.dumps(meta, indent=2, default=str))
    print(f"  wrote {out}")

    # ---- smoke: state JSON + ledger round-trip ----
    st_r, k_r = load_state(mkt.syms if run_mode != "LIVE" else
                           sorted(panel["symbol"].unique()))
    led_r = pd.read_parquet(C.PAPER_LEDGER_PATH)
    smoke = np.isfinite(st_r.cash) and k_r > 0 and "pnl" in led_r.columns
    print(f"  [{'PASS' if smoke else 'FAIL'}] smoke: paper_state.json + ledger reload "
          f"(cash=Rs.{st_r.cash:,.0f}, k_next={k_r}, ledger={len(led_r):,} trades)")

    ok = ok and smoke
    print(f"\n{'PHASE 4 GATES PASSED (' + run_mode + ')' if ok else 'PHASE 4 GATE FINDING'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
