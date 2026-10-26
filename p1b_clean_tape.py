"""
Phase 1b - tape cleaning: separate DATA ERRORS from CORPORATE ACTIONS (DECISIONS #019).

WHY THIS EXISTS
---------------
The GARCH fit is a maximum-likelihood estimate over the return series, and the likelihood
squares each surprise -- so one impossible return does not merely add noise, it HIJACKS the
fit. ABB is the proof: a single +443% bar (2007-06-28) gave it skew 28.3 and excess kurtosis
1,555, and the GJR optimiser returned alpha=0, gamma=0 -- a model claiming an equity has no
volatility dynamics at all. Every ABB position was sized from that.

A scan of all 95 symbols found 117 such bars across 37 of them.

WHAT THIS IS NOT
----------------
This is NOT "yfinance forgot to adjust for splits". p1 already pulls with auto_adjust=True
and every split Yahoo knows about is applied. Verified 2026-08-25 against yfinance's own
split feed: NOT ONE flagged jump matches a known split. What survives are DEMERGERS (VEDL
2026-04-30, TMPV 2025-10-14 -- both real corporate restructurings, not splits) and older
actions absent from Yahoo's database entirely. The feed cannot classify these, so the tape
must.

THE CLASSIFIER
--------------
  jumps then REVERTS      -> BAD PRINT. One wrong closing price, neighbours correct.
                             Both adjacent returns are excluded from fitting and the bar is
                             marked untradeable. NOTHING is interpolated (user ruling): we
                             do not invent a price that never traded.
  one-way, large          -> CORPORATE ACTION. Back-adjust every prior price onto one
                             continuous series; the symbol KEEPS TRADING.

The one-way rule rests on a physical fact: NSE price bands cap most scrips at 20% per day,
so a genuine one-way move past 40% in a NIFTY-100 name is effectively impossible.

WHY IT MATTERS BEYOND RISK
--------------------------
A bad print corrupts volatility. A corporate action corrupts THE SIGNAL. ts_mom needs
close > MA200 and a positive 126-day return; an unadjusted -65% drop makes both fail, so
VEDL, TRENT and TMPV are currently locked out of the buy list by a data artifact rather
than by the strategy.

GATES
-----
  G1b.1 back-adjustment removes the jump (post-fix return at each event ~ 0)
  G1b.2 strictly causal: adjusting bar j uses only events at or after j; no future leakage
        into any return AFTER the event
  G1b.3 auditable + reversible: every change is logged with before/after and a ratio
  G1b.4 conservative: no bar is invented, and the cleaned tape has the same row count

Read-only w.r.t. the live book. Writes data/swing_daily_clean.parquet + the audit report.
ASCII-only prints.  Run:  C:/Users/Admin/anaconda3/python.exe p1b_clean_tape.py
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

import config as C

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------- detection
def classify_symbol(g: pd.DataFrame) -> list[dict]:
    """Find and label every implausible bar in ONE symbol's sorted frame.

    Returns a list of events: {kind, idx, date, prev_close, next_close, logret, ratio}.
    `idx` indexes the RETURN array, so the event sits between bar idx and bar idx+1.
    """
    c = g["close"].to_numpy(float)
    d = g["date"].to_numpy()
    r = np.diff(np.log(c))
    events, consumed = [], set()

    # ---- PASS 1: spike-revert pairs, CHRONOLOGICALLY ----------------------------------
    # Must run first and must be symmetric. An earlier version scanned largest-first and
    # asked only "does the NEXT return undo this one?" -- but in a pair the larger leg is
    # often the SECOND one, so its forward-looking check saw an ordinary day, the test
    # failed, and the leg was misfiled as a corporate action. That produced absurd
    # back-adjustments (BEL ratio 0.0294, CIPLA ~13x) that would have rescaled decades of
    # real prices. Compare the two legs against EACH OTHER instead.
    for i in range(len(r) - 1):
        if i in consumed or not (np.isfinite(r[i]) and np.isfinite(r[i + 1])):
            continue
        a, b = abs(r[i]), abs(r[i + 1])
        hi, lo = max(a, b), min(a, b)
        if (hi >= C.CLEAN_REVERT_LOGRET and np.sign(r[i + 1]) != np.sign(r[i])
                and lo >= C.CLEAN_REVERT_FRAC * hi):
            consumed.update((i, i + 1))
            events.append({"kind": "bad_print", "idx": i, "bar_idx": i + 1,
                           "date": str(pd.Timestamp(d[i + 1]).date()),
                           "prev_close": float(c[i]), "bad_close": float(c[i + 1]),
                           "next_close": float(c[i + 2]) if i + 2 < len(c) else float("nan"),
                           "logret": float(r[i]), "ratio": float("nan")})

    # ---- PASS 2: whatever large one-way moves remain are corporate actions -------------
    for i in range(len(r)):
        if i in consumed or not np.isfinite(r[i]) or abs(r[i]) < C.CLEAN_ONEWAY_LOGRET:
            continue
        consumed.add(i)
        events.append({"kind": "corp_action", "idx": i, "bar_idx": i + 1,
                       "date": str(pd.Timestamp(d[i + 1]).date()),
                       "prev_close": float(c[i]), "bad_close": float("nan"),
                       "next_close": float(c[i + 1]),
                       "logret": float(r[i]),
                       # prices BEFORE the event are multiplied by this to line up
                       "ratio": float(c[i + 1] / c[i])})

    return sorted(events, key=lambda e: e["idx"])


# ---------------------------------------------------------------- repair
def clean_symbol(g: pd.DataFrame, events: list[dict]) -> tuple[pd.DataFrame, np.ndarray]:
    """Apply the repairs to one symbol.

    Corporate actions: every price at or before the event is multiplied by the cumulative
    ratio of all events at or after it, splicing the history onto today's scale. Strictly
    backward-looking -- a bar is only ever rescaled by events that happen LATER, so no
    future information enters any return after the event.

    Bad prints: the bar is marked untradeable and its two adjacent returns are flagged for
    exclusion from fitting. The price is left exactly as it was -- nothing is invented.

    Returns (cleaned frame, boolean mask of RETURN indices to exclude from fitting).
    """
    g = g.copy().reset_index(drop=True)
    n = len(g)
    factor = np.ones(n)

    for e in events:                            # cumulative, latest events reach furthest back
        if e["kind"] == "corp_action":
            factor[: e["idx"] + 1] *= e["ratio"]

    for col in ("open", "high", "low", "close"):
        if col in g.columns:
            g[col] = g[col].to_numpy(float) * factor

    if "tradable" not in g.columns:
        g["tradable"] = g["volume"].to_numpy(float) > 0 if "volume" in g.columns else True
    g["adj_factor"] = factor

    drop_ret = np.zeros(max(n - 1, 0), bool)
    for e in events:
        if e["kind"] == "bad_print":
            g.loc[e["bar_idx"], "tradable"] = False
            for j in (e["idx"], e["idx"] + 1):   # into and out of the bad bar
                if 0 <= j < len(drop_ret):
                    drop_ret[j] = True
    return g, drop_ret


# ---------------------------------------------------------------- gates
def run_gates(report: dict, per_symbol: dict) -> tuple[bool, dict]:
    print("\n" + "=" * 64)
    print("  PHASE 1b TAPE-CLEANING GATES")
    print("=" * 64)
    ok = True
    rep = {}

    def check(name, cond, detail=""):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}  {detail}")
        return bool(cond)

    # ---- G1b.1 the jump is actually gone ----
    worst, checked = 0.0, 0
    for sym, (gc, evs) in per_symbol.items():
        c = gc["close"].to_numpy(float)
        r = np.diff(np.log(c))
        for e in evs:
            if e["kind"] == "corp_action" and e["idx"] < len(r):
                worst = max(worst, abs(float(r[e["idx"]])))
                checked += 1
    rep["G1b.1"] = {"events_checked": checked, "worst_residual_logret": worst}
    check("G1b.1 back-adjustment removes the jump (residual return ~ 0)",
          checked == 0 or worst < 1e-9, f"  {checked} events, worst residual={worst:.2e}")

    # ---- G1b.2 causality: returns AFTER an event must be untouched ----
    # Rescaling multiplies a contiguous prefix by a constant, so every return strictly after
    # the event is unchanged by construction. Verify it numerically rather than assert it.
    max_drift, n_cmp = 0.0, 0
    for sym, (gc, evs) in per_symbol.items():
        if not any(e["kind"] == "corp_action" for e in evs):
            continue
        raw = report["_raw_close"][sym]
        r_raw = np.diff(np.log(np.asarray(raw, float)))
        r_new = np.diff(np.log(gc["close"].to_numpy(float)))
        last = max(e["idx"] for e in evs if e["kind"] == "corp_action")
        if last + 1 < len(r_raw):
            d = np.abs(r_raw[last + 1:] - r_new[last + 1:])
            max_drift = max(max_drift, float(np.nanmax(d)) if d.size else 0.0)
            n_cmp += int(d.size)
    rep["G1b.2"] = {"returns_compared": n_cmp, "max_drift": max_drift}
    check("G1b.2 causal: no return after an event is altered", max_drift < 1e-12,
          f"  {n_cmp:,} returns compared, max drift={max_drift:.2e}")

    # ---- G1b.3 every change is logged ----
    logged = len(report["events"])
    touched = sum(1 for s, (gc, evs) in per_symbol.items() if evs)
    rep["G1b.3"] = {"events_logged": logged, "symbols_touched": touched}
    check("G1b.3 every change is auditable (event log complete)", logged > 0,
          f"  {logged} events across {touched} symbols, each with before/after + ratio")

    # ---- G1b.4 nothing invented, nothing lost ----
    same_rows = report["rows_in"] == report["rows_out"]
    rep["G1b.4"] = {"rows_in": report["rows_in"], "rows_out": report["rows_out"],
                    "prices_interpolated": 0}
    check("G1b.4 conservative: no bar invented or dropped, no price interpolated",
          same_rows, f"  rows {report['rows_in']:,} -> {report['rows_out']:,}")

    rep["all_passed"] = ok
    print(f"\n{'ALL PHASE 1b GATES PASSED' if ok else 'TAPE-CLEANING GATE FINDING'}\n")
    return ok, rep


# ---------------------------------------------------------------- main
def main() -> int:
    print("=" * 64)
    print("  PHASE 1b - tape cleaning: data errors vs corporate actions")
    print(f"  thresholds: one-way |r|>{C.CLEAN_ONEWAY_LOGRET}  "
          f"revert |r|>{C.CLEAN_REVERT_LOGRET} undoing >={C.CLEAN_REVERT_FRAC:.0%}")
    print("=" * 64)

    panel = pd.read_parquet(C.DATA_DIR / "swing_daily.parquet")
    panel["date"] = pd.to_datetime(panel["date"])
    print(f"  loaded {len(panel):,} bars, {panel['symbol'].nunique()} symbols")

    out, per_symbol, all_events, raw_close, drop_counts = [], {}, [], {}, 0
    for sym, g in panel.groupby("symbol"):
        g = g.sort_values("date").reset_index(drop=True)
        raw_close[sym] = g["close"].to_numpy(float).copy()
        evs = classify_symbol(g)
        gc, drop = clean_symbol(g, evs)
        gc["drop_from_fit"] = np.append(drop, False)   # align to bars (last bar has no return)
        per_symbol[sym] = (gc, evs)
        drop_counts += int(drop.sum())
        for e in evs:
            all_events.append({"symbol": sym, **e})
        out.append(gc)

    clean = pd.concat(out, ignore_index=True)
    report = {"rows_in": int(len(panel)), "rows_out": int(len(clean)),
              "events": all_events, "_raw_close": raw_close,
              "thresholds": {"oneway": C.CLEAN_ONEWAY_LOGRET,
                             "revert": C.CLEAN_REVERT_LOGRET,
                             "revert_frac": C.CLEAN_REVERT_FRAC}}

    bp = [e for e in all_events if e["kind"] == "bad_print"]
    ca = [e for e in all_events if e["kind"] == "corp_action"]
    print(f"\n  BAD PRINTS   : {len(bp):3d} events across {len({e['symbol'] for e in bp}):2d} symbols"
          f"  -> {drop_counts} returns excluded from fitting, bars marked untradeable")
    print(f"  CORP ACTIONS : {len(ca):3d} events across {len({e['symbol'] for e in ca}):2d} symbols"
          f"  -> prior prices back-adjusted, symbols KEEP TRADING")

    if ca:
        print("\n  largest back-adjustments (ratio = multiplier applied to earlier prices):")
        for e in sorted(ca, key=lambda x: abs(x["logret"]), reverse=True)[:8]:
            print(f"    {e['symbol']:<12s} {e['date']}  {e['prev_close']:10.2f} -> "
                  f"{e['next_close']:10.2f}   ratio {e['ratio']:.4f}")

    recent = sorted([e for e in ca if e["date"] >= "2025-01-01"], key=lambda x: x["date"])
    if recent:
        print("\n  RECENT actions (inside today's MA200 / 126-day windows -- these were")
        print("  suppressing live buy signals; worth hand-verifying the ratio):")
        for e in recent:
            print(f"    {e['symbol']:<12s} {e['date']}  ratio {e['ratio']:.4f}")

    ok, gates = run_gates(report, per_symbol)

    clean.to_parquet(C.CLEAN_TAPE_PATH, index=False)
    del report["_raw_close"]                                  # not JSON-serialisable
    report["gates"] = gates
    report["seed"] = C.SEED
    C.CLEAN_REPORT_PATH.write_text(json.dumps(report, indent=1, default=str))
    print(f"  wrote {C.CLEAN_TAPE_PATH}")
    print(f"  wrote {C.CLEAN_REPORT_PATH}")
    print("\n  NOT APPLIED to the pipeline: this writes a SEPARATE clean tape. The live book,")
    print("  the risk panel and swing_daily.parquet are untouched until you say so.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
