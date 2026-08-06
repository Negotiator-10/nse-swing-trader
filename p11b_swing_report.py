"""
p11b_swing_report.py - the paper-trading DASHBOARD (Kite-style + hedge-fund blotter).

Renders reports/paper_dashboard.html (self-contained, offline, open in any browser)
from the live handoffs -- no API calls, no engine run:
    data/paper_state.json           current book (positions, cash, kill state)
    data/paper_ledger.parquet       closed-trade ledger
    artifacts/p4_paper.json         latest decision record (armed actions, bar date)
    data/swing_risk_panel.parquet   latest close per symbol (mark-to-market)

What a real fund records, mapped here:
    NAV / equity curve   -> KPI tiles + line chart (data/paper_equity_curve.parquet,
                            appended one row per decision date whenever this runs)
    positions book       -> open positions marked to the latest close, unrealized P&L
    trade blotter        -> every closed round trip: dates, prices, shares, hold days,
                            exit reason, FULL NSE cost breakdown (STT/exchange/SEBI/
                            stamp/GST/DP + modeled slippage), net P&L
    orders for tomorrow  -> armed entries/exits from tonight's decision
    risk state           -> risk budget usage, exposure, kill-switch status

Run:  C:/Users/Admin/anaconda3/python.exe p11b_swing_report.py
(Also auto-run at the end of every LIVE p11 run.)  ASCII-only prints.
"""
from __future__ import annotations

import html
import json

import numpy as np
import pandas as pd

import config as C

REPORT_DIR = C.ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)
OUT = REPORT_DIR / "paper_dashboard.html"
CURVE = C.DATA_DIR / "paper_equity_curve.parquet"

# validated reference palette (light mode)
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
SURF, PAGE, GRID, BASE = "#fcfcfb", "#f9f9f7", "#e1e0d9", "#c3c2b7"
BLUE, RED, GREEN2 = "#2a78d6", "#e34948", "#006300"


def load_inputs():
    st = json.loads(C.PAPER_STATE_PATH.read_text())
    led = pd.read_parquet(C.PAPER_LEDGER_PATH) if C.PAPER_LEDGER_PATH.exists() else pd.DataFrame()
    art = json.loads((C.ARTIFACTS_DIR / "p4_paper.json").read_text())
    g46 = art.get("gates", {}).get("G4.6", {})
    risk = pd.read_parquet(C.DATA_DIR / "swing_risk_panel.parquet")
    risk["date"] = pd.to_datetime(risk["date"])
    last_close = (risk.sort_values("date").groupby("symbol").tail(1)
                  .set_index("symbol")["ref_px"].to_dict())
    # per-symbol daily close series (the ONLY store carrying live-era bars:
    # swing_daily.parquet stops at the research cutoff -- see DECISIONS.md #001-a)
    pxs = risk[["date", "symbol", "ref_px"]].sort_values("date").reset_index(drop=True)
    return st, led, g46, last_close, pxs


def trade_costs(shares: int, epx: float, xpx: float) -> dict:
    """Recompute the NSE delivery cost components for one closed round trip."""
    dc = C.COSTS_EQUITY_DELIVERY
    bv, sv = shares * epx, shares * xpx
    stt = dc["stt_pct_each_side"] * (bv + sv)
    exch = dc["exch_txn_pct"] * (bv + sv)
    sebi = dc["sebi_pct"] * (bv + sv)
    stamp = dc["stamp_buy_pct"] * bv
    gst = dc["gst_pct"] * (exch + sebi)
    dp = dc["dp_charge_sell_inr"]
    return {"stt": stt, "exch": exch, "sebi": sebi, "stamp": stamp, "gst": gst,
            "dp": dp, "total": stt + exch + sebi + stamp + gst + dp}


def update_curve(st: dict, asof_ts: pd.Timestamp, last_close: dict, mkt_syms_pos: dict) -> pd.DataFrame:
    """Append today's NAV row (dedup by date) and return the full curve. asof_ts is the
    resolved decision date -- the caller guarantees it is a valid Timestamp (never NaT), so
    the curve never gains a dateless point again. Also self-heals: any pre-existing NaT-dated
    row (written by an older build that read a stale/failed artifact) is dropped on load."""
    invested = sum(p["shares"] * last_close.get(sym, p["entry_px"])
                   for sym, p in mkt_syms_pos.items())
    row = {"date": pd.Timestamp(asof_ts),
           "equity": float(st["cash"]) + invested, "cash": float(st["cash"]),
           "invested": invested, "n_positions": len(mkt_syms_pos)}
    curve = pd.read_parquet(CURVE) if CURVE.exists() else pd.DataFrame()
    curve = pd.concat([curve, pd.DataFrame([row])], ignore_index=True)
    curve["date"] = pd.to_datetime(curve["date"])
    curve = curve.dropna(subset=["date"])                          # self-heal old NaT rows
    curve = (curve.sort_values("date").drop_duplicates(subset="date", keep="last")
             .reset_index(drop=True))
    curve.to_parquet(CURVE, index=False)
    return curve


def svg_curve(curve: pd.DataFrame, w=880, h=180) -> str:
    """Tiny self-contained SVG line chart of the NAV curve."""
    if len(curve) == 0:
        return "<p class='muted'>no NAV history yet</p>"
    y = curve["equity"].to_numpy(float)
    x = np.linspace(40, w - 12, len(y)) if len(y) > 1 else np.array([w / 2])
    lo, hi = float(y.min()), float(y.max())
    pad = max((hi - lo) * 0.15, 1.0)
    lo, hi = lo - pad, hi + pad
    ys = h - 24 - (y - lo) / (hi - lo) * (h - 44)
    pts = " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(x, ys))
    dots = "".join(f"<circle cx='{a:.1f}' cy='{b:.1f}' r='4' fill='{BLUE}' "
                   f"stroke='{SURF}' stroke-width='2'/>" for a, b in zip(x, ys))
    base = C.INITIAL_CAPITAL_INR
    yb = h - 24 - (base - lo) / (hi - lo) * (h - 44)
    labels = (f"<text x='6' y='{ys[0]+4:.0f}' font-size='11' fill='{INK2}'>"
              f"{y[0]:,.0f}</text>"
              f"<text x='{x[-1]-6:.0f}' y='{ys[-1]-8:.0f}' font-size='11' fill='{INK}'"
              f" text-anchor='end' font-weight='bold'>{y[-1]:,.0f}</text>")
    line = (f"<polyline points='{pts}' fill='none' stroke='{BLUE}' stroke-width='2'/>"
            if len(y) > 1 else "")
    return (f"<svg viewBox='0 0 {w} {h}' style='width:100%;height:auto'>"
            f"<line x1='40' y1='{yb:.1f}' x2='{w-12}' y2='{yb:.1f}' stroke='{BASE}' "
            f"stroke-width='1' stroke-dasharray='4 4'/>"
            f"<text x='6' y='{yb+4:.0f}' font-size='10' fill='{MUTED}'>start</text>"
            f"{line}{dots}{labels}</svg>")


def episodes(sym: str, led: pd.DataFrame, pos: dict) -> list:
    """Every buy/sell episode for one symbol: closed round trips + any open position."""
    eps = []
    if len(led):
        for _, t in led[led["symbol"].astype(str) == sym].iterrows():
            eps.append({"entry_date": pd.Timestamp(t["entry_date"]),
                        "entry_px": float(t["entry_px"]),
                        "exit_date": pd.Timestamp(t["exit_date"]),
                        "exit_px": float(t["exit_px"]), "pnl": float(t["pnl"]),
                        "reason": str(t["reason"]), "shares": int(t["shares"]),
                        "open": False, "stop": None})
    if sym in pos:
        p = pos[sym]
        eps.append({"entry_date": pd.Timestamp(str(p["entry_date"])[:10]),
                    "entry_px": float(p["entry_px"]), "exit_date": None,
                    "exit_px": None, "pnl": None, "reason": "",
                    "shares": int(p["shares"]), "open": True,
                    "stop": float(p["stop"])})
    return eps


def svg_trade_chart(ser: pd.DataFrame, eps: list, w=430, h=158) -> str:
    """Close line for one symbol with BUY/SELL markers and the shaded hold span.

    NOTE: the line is CLOSES (the only live-era price series available), while fills
    happen at the OPEN -- so on a gap day a marker can sit off the line. That is real,
    not a rendering bug (DECISIONS.md #001-a)."""
    if len(ser) < 2 or not eps:
        return "<p class='muted'>no price history</p>"
    dates = ser["date"].to_numpy("datetime64[ns]")
    yall = ser["ref_px"].to_numpy(float)
    n = len(yall)

    def loc(ts) -> int:
        i = int(np.searchsorted(dates, np.datetime64(pd.Timestamp(ts), "ns")))
        return min(max(i, 0), n - 1)

    ents = [loc(e["entry_date"]) for e in eps]
    exts = [loc(e["exit_date"]) if e["exit_date"] is not None else n - 1 for e in eps]
    lo, hi = max(0, min(ents) - 20), min(n, max(exts) + 11)
    if hi - lo < 3:
        lo = max(0, hi - 6)
    ys = yall[lo:hi]
    m = len(ys)

    marks = [e["entry_px"] for e in eps]
    marks += [e["exit_px"] for e in eps if e["exit_px"] is not None]
    marks += [e["stop"] for e in eps if e["stop"] is not None]
    vlo, vhi = min(float(ys.min()), min(marks)), max(float(ys.max()), max(marks))
    pad = max((vhi - vlo) * 0.12, 0.5)
    vlo, vhi = vlo - pad, vhi + pad
    PL, PR, PT, PB = 6, 46, 12, 16

    def X(i):
        return PL + (i - lo) / max(m - 1, 1) * (w - PL - PR)

    def Y(v):
        return PT + (vhi - v) / (vhi - vlo) * (h - PT - PB)

    out = []
    for e, ie, ix in zip(eps, ents, exts):
        x0, x1 = X(max(ie, lo)), X(min(ix, hi - 1))
        out.append(f"<rect x='{x0:.1f}' y='{PT}' width='{max(x1 - x0, 1.5):.1f}' "
                   f"height='{h - PT - PB:.1f}' fill='{BLUE}' opacity='0.07'/>")
    for e in eps:
        if e["open"] and e["stop"]:
            yv = Y(e["stop"])
            out.append(f"<line x1='{PL}' y1='{yv:.1f}' x2='{w - PR}' y2='{yv:.1f}' "
                       f"stroke='{RED}' stroke-width='1' stroke-dasharray='3 3' "
                       f"opacity='0.6'/><text x='{w - PR + 3}' y='{yv + 3:.1f}' "
                       f"font-size='9' fill='{RED}'>stop</text>")
    pts = " ".join(f"{X(lo + k):.1f},{Y(ys[k]):.1f}" for k in range(m))
    out.append(f"<polyline points='{pts}' fill='none' stroke='{INK2}' stroke-width='1.6'/>")
    for e, ie, ix in zip(eps, ents, exts):
        out.append(f"<circle cx='{X(ie):.1f}' cy='{Y(e['entry_px']):.1f}' r='4.5' "
                   f"fill='{GREEN2}' stroke='{SURF}' stroke-width='1.6'><title>BUY "
                   f"{e['shares']} @ {e['entry_px']:,.2f} on "
                   f"{str(e['entry_date'])[:10]}</title></circle>")
        if not e["open"]:
            out.append(f"<circle cx='{X(ix):.1f}' cy='{Y(e['exit_px']):.1f}' r='4.5' "
                       f"fill='{RED}' stroke='{SURF}' stroke-width='1.6'><title>SELL @ "
                       f"{e['exit_px']:,.2f} on {str(e['exit_date'])[:10]} "
                       f"({e['reason']}) net P&amp;L {e['pnl']:+,.2f}</title></circle>")
    out.append(f"<text x='{w - PR + 3}' y='{Y(ys[-1]) + 3:.1f}' font-size='9.5' "
               f"fill='{INK}'>{ys[-1]:,.0f}</text>")
    return f"<svg viewBox='0 0 {w} {h}' style='width:100%;height:auto'>{''.join(out)}</svg>"


def library_rows(led: pd.DataFrame, pos: dict) -> str:
    """Per-symbol aggregates. LIVE ERA ONLY -- backtest trades are never merged in
    (a 1-trade live record blended with 2,747 backtest rows means nothing)."""
    syms = set(pos)
    if len(led):
        syms |= set(led["symbol"].astype(str))
    if not syms:
        return "<tr><td class='muted'>no stocks traded yet</td></tr>"
    rows = []
    for sym in sorted(syms):
        sub = led[led["symbol"].astype(str) == sym] if len(led) else led
        nc = len(sub)
        wins = int((sub["pnl"] > 0).sum()) if nc else 0
        net = float(sub["pnl"].sum()) if nc else 0.0
        costs = sum(trade_costs(int(t["shares"]), float(t["entry_px"]),
                                float(t["exit_px"]))["total"]
                    for _, t in sub.iterrows()) if nc else 0.0
        avgd = f"{sub['days_held'].mean():.1f}" if nc else "-"
        col = GREEN2 if net > 0 else (RED if net < 0 else INK2)
        held = ("<span class='buy'>HOLDING</span>" if sym in pos
                else "<span class='muted'>flat</span>")
        rows.append(f"<tr><td><b>{html.escape(sym)}</b></td><td>{held}</td>"
                    f"<td class='r'>{nc}</td><td class='r'>{wins}</td>"
                    f"<td class='r'>{nc - wins}</td>"
                    f"<td class='r'>{net + costs:+,.2f}</td>"
                    f"<td class='r'>{costs:,.2f}</td>"
                    f"<td class='r' style='color:{col};font-weight:600'>{net:+,.2f}</td>"
                    f"<td class='r'>{avgd}</td></tr>")
    return ("<tr><th>Stock</th><th>Status</th><th class='r'>Closed</th><th class='r'>Wins</th>"
            "<th class='r'>Losses</th><th class='r'>Gross P&L</th><th class='r'>Costs</th>"
            "<th class='r'>Net P&L</th><th class='r'>Avg days</th></tr>" + "".join(rows))


def cost_breakdown(led: pd.DataFrame, pos: dict) -> dict:
    """Every rupee of cost, split into what has actually been CHARGED vs what has been
    incurred but not yet charged.

    IMPORTANT (see DECISIONS.md #003): the engine books the WHOLE round-trip cost at
    EXIT (`_do_exit` -> delivery_roundtrip_cost(buy_val, sell_val)); nothing is deducted
    at entry. So an open position has paid nothing yet, even though its buy-side STT and
    stamp duty are already economically incurred -- NAV is overstated by that amount
    until the position closes."""
    dc = C.COSTS_EQUITY_DELIVERY
    s = C.SLIPPAGE_BPS / 1e4
    keys = ("stt", "dp", "stamp", "exch", "sebi", "gst")
    booked = {k: 0.0 for k in keys}
    accrued = {k: 0.0 for k in keys}
    slip_booked = slip_accrued = 0.0

    if len(led):
        for _, t in led.iterrows():
            sh, epx, xpx = int(t["shares"]), float(t["entry_px"]), float(t["exit_px"])
            c = trade_costs(sh, epx, xpx)
            for k in keys:
                booked[k] += c[k]
            # slippage is embedded in the fill prices: entry = O*(1+s), exit = O*(1-s)
            slip_booked += sh * (epx * s / (1 + s) + xpx * s / (1 - s))

    for p in pos.values():
        bv = p["shares"] * p["entry_px"]
        accrued["stt"] += dc["stt_pct_each_side"] * bv      # buy leg only
        accrued["stamp"] += dc["stamp_buy_pct"] * bv
        e, sb = dc["exch_txn_pct"] * bv, dc["sebi_pct"] * bv
        accrued["exch"] += e
        accrued["sebi"] += sb
        accrued["gst"] += dc["gst_pct"] * (e + sb)
        slip_accrued += p["shares"] * p["entry_px"] * s / (1 + s)

    return {"booked": booked, "accrued": accrued, "slip_booked": slip_booked,
            "slip_accrued": slip_accrued,
            "total_booked": sum(booked.values()) + slip_booked,
            "total_accrued": sum(accrued.values()) + slip_accrued}


def cost_rows(cb: dict) -> str:
    """Cost table: charged vs incurred-not-yet-charged, per component."""
    label = {"stt": "STT (0.1% EACH side - delivery)",
             "dp": "DP charge (FLAT Rs.15.34 per sell)",
             "stamp": "Stamp duty (0.015%, buy only)",
             "exch": "Exchange transaction (0.00297%)",
             "sebi": "SEBI turnover (0.0001%)",
             "gst": "GST (18% on exchange + SEBI)"}
    tot = cb["total_booked"]
    rows = []
    for k, lab in label.items():
        b, a = cb["booked"][k], cb["accrued"][k]
        share = f"{b / tot * 100:.0f}%" if tot > 0 else "-"
        rows.append(f"<tr><td>{lab}</td><td class='r'>{b:,.2f}</td>"
                    f"<td class='r muted'>{a:,.2f}</td><td class='r muted'>{share}</td></tr>")
    rows.append(f"<tr><td>Slippage ({C.SLIPPAGE_BPS:.0f}bp/side, modeled - inside fill px)"
                f"</td><td class='r'>{cb['slip_booked']:,.2f}</td>"
                f"<td class='r muted'>{cb['slip_accrued']:,.2f}</td>"
                f"<td class='r muted'>"
                f"{cb['slip_booked'] / tot * 100 if tot > 0 else 0:.0f}%</td></tr>")
    rows.append(f"<tr style='background:{PAGE}'><td><b>TOTAL</b></td>"
                f"<td class='r'><b>{cb['total_booked']:,.2f}</b></td>"
                f"<td class='r'><b>{cb['total_accrued']:,.2f}</b></td>"
                f"<td class='r'></td></tr>")
    return ("<tr><th>Cost component</th><th class='r'>Charged to NAV</th>"
            "<th class='r'>Incurred, not yet charged</th><th class='r'>Share of charged</th>"
            "</tr>" + "".join(rows))


def manual_block(g46: dict, led: pd.DataFrame, esc) -> str:
    """Manual-sell control + pending orders + the strategy-vs-actual P&L split
    (DECISIONS #014). The dashboard is a static file with no server, so the button copies
    the command that queues the sell; p11 executes it at the next run's open."""
    orders = g46.get("manual_orders") or []
    pend = [o for o in orders if o.get("status", "pending") == "pending"]
    rows = ""
    if orders:
        rr = []
        for o in orders:
            st_ = o.get("status", "pending")
            colour = GREEN2 if st_ == "executed" else INK2
            rr.append(f"<tr><td><b>{esc(str(o['symbol']))}</b></td>"
                      f"<td><span class='sell'>{esc(str(o.get('side','SELL')))}</span></td>"
                      f"<td>{esc(str(o['issued_date']))}</td>"
                      f"<td>{esc(str(o.get('fill_date') or 'next session'))}</td>"
                      f"<td style='color:{colour}'>{esc(st_)}</td></tr>")
        rows = ("<table><tr><th>Stock</th><th>Side</th><th>Issued</th><th>Fills</th>"
                "<th>Status</th></tr>" + "".join(rr) + "</table>")
    else:
        rows = "<p class='muted'>No manual orders have been issued.</p>"

    # strategy-only vs actual P&L -- the whole point of tagging manual exits
    split = ""
    if len(led):
        man = led[led["reason"].astype(str) == "manual"]
        rule = led[led["reason"].astype(str) != "manual"]
        # Shown from the moment the first order is issued (not just executed) so the
        # strategy-vs-discretion comparison is visible while it is still empty.
        if len(man) or orders:
            split = (f"<div class='kpis' style='margin:10px 0'>"
                     f"<div class='kpi'><div class='l'>Realized: strategy only</div>"
                     f"<div class='v'>Rs.{rule['pnl'].sum():,.2f}"
                     f"<span style='font-size:12px'> ({len(rule)} trades)</span></div></div>"
                     f"<div class='kpi'><div class='l'>Realized: your manual exits</div>"
                     f"<div class='v'>Rs.{man['pnl'].sum():,.2f}"
                     f"<span style='font-size:12px'> ({len(man)} trades)</span></div></div>"
                     f"<div class='kpi'><div class='l'>Realized: actual (both)</div>"
                     f"<div class='v'>Rs.{led['pnl'].sum():,.2f}</div></div></div>")
    note = ("<div class='sub' style='margin-top:8px'><b>How this works.</b> This page is a "
            "static file with no server, so the button copies a command. Paste it in a "
            "terminal to queue the sell; it fills at the <b>next session's open</b>, the same "
            "timing as every rule-driven exit. Manual exits are tagged "
            "<code>reason=\"manual\"</code> in the ledger and reported separately above, so "
            "the strategy's own track record stays measurable. Cancel a pending order with "
            "<code>--undo</code>. See DECISIONS.md #014.</div>")
    warn = ""
    if pend:
        warn = (f"<div class='sub' style='color:{RED};margin-top:6px'><b>"
                f"{len(pend)} manual sell(s) pending</b> - they execute at the next "
                f"evening run.</div>")
    return split + rows + warn + note


def shadow_block(esc) -> str:
    """The 'Waiting stocks (what-if)' section (DECISIONS #005 Piece 1). Read-only from
    paper_shadow.parquet -- each skipped candidate tracked as a hypothetical buy vs the
    stocks we kept. Display only; nothing here exists in the real book."""
    if not C.PAPER_SHADOW_PATH.exists():
        return "<p class='muted'>No shadow data yet - populated after the next LIVE run.</p>"
    df = pd.read_parquet(C.PAPER_SHADOW_PATH)
    wait = df[df["kind"] == "waiting"]
    hold = df[df["kind"] == "hold"]
    if not len(wait):
        return ("<p class='muted'>Nothing waiting - the book is not full, so no fresh signal "
                "is being skipped.</p>")

    def rc(v):
        return GREEN2 if v > 0 else (RED if v < 0 else INK2)

    rows = []
    for _, r in wait.sort_values("entry_date").iterrows():
        stat = esc(str(r["status"]))
        if str(r["exit_reason"]) not in ("-", "nan", "None"):
            stat += f" ({esc(str(r['exit_reason']))})"
        rows.append(f"<tr><td><b>{esc(str(r['symbol']))}</b></td>"
                    f"<td>{esc(str(r['entry_date'])[:10])}</td>"
                    f"<td class='r'>{r['entry_px']:,.2f}</td>"
                    f"<td class='r'>{r['mark_px']:,.2f}</td>"
                    f"<td class='r' style='color:{rc(r['ret_pct'])}'>{r['ret_pct']:+.2f}%</td>"
                    f"<td class='r'>{int(r['days'])}</td><td>{stat}</td></tr>")
    table = ("<table><tr><th>Stock (skipped)</th><th>What-if buy date</th>"
             "<th class='r'>Buy px</th><th class='r'>Now (close)</th><th class='r'>Return</th>"
             "<th class='r'>Days</th><th>Status</th></tr>" + "".join(rows) + "</table>")

    mw = float(wait["ret_pct"].mean())
    mh = float(hold["ret_pct"].mean()) if len(hold) else float("nan")
    edge = mw - mh
    verdict = ("skipped stocks did BETTER so far - rotation may have a prize (keep watching)"
               if edge > 0 else
               "skipped stocks did WORSE so far - the cap saved turnover + cost (keep watching)")
    summ = (f"<div class='sub' style='margin-top:8px'>Mean price return so far: "
            f"<b style='color:{rc(mw)}'>waiting {mw:+.2f}%</b> vs "
            f"<b style='color:{rc(mh)}'>holds {mh:+.2f}%</b> "
            f"(edge <b style='color:{rc(edge)}'>{edge:+.2f}%</b>) - {esc(verdict)}. "
            f"<b>Preliminary:</b> gross price return, pre-cost, on only a few live days - the "
            f"real answer needs 2-3 months of data. These are HYPOTHETICAL buys; nothing here "
            f"is in your real Zerodha book. See DECISIONS.md #005 / #008.</div>")
    return table + summ


def build() -> str:
    st, led, g46, last_close, pxs = load_inputs()
    pos = {sym: p for sym, p in st.get("pos", {}).items()}
    # Resolve the decision date ROBUSTLY. The dashboard is refreshed DURING a live run, before
    # p4_paper.json is rewritten, so g46 can be the PREVIOUS (or a failed) run's block with no
    # decision_date -- pd.Timestamp(None) then poisoned the NAV curve with a NaT-dated point.
    # Fall back to the latest live-era bar date in the risk panel, which this run already
    # refreshed, so asof_ts is always valid and current.
    _d = g46.get("decision_date") or g46.get("bar_date")
    asof_ts = pd.Timestamp(_d) if _d else pd.NaT
    if pd.isna(asof_ts):
        asof_ts = pd.to_datetime(pxs["date"]).max()
    curve = update_curve(st, asof_ts, last_close, pos)
    eq = float(curve["equity"].iloc[-1])
    invested = float(curve["invested"].iloc[-1])
    day_pnl = float(eq - curve["equity"].iloc[-2]) if len(curve) > 1 else eq - C.INITIAL_CAPITAL_INR
    tot_pnl = eq - C.INITIAL_CAPITAL_INR
    risk_used = sum(p["shares"] * p["vpu"] for p in pos.values())
    esc = html.escape

    def money(x, signed=False):
        s = f"{x:+,.2f}" if signed else f"{x:,.2f}"
        col = GREEN2 if x > 0 else (RED if x < 0 else INK2)
        return f"<span style='color:{col if signed else INK}'>{s}</span>"

    # ---- open positions table ----
    prow = []
    for sym, p in sorted(pos.items()):
        mark = last_close.get(sym, p["entry_px"])
        upnl = (mark - p["entry_px"]) * p["shares"]
        upct = (mark / p["entry_px"] - 1) * 100
        prow.append(f"<tr><td><b>{esc(sym)}</b></td><td>{esc(p['sleeve'])}</td>"
                    f"<td>{esc(str(p['entry_date'])[:10])}</td><td class='r'>{p['shares']}</td>"
                    f"<td class='r'>{p['entry_px']:,.2f}</td><td class='r'>{mark:,.2f}</td>"
                    f"<td class='r'>{p['shares']*p['entry_px']:,.0f}</td>"
                    f"<td class='r'>{money(upnl, True)} ({upct:+.1f}%)</td>"
                    f"<td class='r'>{p['stop']:,.2f}</td>"
                    f"<td class='r'><button class='sellbtn' data-sym='{esc(sym)}'>"
                    f"Sell</button></td></tr>")
    pos_tbl = ("<tr><th>Stock</th><th>Sleeve</th><th>Entry date</th><th class='r'>Qty</th>"
               "<th class='r'>Avg. buy</th><th class='r'>LTP (close)</th><th class='r'>"
               "Invested Rs.</th><th class='r'>Unrealized P&L</th><th class='r'>Stop</th>"
               "<th class='r'>Manual</th></tr>"
               + "".join(prow)) if prow else "<tr><td class='muted'>no open positions</td></tr>"

    # ---- armed actions (tomorrow's orders) ----
    arm = []
    for sym, r in (g46.get("armed_exits_next_open") or {}).items():
        arm.append(f"<tr><td><b>{esc(sym)}</b></td><td><span class='sell'>SELL</span></td>"
                   f"<td>next open</td><td>{esc(str(r))}</td></tr>")
    for sym, pe in (g46.get("armed_entries_next_open") or {}).items():
        arm.append(f"<tr><td><b>{esc(sym)}</b></td><td><span class='buy'>BUY</span></td>"
                   f"<td>next open</td><td>{esc(pe['sleeve'])} | risk budget "
                   f"Rs.{pe['budget_inr']:,.0f}</td></tr>")
    arm_tbl = ("<tr><th>Stock</th><th>Side</th><th>When</th><th>Reason / sizing</th></tr>"
               + "".join(arm)) if arm else "<tr><td class='muted'>nothing armed</td></tr>"

    # ---- blotter (closed trades, newest first) ----
    brow = []
    if len(led):
        led2 = led.copy()
        led2["exit_date"] = pd.to_datetime(led2["exit_date"])
        for _, t in led2.sort_values("exit_date", ascending=False).head(200).iterrows():
            cost = trade_costs(int(t["shares"]), float(t["entry_px"]), float(t["exit_px"]))
            brow.append(
                f"<tr><td>{str(t['entry_date'])[:10]}</td><td>{str(t['exit_date'])[:10]}</td>"
                f"<td><b>{esc(str(t['symbol']))}</b></td><td>{esc(str(t['sleeve']))}</td>"
                f"<td class='r'>{int(t['shares'])}</td><td class='r'>{t['entry_px']:,.2f}</td>"
                f"<td class='r'>{t['exit_px']:,.2f}</td><td>{esc(str(t['reason']))}</td>"
                f"<td class='r'>{int(t['days_held'])}</td>"
                f"<td class='r' title='STT {cost['stt']:.2f} | exch {cost['exch']:.2f} | "
                f"SEBI {cost['sebi']:.2f} | stamp {cost['stamp']:.2f} | GST {cost['gst']:.2f} "
                f"| DP {cost['dp']:.2f}'>{cost['total']:,.2f}</td>"
                f"<td class='r'>{money(float(t['pnl']), True)}</td></tr>")
    blot_tbl = ("<tr><th>Buy date</th><th>Sell date</th><th>Stock</th><th>Sleeve</th>"
                "<th class='r'>Qty</th><th class='r'>Buy px</th><th class='r'>Sell px</th>"
                "<th>Exit reason</th><th class='r'>Days</th><th class='r'>Costs Rs. "
                "(hover)</th><th class='r'>Net P&L</th>"
                + "".join(brow)) if brow else "<tr><td class='muted'>no closed trades yet"\
                " - the live era has just begun</td></tr>"

    # ---- per-stock trade charts + library (LIVE ERA ONLY) ----
    traded = sorted(set(pos) | (set(led["symbol"].astype(str)) if len(led) else set()))
    cards = []
    for sym in traded:
        eps = episodes(sym, led, pos)
        ser = pxs[pxs["symbol"] == sym][["date", "ref_px"]].reset_index(drop=True)
        p = pos.get(sym)
        if p:
            mark = last_close.get(sym, p["entry_px"])
            note = (f"holding {p['shares']} @ {p['entry_px']:,.2f} | mark {mark:,.2f}")
            tag = money((mark - p["entry_px"]) * p["shares"], True) + " unreal."
        else:
            sub = led[led["symbol"].astype(str) == sym]
            note = f"{len(sub)} closed round trip(s)"
            tag = money(float(sub["pnl"].sum()), True) + " realized"
        cards.append(f"<div class='card'><div class='chd'><b>{esc(sym)}</b>"
                     f"<span class='r'>{tag}</span></div>"
                     f"<div class='muted' style='font-size:11px;margin-bottom:2px'>{note}"
                     f"</div>{svg_trade_chart(ser, eps)}</div>")
    charts = "".join(cards) or "<p class='muted'>no stocks traded yet</p>"
    lib_tbl = library_rows(led, pos)
    cb = cost_breakdown(led, pos)
    cost_tbl = cost_rows(cb)
    cpt = cb["total_booked"] / len(led) if len(led) else 0.0

    wins = int((led["pnl"] > 0).sum()) if len(led) else 0
    kill = "HALTED" if st.get("halted") else "armed"
    asof = esc(str(asof_ts.date()) if pd.notna(asof_ts) else "?")

    body = f"""
<style>
 body {{ background:{PAGE}; color:{INK}; font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
        margin:0; padding:24px; }}
 .wrap {{ max-width:960px; margin:0 auto; }}
 h1 {{ font-size:20px; margin:0 0 2px; }}  h2 {{ font-size:14px; margin:26px 0 8px; }}
 .sub {{ color:{INK2}; font-size:12px; margin-bottom:18px; }}
 .kpis {{ display:flex; flex-wrap:wrap; gap:10px; }}
 .kpi {{ background:{SURF}; border:1px solid {GRID}; border-radius:10px; padding:10px 16px;
        min-width:128px; }}
 .kpi .l {{ font-size:11px; color:{MUTED}; }}  .kpi .v {{ font-size:19px; font-weight:650; }}
 table {{ width:100%; border-collapse:collapse; background:{SURF}; border:1px solid {GRID};
        border-radius:10px; overflow:hidden; font-size:12.5px; }}
 th {{ text-align:left; color:{INK2}; font-weight:600; padding:8px 10px;
      border-bottom:1px solid {GRID}; background:{PAGE}; }}
 td {{ padding:7px 10px; border-bottom:1px solid {GRID}; }}
 tr:last-child td {{ border-bottom:none; }}
 .r {{ text-align:right; font-variant-numeric:tabular-nums; }}
 .muted {{ color:{MUTED}; }}
 .buy {{ color:{GREEN2}; font-weight:700 }} .sell {{ color:{RED}; font-weight:700 }}
 .card {{ background:{SURF}; border:1px solid {GRID}; border-radius:10px; padding:12px; }}
 .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:12px; }}
 .chd {{ display:flex; justify-content:space-between; align-items:baseline;
        font-size:12.5px; margin-bottom:1px; }}
 .sellbtn {{ background:{SURF}; color:{RED}; border:1px solid {GRID}; border-radius:6px;
        padding:2px 10px; font-size:11.5px; font-weight:600; cursor:pointer;
        font-family:inherit; }}
 .sellbtn:hover, .sellbtn:focus {{ background:{RED}; color:{SURF}; border-color:{RED};
        outline:2px solid transparent; outline-offset:2px; }}
 .sellbtn:focus-visible {{ outline:2px solid {BLUE}; }}
 .sellbtn.done {{ color:{GREEN2}; border-color:{GREEN2}; }}
 code {{ font-family:Consolas,ui-monospace,monospace; background:{PAGE};
        border:1px solid {GRID}; border-radius:4px; padding:1px 5px; font-size:12px; }}
</style>
<div class='wrap'>
 <h1>nse_paper_v1 - paper trading book</h1>
 <div class='sub'>as of decision date <b>{asof}</b> | run mode LIVE (paper - no real orders)
  | capital Rs.{C.INITIAL_CAPITAL_INR:,.0f} | era start {C.PAPER_ERA_START}</div>
 <div class='kpis'>
  <div class='kpi'><div class='l'>NAV (equity)</div><div class='v'>Rs.{eq:,.2f}</div></div>
  <div class='kpi'><div class='l'>Day P&amp;L</div><div class='v'>{money(day_pnl, True)}</div></div>
  <div class='kpi'><div class='l'>Total P&amp;L</div><div class='v'>{money(tot_pnl, True)}
    <span style='font-size:12px'>({tot_pnl/C.INITIAL_CAPITAL_INR*100:+.2f}%)</span></div></div>
  <div class='kpi'><div class='l'>Cash</div><div class='v'>Rs.{st['cash']:,.0f}</div></div>
  <div class='kpi'><div class='l'>Invested / exposure</div><div class='v'>Rs.{invested:,.0f}
    <span style='font-size:12px'>({invested/eq*100:.0f}%)</span></div></div>
  <div class='kpi'><div class='l'>Risk used / budget</div><div class='v'>Rs.{risk_used:,.0f}
    <span style='font-size:12px'>/ {C.PORTFOLIO_RISK_BUDGET_INR:,.0f}</span></div></div>
  <div class='kpi'><div class='l'>Positions</div><div class='v'>{len(pos)} / {C.MAX_CONCURRENT_POSITIONS}</div></div>
  <div class='kpi'><div class='l'>Closed trades (wins)</div><div class='v'>{len(led)} ({wins})</div></div>
  <div class='kpi'><div class='l'>Kill switch</div><div class='v'>{kill}</div></div>
 </div>
 <h2>NAV curve (live era)</h2><div class='card'>{svg_curve(curve)}</div>
 <h2>Open positions (marked to last close)</h2><table>{pos_tbl}</table>
 <h2>Orders armed for the next open</h2><table>{arm_tbl}</table>
 <h2>Manual sell (discretionary override)</h2>
 {manual_block(g46, led, esc)}
 <h2>Waiting stocks (what-if) - candidates skipped at the {C.MAX_CONCURRENT_POSITIONS}/{C.MAX_CONCURRENT_POSITIONS} cap, tracked as if we had bought them</h2>
 {shadow_block(esc)}
 <h2>Trade blotter (closed round trips; hover Costs for the full breakdown)</h2>
 <table>{blot_tbl}</table>
 <h2>Per-stock trade charts (live era) - green = BUY, red = SELL, shaded = held; hover a
  marker for details</h2>
 <div class='grid'>{charts}</div>
 <h2>Library - every stock traded in the live era (from {C.PAPER_ERA_START})</h2>
 <table>{lib_tbl}</table>
 <h2>Costs paid (live era) - every rupee of friction</h2>
 <div class='kpis' style='margin-bottom:10px'>
  <div class='kpi'><div class='l'>Costs charged to NAV</div>
   <div class='v'>Rs.{cb['total_booked']:,.2f}</div></div>
  <div class='kpi'><div class='l'>Per closed trade</div>
   <div class='v'>Rs.{cpt:,.2f}</div></div>
  <div class='kpi'><div class='l'>Incurred, not yet charged</div>
   <div class='v'>Rs.{cb['total_accrued']:,.2f}</div></div>
  <div class='kpi'><div class='l'>Total friction vs NAV</div>
   <div class='v'>{(cb['total_booked']+cb['total_accrued'])/eq*100:.3f}%</div></div>
 </div>
 <table>{cost_tbl}</table>
 <div class='sub' style='margin-top:6px'><b>Why two columns.</b> The engine charges the
  WHOLE round-trip cost at EXIT (<code>_do_exit</code>), nothing at entry. So an open
  position has paid nothing yet even though its buy-side STT and stamp duty are already
  economically incurred - column 2. NAV is therefore overstated by that amount until each
  position closes, and every open position still owes a sell leg (another 0.1% STT plus
  the flat Rs.{C.COSTS_EQUITY_DELIVERY['dp_charge_sell_inr']:.2f} DP charge) when it
  exits. See DECISIONS.md #003.</div>
<script>
// Manual-sell buttons (DECISIONS #014). This page is static -- no server -- so the button
// copies the command that queues the sell. p11 executes it at the next run's open.
document.querySelectorAll('.sellbtn').forEach(function (b) {{
  b.addEventListener('click', function () {{
    var sym = b.dataset.sym;
    var cmd = 'python manual_sell.py ' + sym;
    if (!window.confirm('Queue a MANUAL SELL of ' + sym + '?\\n\\n' +
        'This copies the command below. Paste it in a terminal in the project folder to ' +
        'queue the order.\\n\\n  ' + cmd + '\\n\\n' +
        'It fills at the NEXT session\\'s open and is tagged reason="manual" in the ledger.')) {{
      return;
    }}
    var done = function () {{
      b.textContent = 'Copied';
      b.classList.add('done');
      setTimeout(function () {{ b.textContent = 'Sell'; b.classList.remove('done'); }}, 2500);
    }};
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(cmd).then(done, function () {{ window.prompt('Copy this command:', cmd); }});
    }} else {{
      window.prompt('Copy this command:', cmd);
    }}
  }});
}});
</script>
 <div class='sub' style='margin-top:6px'>Library and charts cover the LIVE ERA ONLY.
  Backtest trades are deliberately never merged in - see DECISIONS.md #001-b. Chart lines
  are daily CLOSES while fills happen at the OPEN, so a marker may sit off the line on a
  gap day.</div>
 <div class='sub' style='margin-top:14px'>Paper simulation: fills at next open +/-
  {C.SLIPPAGE_BPS:.0f}bp slippage; costs = full NSE equity-delivery stack (STT both sides,
  exchange txn, SEBI, stamp, GST, DP on sell). Nothing here exists in the real Zerodha
  account. Generated by p11b_swing_report.py.</div>
</div>"""
    OUT.write_text("<!doctype html><meta charset='utf-8'><title>paper book</title>" + body,
                   encoding="utf-8")
    return str(OUT)


if __name__ == "__main__":
    path = build()
    print(f"dashboard written: {path}")
