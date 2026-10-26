"""
Phase 3 (part 1/2) - Per-stock GARCH-EVT risk engine + VaR position sizing.

WHAT THIS IS
------------
Phase 2 proved a per-TRADE edge. A per-trade edge is not a P&L. To turn edges into a
BOOK we must answer, for every stock every day: "how much can one share lose over the
holding horizon?" -- then size so each position risks the same rupees.

We port the GARCH-EVT path from the Desktop/VaR Model engine (McNeil-Frey-Embrechts):
  1. GJR-GARCH(1,1) volatility filter (asymmetric -- crashes raise vol more than rallies)
  2. EVT / Generalized-Pareto tail fit on the standardized residuals (tails only)
  3. horizon aggregation of the conditional vol -> a 99% loss over ~12 trading days
The GJR likelihood recursion is numba-accelerated so a walk-forward, refit-on-a-schedule
panel over 20 stocks x ~30 years is a couple of minutes, not hours.

WALK-FORWARD, NO LOOK-AHEAD (the cardinal rule)
-----------------------------------------------
A risk snapshot for a decision made at the CLOSE of day i uses only returns[:i+1]. Params
are refit on a DETERMINISTIC cadence (every GARCH_REFIT_EVERY days, expanding prior-only
window); between refits the variance recursion rolls forward on realized returns (still
causal). The deterministic cadence is what makes Gate G3.3 testable: an independent
"as-of day i" recompute on the truncated series must reproduce the panel byte-for-byte.

VaR CONVENTION
--------------
Zero-drift (config.VAR_DRIFT_ZERO): the signal's positive edge is kept OUT of the risk
estimate (conservative, standard). var_frac = sigma_H * z_q ; es_frac = sigma_H * es_zq,
where z_q / es_zq are the EVT loss-quantile / expected-shortfall of the standardized
residual and sigma_H is the GARCH multi-step horizon volatility. Both are positive and
coherent (ES >= VaR) by construction for xi < 1.

GATES
-----
  G3.1 sizing invariant : shares * var_per_unit <= budget on EVERY gated position (unit + panel)
  G3.2 EVT coherence    : on every TRADABLE snapshot ES >= VaR > 0, GARCH converged, n_u >= min
  G3.3 causality        : as-of-day recompute on truncated data reproduces the panel exactly
  G3.5 reproducibility  : fixed seed -> byte-identical risk panel (hash check)

ASCII-only prints. Run:  C:/Users/Admin/anaconda3/python.exe p8_swing_risk.py
Writes: data/swing_risk_panel.parquet, artifacts/p3_risk.json
"""
from __future__ import annotations

import hashlib
import json
import warnings

import numpy as np
import pandas as pd
from numba import njit
from scipy import optimize, stats

import config as C

warnings.filterwarnings("ignore")
np.random.seed(C.SEED)

H = int(C.RISK_HORIZON_DAYS)
Q = float(C.VAR_Q)
MIN_WIN = int(C.GARCH_MIN_WINDOW)
REFIT_EVERY = int(C.GARCH_REFIT_EVERY)
MIN_EXC = int(C.EVT_MIN_EXCEEDANCES)
THRESH_PCTS = list(C.EVT_THRESHOLD_PCTS)
ZBURN = 50   # drop the first residuals (recursion warm-up) before the EVT tail fit


# ================================================================ GJR-GARCH (numba)
@njit(cache=True)
def _gjr_var_path(mu, omega, alpha, gamma, beta, r):
    """Conditional variance path h_t for GJR-GARCH(1,1). h_t uses eps up to t-1 (causal)."""
    T = r.shape[0]
    h = np.empty(T)
    v0 = np.var(r)
    h[0] = v0
    for t in range(1, T):
        e = r[t - 1] - mu
        neg = 1.0 if e < 0.0 else 0.0
        ht = omega + alpha * e * e + gamma * e * e * neg + beta * h[t - 1]
        if ht < 1e-14:
            ht = 1e-14
        h[t] = ht
    return h


@njit(cache=True)
def _gjr_negll(mu, omega, alpha, gamma, beta, r):
    """Negative Gaussian QMLE log-likelihood for GJR-GARCH(1,1)."""
    if omega <= 0.0 or alpha < 0.0 or beta < 0.0 or (alpha + gamma) < 0.0 \
       or (alpha + gamma / 2.0 + beta) >= 0.9999:
        return 1e10
    h = _gjr_var_path(mu, omega, alpha, gamma, beta, r)
    eps = r - mu
    ll = -0.5 * np.sum(np.log(2.0 * np.pi * h) + eps * eps / h)
    return -ll


def _negll_vec(p, r):
    return _gjr_negll(p[0], p[1], p[2], p[3], p[4], r)


def fit_gjr(r: np.ndarray) -> dict:
    """Fit GJR-GARCH(1,1) by QMLE (percent-space for optimizer stability). Returns params
    in RETURN space plus a converged flag and persistence. Ported from var_phase1."""
    r_pct = r * 100.0
    v = np.var(r_pct)
    x0 = np.array([np.mean(r_pct), v * 0.05, 0.03, 0.10, 0.88])
    bnds = [(None, None), (1e-12, None), (1e-6, 0.5), (0.0, 0.5), (0.3, 0.999)]
    res = optimize.minimize(_negll_vec, x0, args=(r_pct,), method="L-BFGS-B", bounds=bnds)
    p = res.x.copy()
    p[0] /= 100.0        # mu: percent -> fraction
    p[1] /= 10000.0      # omega: percent^2 -> fraction^2
    mu, omega, alpha, gamma, beta = p
    persistence = alpha + gamma / 2.0 + beta
    converged = bool(res.success) and not np.allclose(res.x, x0) and persistence < 0.9999
    return {"params": p, "converged": converged, "persistence": float(persistence)}


# ================================================================ EVT / GPD tail
def fit_gpd(z: np.ndarray) -> dict:
    """Fit a GPD to the LOSS tail of standardized residuals (losses = -z). Chooses the
    lowest candidate threshold whose exceedance count clears EVT_MIN_EXCEEDANCES with a
    stable shape xi. Returns u, xi, sigma, n_u, n_total. Ported from var_phase1."""
    losses = -np.asarray(z, float)
    n_total = len(losses)
    fits = []
    for pct in THRESH_PCTS:
        u = float(np.quantile(losses, pct))
        exc = losses[losses > u] - u
        if len(exc) < 20:
            continue
        xi, _, sigma = stats.genpareto.fit(exc, floc=0)
        se_xi = (1 + xi) * np.sqrt(max(1 + xi, 0.0) / len(exc)) if xi > -0.5 else np.nan
        fits.append({"pct": pct, "u": u, "n_u": int(len(exc)),
                     "xi": float(xi), "sigma": float(sigma), "se_xi": float(se_xi)})
    if not fits:
        return {"u": np.nan, "xi": np.nan, "sigma": np.nan, "n_u": 0, "n_total": n_total}
    chosen = None
    for i, f in enumerate(fits):
        if f["n_u"] < MIN_EXC:
            continue
        if i + 1 < len(fits):
            if abs(f["xi"] - fits[i + 1]["xi"]) <= max(f["se_xi"], 0.05):
                chosen = f
                break
        else:
            chosen = f
    if chosen is None:                       # none stable+adequate -> most data available
        chosen = max(fits, key=lambda f: f["n_u"])
    chosen = dict(chosen)
    chosen["n_total"] = n_total
    return chosen


def evt_loss_quantile(gpd: dict, q: float) -> float:
    """q-quantile of the LOSS of a standardized residual, from the GPD tail (positive)."""
    u, xi, sigma, n_u, n = gpd["u"], gpd["xi"], gpd["sigma"], gpd["n_u"], gpd["n_total"]
    tail_frac = (1.0 - q) * n / n_u
    if abs(xi) < 1e-8:
        return u - sigma * np.log(tail_frac)
    return u + (sigma / xi) * (tail_frac ** (-xi) - 1.0)


def evt_es(gpd: dict, z_q: float) -> float:
    """Expected shortfall of the standardized-residual loss at the same level (McNeil)."""
    xi, sigma, u = gpd["xi"], gpd["sigma"], gpd["u"]
    if xi >= 1.0:                            # infinite-mean tail: fall back to a floor > VaR
        return z_q * 1.5
    return z_q / (1.0 - xi) + (sigma - xi * u) / (1.0 - xi)


# ================================================================ horizon aggregation
def horizon_sigma(params: np.ndarray, h_i: float, r_i: float, horizon: int) -> float:
    """GARCH multi-step: total return volatility over `horizon` days ahead of day i.
    sigma_H = sqrt( sum_{k=1..H} E[h_{i+k}] ), symmetric-innovation expectation for k>=2."""
    mu, omega, alpha, gamma, beta = params
    e = r_i - mu
    neg = 1.0 if e < 0.0 else 0.0
    h_next = omega + (alpha + gamma * neg) * e * e + beta * h_i     # var of day i+1
    phi = alpha + gamma / 2.0 + beta
    total = h_next
    hk = h_next
    for _ in range(2, horizon + 1):
        hk = omega + phi * hk
        total += hk
    return float(np.sqrt(max(total, 1e-18)))


# ================================================================ per-symbol panel
def _log_returns(close: np.ndarray) -> np.ndarray:
    return np.log(close[1:] / close[:-1])


def fit_returns(close: np.ndarray) -> np.ndarray:
    """Return series used for FITTING, with bad-print pairs folded out (DECISIONS #019).

    A bad print is one wrong closing price between two correct ones, so it produces a
    matched pair of monstrous opposite returns (BAJFINANCE 2005: +4.696 then -4.695 in
    logs). Left alone they dominate the likelihood: ABB's single bad bar collapsed its GJR
    fit to alpha=0, gamma=0.

    A GARCH recursion cannot skip an observation -- h_t is defined from h_{t-1} -- so the
    pair is FOLDED into the move it actually spans: r[i] becomes r[i] + r[i+1] (exactly
    log(close[i+2]/close[i]), the true two-day return) and r[i+1] becomes 0. Nothing is
    invented; the cumulative price path is preserved exactly and the two impossible
    observations are re-attributed to the one real move between them.

    DERIVED FROM PRICES ALONE, deliberately. An earlier version read a `drop_from_fit`
    column, which only the panel builder had -- the as-of recompute in snapshot_asof takes
    a bare price array, so the two paths disagreed and gate G3.3 caught it (2/40 probes,
    worst diff 31.7). Both paths now call THIS function, so they cannot drift apart, and
    the repair depends only on close[:i+2] -- strictly causal by construction.
    """
    r = _log_returns(close)
    if not getattr(C, "USE_CLEAN_TAPE", False) or len(r) < 2:
        return r
    r = r.copy()
    i = 0
    while i < len(r) - 1:
        a, b = abs(r[i]), abs(r[i + 1])
        hi, lo = (a, b) if a >= b else (b, a)
        if (hi >= C.CLEAN_REVERT_LOGRET and np.sign(r[i + 1]) != np.sign(r[i])
                and lo >= C.CLEAN_REVERT_FRAC * hi):
            r[i] = r[i] + r[i + 1]
            r[i + 1] = 0.0
            i += 2
        else:
            i += 1
    return r


def _refit_index(i: int) -> int:
    """Deterministic last-refit index at decision day i (>= MIN_WIN). Cadence anchored at
    MIN_WIN so an independent as-of recompute lands on the identical fit window."""
    return MIN_WIN + ((i - MIN_WIN) // REFIT_EVERY) * REFIT_EVERY


def _fit_asof(r: np.ndarray, refit_idx: int) -> dict:
    """Fit GJR + GPD on the expanding prior-only window r[:refit_idx+1]."""
    win = r[: refit_idx + 1]
    g = fit_gjr(win)
    h = _gjr_var_path(*g["params"], win)
    z = (win - g["params"][0]) / np.sqrt(h)
    gpd = fit_gpd(z[ZBURN:])
    return {"gjr": g, "gpd": gpd}


def snapshot_asof(close: np.ndarray, i: int) -> dict | None:
    """Risk snapshot for a decision at the CLOSE of return-index i, using ONLY close[:i+2]
    (i indexes the RETURN series; close is one longer). Independent, from-scratch path used
    both by the panel builder's logic and by the causality gate."""
    r = fit_returns(close)
    if i < MIN_WIN or i >= len(r):
        return None
    refit_idx = _refit_index(i)
    fit = _fit_asof(r, refit_idx)
    return _finish_snapshot(fit, r, i, float(close[i + 1]))


def _finish_snapshot(fit: dict, r: np.ndarray, i: int, ref_px: float) -> dict:
    g, gpd = fit["gjr"], fit["gpd"]
    evt_ok = (gpd["n_u"] >= MIN_EXC) and np.isfinite(gpd["xi"]) and (gpd["xi"] < 1.0)
    h_path = _gjr_var_path(*g["params"], r[: i + 1])
    sig_h = horizon_sigma(g["params"], float(h_path[i]), float(r[i]), H)
    if evt_ok:
        z_q = evt_loss_quantile(gpd, Q)
        es_zq = evt_es(gpd, z_q)
    else:
        z_q = es_zq = np.nan
    var_frac = sig_h * z_q
    es_frac = sig_h * es_zq
    tradable = bool(evt_ok and g["converged"] and np.isfinite(var_frac) and var_frac > 0)
    return {
        "sigma_1d": float(np.sqrt(h_path[i])), "sigma_h": float(sig_h),
        "z_q": float(z_q), "es_zq": float(es_zq),
        "var_frac": float(var_frac), "es_frac": float(es_frac),
        "var_per_unit": float(ref_px * var_frac), "es_per_unit": float(ref_px * es_frac),
        "ref_px": float(ref_px), "xi": float(gpd["xi"]), "n_u": int(gpd["n_u"]),
        "converged": bool(g["converged"]), "persistence": float(g["persistence"]),
        "evt_ok": bool(evt_ok), "tradable": tradable,
    }


def build_symbol_panel(g: pd.DataFrame) -> pd.DataFrame:
    """Walk-forward risk snapshots for one symbol. Refits on the deterministic cadence and
    reuses each fit across its segment; the variance recursion is causal so h_full[i] equals
    an as-of-i recompute. ref_px = close of the decision day (sizing reference)."""
    g = g.sort_values("date").reset_index(drop=True)
    close = g["close"].to_numpy(float)
    dates = g["date"].to_numpy()
    split = g["split"].to_numpy()
    r = fit_returns(close)          # same function the as-of gate uses -- cannot drift (G3.3)
    N = len(r)
    rows = []
    if N <= MIN_WIN:
        return pd.DataFrame(rows)
    refit_starts = list(range(MIN_WIN, N, REFIT_EVERY))
    for s, refit_idx in enumerate(refit_starts):
        seg_end = refit_starts[s + 1] if s + 1 < len(refit_starts) else N
        fit = _fit_asof(r, refit_idx)
        h_full = _gjr_var_path(*fit["gjr"]["params"], r[:seg_end])   # once per segment
        for i in range(refit_idx, seg_end):
            # return-index i is the LAST available return; the decision is made at the
            # close of day d = i+1 (that day's return r[i] is known), sized on close[d];
            # the fill is the next open, which p9 resolves from its own price arrays.
            d = i + 1
            snap = _finish_snapshot_fast(fit, r, i, float(close[d]), float(h_full[i]))
            snap["symbol"] = g["symbol"].iloc[0]
            snap["date"] = dates[d]            # decision CLOSE date -> join key for p9
            snap["split"] = str(split[d])
            rows.append(snap)
    return pd.DataFrame(rows)


def _finish_snapshot_fast(fit, r, i, ref_px, h_i):
    """Same as _finish_snapshot but reuses a precomputed h_i from the segment recursion."""
    g, gpd = fit["gjr"], fit["gpd"]
    evt_ok = (gpd["n_u"] >= MIN_EXC) and np.isfinite(gpd["xi"]) and (gpd["xi"] < 1.0)
    sig_h = horizon_sigma(g["params"], h_i, float(r[i]), H)
    if evt_ok:
        z_q = evt_loss_quantile(gpd, Q)
        es_zq = evt_es(gpd, z_q)
    else:
        z_q = es_zq = np.nan
    var_frac = sig_h * z_q
    es_frac = sig_h * es_zq
    tradable = bool(evt_ok and g["converged"] and np.isfinite(var_frac) and var_frac > 0)
    return {
        "sigma_1d": float(np.sqrt(h_i)), "sigma_h": float(sig_h),
        "z_q": float(z_q), "es_zq": float(es_zq),
        "var_frac": float(var_frac), "es_frac": float(es_frac),
        "var_per_unit": float(ref_px * var_frac), "es_per_unit": float(ref_px * es_frac),
        "ref_px": float(ref_px), "xi": float(gpd["xi"]), "n_u": int(gpd["n_u"]),
        "converged": bool(g["converged"]), "persistence": float(g["persistence"]),
        "evt_ok": bool(evt_ok), "tradable": tradable,
    }


def build_risk_panel(panel: pd.DataFrame) -> pd.DataFrame:
    out = []
    for sym, g in panel.groupby("symbol"):
        sp = build_symbol_panel(g)
        n_tr = int(sp["tradable"].sum()) if len(sp) else 0
        print(f"    {sym:12s} {len(sp):5d} snapshots  {n_tr:5d} tradable")
        out.append(sp)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ================================================================ position sizing
def size_position(var_per_unit: float, ref_px: float, equity: float,
                  per_pos_budget: float) -> int:
    """Shares so that shares*var_per_unit <= per_pos_budget (Gate G3.1) AND the notional
    stays under MAX_POSITION_NOTIONAL_FRAC of equity. Floor -> the invariant can only bind
    below, never above. Returns 0 if a single share already breaches the risk budget."""
    if not (var_per_unit > 0) or not (ref_px > 0):
        return 0
    by_risk = int(np.floor(per_pos_budget / var_per_unit))
    by_notional = int(np.floor((C.MAX_POSITION_NOTIONAL_FRAC * equity) / ref_px))
    return max(0, min(by_risk, by_notional))


# ================================================================ gates
def _hash_panel(df: pd.DataFrame) -> str:
    cols = ["symbol", "date", "var_frac", "es_frac", "var_per_unit", "es_per_unit"]
    b = pd.util.hash_pandas_object(df[cols].round(12), index=False).values.tobytes()
    return hashlib.sha256(b).hexdigest()


def run_gates(risk: pd.DataFrame, panel: pd.DataFrame) -> tuple[bool, dict]:
    print("\n" + "=" * 64)
    print("  PHASE 3 RISK-ENGINE GATES")
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

    tr = risk[risk["tradable"]].copy()

    # ---- G3.1 sizing invariant (unit hand-check + across every tradable snapshot) ----
    # Unit: a fixed case must satisfy shares*var_per_unit <= budget with shares>0.
    unit_shares = size_position(50.0, 1000.0, C.INITIAL_CAPITAL_INR, C.PER_POSITION_RISK_BUDGET_INR)
    unit_ok = unit_shares > 0 and unit_shares * 50.0 <= C.PER_POSITION_RISK_BUDGET_INR + 1e-9
    # Panel: size every tradable snapshot and assert the invariant holds everywhere.
    vpu = tr["var_per_unit"].to_numpy()
    px = tr["ref_px"].to_numpy()
    shares = np.array([size_position(v, p, C.INITIAL_CAPITAL_INR, C.PER_POSITION_RISK_BUDGET_INR)
                       for v, p in zip(vpu, px)])
    risk_used = shares * vpu
    invariant = bool(np.all(risk_used <= C.PER_POSITION_RISK_BUDGET_INR + 1e-6))
    n_sizeable = int((shares > 0).sum())
    rep["G3.1"] = {"unit_shares": int(unit_shares), "unit_ok": unit_ok,
                   "n_tradable": int(len(tr)), "n_sizeable": n_sizeable,
                   "max_risk_used": float(risk_used.max()) if len(risk_used) else 0.0,
                   "budget": C.PER_POSITION_RISK_BUDGET_INR}
    check("G3.1 sizing invariant shares*var_per_unit <= budget (unit + all snapshots)",
          unit_ok and invariant,
          f"  n_sizeable={n_sizeable}/{len(tr)} max_used=Rs.{risk_used.max() if len(risk_used) else 0:,.0f}"
          f" <= Rs.{C.PER_POSITION_RISK_BUDGET_INR:,.0f}")

    # ---- G3.2 EVT coherence on every tradable snapshot ----
    es_ge_var = bool(np.all(tr["es_frac"].to_numpy() >= tr["var_frac"].to_numpy() - 1e-12))
    var_pos = bool(np.all(tr["var_frac"].to_numpy() > 0))
    conv = bool(np.all(tr["converged"].to_numpy()))
    exc_ok = bool(np.all(tr["n_u"].to_numpy() >= MIN_EXC))
    cover = float(len(tr) / max(len(risk), 1))
    rep["G3.2"] = {"es_ge_var": es_ge_var, "var_pos": var_pos, "garch_converged": conv,
                   "min_exceedances_ok": exc_ok, "min_n_u": int(tr["n_u"].min()) if len(tr) else 0,
                   "xi_median": float(tr["xi"].median()) if len(tr) else float("nan"),
                   "tradable_coverage": cover}
    check("G3.2 EVT coherence (ES>=VaR>0, GARCH converged, n_u>=min) on tradable snapshots",
          es_ge_var and var_pos and conv and exc_ok,
          f"  min_n_u={rep['G3.2']['min_n_u']} xi_med={rep['G3.2']['xi_median']:+.3f} "
          f"coverage={cover*100:.0f}%")

    # ---- G3.3 walk-forward causality: as-of recompute on truncated data == panel ----
    # For a sampled tradable snapshot with decision-close date D (close-index d), recompute
    # the risk from scratch on close[:d+1] ONLY. A causal engine reproduces it exactly; any
    # future-data leak would change the number.
    rng = np.random.default_rng(C.SEED)
    probes, mism = 0, 0
    worst = 0.0
    for sym in rng.permutation(sorted(tr["symbol"].unique())):
        sub = tr[tr["symbol"] == sym]
        gg = panel[panel["symbol"] == sym].sort_values("date").reset_index(drop=True)
        close = gg["close"].to_numpy(float)
        date_to_pos = {np.datetime64(dd): k for k, dd in enumerate(gg["date"].to_numpy())}
        pick = sub.sample(min(4, len(sub)), random_state=int(rng.integers(1 << 30)))
        for _, row in pick.iterrows():
            d = date_to_pos[np.datetime64(row["date"])]   # close-index of the decision day
            snap = snapshot_asof(close[: d + 1], d - 1)    # STRICTLY truncated to close[:d+1]
            if snap is None:
                continue
            probes += 1
            diff = abs(snap["var_per_unit"] - row["var_per_unit"]) \
                + abs(snap["es_per_unit"] - row["es_per_unit"])
            worst = max(worst, diff)
            if diff > 1e-6:
                mism += 1
        if probes >= 40:
            break
    rep["G3.3"] = {"probes": probes, "mismatches": mism, "worst_abs_diff": float(worst)}
    check("G3.3 walk-forward causality (as-of recompute on truncated data reproduces panel)",
          probes > 0 and mism == 0, f"  probes={probes} mismatches={mism} worst={worst:.2e}")

    # ---- G3.5 (engine-side) reproducibility: rebuild one symbol, identical hash ----
    one = panel[panel["symbol"] == sorted(panel["symbol"].unique())[0]]
    h1 = _hash_panel(build_symbol_panel(one))
    h2 = _hash_panel(build_symbol_panel(one))
    rep["G3.5_engine"] = {"hash": h1, "identical": h1 == h2}
    check("G3.5 reproducibility (rebuild risk panel -> identical hash)", h1 == h2,
          f"  sha256={h1[:16]}...")

    rep["all_passed"] = ok
    print(f"\n{'ALL PHASE 3 RISK GATES PASSED' if ok else 'RISK GATE FINDING (see above)'}\n")
    return ok, rep


# ================================================================ main
def main() -> int:
    print("=" * 64)
    print("  PHASE 3 - per-stock GARCH-EVT risk engine + VaR sizing")
    print(f"  horizon={H}d  q={Q:.2f}  refit_every={REFIT_EVERY}  min_win={MIN_WIN}")
    print("=" * 64)
    src = (C.CLEAN_TAPE_PATH if (getattr(C, "USE_CLEAN_TAPE", False)
                                 and C.CLEAN_TAPE_PATH.exists())
           else C.DATA_DIR / "swing_daily.parquet")
    panel = pd.read_parquet(src)
    print(f"  tape: {src.name}" + ("  (CLEANED - DECISIONS #019)" if src == C.CLEAN_TAPE_PATH
                                   else "  (original)"))
    if "drop_from_fit" in panel.columns:
        print(f"  bad-print returns folded out of the fit: {int(panel['drop_from_fit'].sum())}")
    print(f"  loaded {len(panel):,} bars, {panel['symbol'].nunique()} symbols")
    print("\n  building walk-forward risk snapshots (numba GJR + EVT/GPD):")
    risk = build_risk_panel(panel)
    tr = risk[risk["tradable"]]
    print(f"\n  {len(risk):,} snapshots total, {len(tr):,} tradable "
          f"({(risk['split'] == 'test').sum():,} in OOS window)")
    if len(tr):
        print(f"  median var_frac (99%/{H}d) = {tr['var_frac'].median()*100:.2f}% of price | "
              f"median es_frac = {tr['es_frac'].median()*100:.2f}%")

    ok, rep = run_gates(risk, panel)

    # ---- persist typed handoff ----
    out = C.DATA_DIR / "swing_risk_panel.parquet"
    risk["date"] = pd.to_datetime(risk["date"])
    risk.to_parquet(out, index=False)
    print(f"  wrote {out}")
    meta = {
        "horizon_days": H, "q": Q, "refit_every": REFIT_EVERY, "min_window": MIN_WIN,
        "evt_min_exceedances": MIN_EXC, "threshold_pcts": THRESH_PCTS,
        "drift_zero": bool(C.VAR_DRIFT_ZERO),
        "n_snapshots": int(len(risk)), "n_tradable": int(len(tr)),
        "median_var_frac": float(tr["var_frac"].median()) if len(tr) else None,
        "median_es_frac": float(tr["es_frac"].median()) if len(tr) else None,
        "gates": rep, "seed": C.SEED,
        "versions": {"numpy": np.__version__, "pandas": pd.__version__},
    }
    (C.ARTIFACTS_DIR / "p3_risk.json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"  wrote {C.ARTIFACTS_DIR / 'p3_risk.json'}")

    # ---- smoke: reload, re-verify the sizing invariant survives a round trip ----
    rr = pd.read_parquet(out)
    rt = rr[rr["tradable"]]
    sh = np.array([size_position(v, p, C.INITIAL_CAPITAL_INR, C.PER_POSITION_RISK_BUDGET_INR)
                   for v, p in zip(rt["var_per_unit"], rt["ref_px"])])
    smoke = bool(np.all(sh * rt["var_per_unit"].to_numpy() <= C.PER_POSITION_RISK_BUDGET_INR + 1e-6))
    print(f"  [{'PASS' if smoke else 'FAIL'}] smoke: reload panel, sizing invariant holds")

    print(f"\n{'PHASE 3 RISK ENGINE COMPLETE' if ok else 'PHASE 3 RISK ENGINE - GATE FINDING'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
