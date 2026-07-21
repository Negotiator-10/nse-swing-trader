# nse_paper_v1 — Program Status

Real-time **NSE paper-trading** system. Single platform: **Zerodha Kite Connect + this
Python code** (no TradingView in the core, no market making — retail cannot MM NSE).
Live Kite market data → directional/systematic signal → VaR-based sizing → **local
paper-fill simulator** (Kite has no order sandbox) → live-vs-backtest reconciliation.

Reuses the **GARCH-EVT VaR engine** (`C:\Users\Admin\Desktop\VaR Model\`, which already
models NIFTY & BANKNIFTY) and the **streaming architecture pattern** from
`integrated_mm_v2/phase7_shadow.py`. Full plan: `.claude/plans/i-want-to-make-ancient-torvalds.md`.
Python: `C:\Users\Admin\anaconda3\python.exe`.

## v1 scope — SWING (pivoted; intraday NIFTY is a LATER track)

- **Style:** SWING — per-stock daily signals on a small **watchlist**, hold ~10-15 days
  (hard max < 1 month), **end-of-day** decisions (no real-time streaming needed for v1).
- **Watchlist (default, editable):** RELIANCE, HDFCBANK, INFY, TCS, ICICIBANK.
- **Segments:** ONE signal+risk brain on the underlying; two pluggable paper-execution
  backends — **equity delivery (cash)** first (long-only), then **stock futures** (leverage +
  shorting, monthly expiry).
- **Signal (default):** momentum/breakout + trend filter + ATR stop, VaR-sized. v1's job is to
  prove the SYSTEM; the signal is honestly gated for OOS edge net of full NSE costs (a no-edge
  finding is valid).
- **Data:** yfinance DAILY (deep: 1996/2002 -> 2026). The daily GARCH-EVT VaR engine is a
  natural fit for multi-day/overnight risk. Broker (Kite etc.) only needed for LIVE orders.
- **Regulatory:** paper stage is regulation-light (no real orders); a daily swing strategy is
  trivially under SEBI's algo thresholds.

## Phases (gated; SWING v1)

| # | Module | Delivers | Status |
|---|--------|----------|--------|
| 0 | `config.py` + scaffold | skeleton, IST hours, cash+futures cost stacks, secrets | **DONE** |
| 1 | `p1_swing_data.py` | daily OHLCV panel for the watchlist + IS/OOS split | **DONE** (34,892 bars, 5 stocks, 1996-2026, cutoff 2017-03-23, 4 gates PASS; 68 pre-1997 OHLC adj-artifacts repaired) |
| 0b | `p0_universe.py` | screen NIFTY 50 by ex-ante tradability -> 20-stock watchlist (no return peeking) | **DONE** (20 selected, JIOFIN dropped) |
| 2 | `p2_swing_signal.py`, `p3_swing_backtest.py`, `p4_signal_search.py`, `p5_swing_robustness.py`, `p6_swing_combine.py`, `p7_diversifier_search.py`; docs `p2_{summary,learning}.docx` | causal signal library + backtest + honest search + hardening + diversifier + write-up | **DONE** — breakout_mom NO-EDGE; honest search (select-IS/confirm-OOS/deflate x4) picked **ts_mom**: OOS **+54.5bp/trade**, deflated CI LB **+11.5bp**, **17/20 stocks +ve**, hold 12.4d. Caveats: survivorship (current NIFTY50), losing yrs 2018/22/26, per-trade edge != beats buy&hold |
| 3 | `p8_swing_risk.py`, `p9_swing_portfolio.py`; docs `p3_{summary,learning}.docx` | VaR-engine port: per-stock GARCH-EVT sizing + **risk-parity ts_mom+tom sleeves** + portfolio cap + kill switch + equity curve | **DONE** — 7 gates PASS; risk-parity combo OOS Sharpe **0.70** / maxDD **-5.0%** beats ts_mom-alone (0.49/-7.3%) on BOTH axes; but book ~15% invested so CAGR 2.2% << buy&hold 15.5% (per-trade edge != total return; risk budget is a dial; idle cash earns 0 in sim) |
| 4 | `p10_swing_feed.py`, `p11_swing_paper.py`, `kite_login.py`; `step_day` refactor in p9; docs `p4_{summary,learning}.docx` | live Kite EOD feed + local paper-fill engine (REPLAY/LIVE, one code path) | **DONE** — 6 gates PASS in LIVE; online==batch byte-for-byte (same hash); first real decision 2026-07-17 (armed AXISBANK ts_mom); caught+fixed the `tom` live-edge bug (every live day looked like turn-of-month; hash-proof zero backtest impact) |
| 5 | `p12_swing_reconcile.py` (tbd) | paper broker + live-vs-backtest reconciliation (needs accumulated live-paper days) | pending |
| 6 | `p13_swing_daily_job.py` (tbd) | EOD scheduled live-paper loop (wrap p11 in a scheduler) | pending |
| 7 | ops + go-live readiness (SEBI algo framework applies) | | pending |

**Phase 2 hardening (`p5_swing_robustness.py`) — ROBUST.** ts_mom OOS +54.5bp/trade survives:
R1 params 16/16 one-at-a-time configs positive (plateau, not a peak); R2 cost stress break-even
~+28bp extra slippage/side (~10x modeled); R3 random-entry null p≈0.000 (entry timing adds ~48bp
over random — real signal, not beta); R4 out-of-universe on 12 Next-50 names +79.6bp CI[+23,+141]
9/12 positive (generalizes). Residual caveats: Next-50 also survivors; period 2017-26 (2018/22 down
years); per-trade edge != total return (needs Phase-3 sizing).

**Phase 2 diversification (`p6_swing_combine.py`) — MIXED/HONEST.** Mean-reversion family
(mr_rsi/mr_bb/mr_down3) has **NO proven stand-alone edge** (all OOS CIs include 0). BUT mr_bb is
near-uncorrelated with ts_mom (monthly corr +0.08); a 50/50 combined book cuts max drawdown
-18.7%->-11.6% and lifts Sharpe 0.71->0.78. Caveat: that's RISK REDUCTION, not proven alpha
(mr_bb return not significant) + some OOS-selection optimism. Recommendation: ts_mom primary +
SMALL MR overlay (~20-25%), OR keep ts_mom single-sleeve. Decision pending.

**Phase 2 diversifier search (`p7_diversifier_search.py`) — found `tom`.** Pre-registered
distinct families, deflated x9. vol_squeeze failed (corr +0.37, no edge). **turn-of-month (`tom`)
is a real, uncorrelated diversifier**: OOS +39bp, deflated CI LB **+2.8bp** (survives 9x
Bonferroni), corr to ts_mom **+0.22**, combined Sharpe 0.71->0.75. Caveats: MARGINAL significance
(TOM effects decay), naive 50/50 didn't cut drawdown. Conclusion: STOP signal hunt (9 screened =
multiple-testing tax); carry **ts_mom (primary) + tom (secondary)** into Phase 3 and let VaR
RISK-WEIGHTING do the combination (the principled way, not hand-tuned blends).

**Phase 3 risk layer (`p8_swing_risk.py` + `p9_swing_portfolio.py`) — DONE, all 7 gates PASS.**
Ported the GARCH-EVT VaR path per stock (numba-accelerated GJR-GARCH + GPD tail), walk-forward,
strictly causal (G3.3: as-of recompute on truncated data reproduces the panel, 0 mismatches).
105,717 snapshots, 92,108 tradable (87%); median 99%/12d VaR = 21.1% of price, ES 27.8%, tail
xi +0.13. Sizing invariant `shares*var_per_unit<=budget` holds on all 92,108 (G3.1). **Risk-parity**
sleeve weighting (inverse realized vol, walk-forward) replaced the 50/50: mean weights ts_mom
0.42 / tom 0.58 — combined book beats ts_mom-alone on BOTH Sharpe (0.70 vs 0.49) and drawdown
(-5.0% vs -7.3%) OOS → `tom` earns its place (G3.6). Portfolio cap (Rs.80k risk budget / 8 names /
20% notional / no leverage) BINDS — 9,135 skips normal, proven under a tight cap (G3.4b).
Rolling-drawdown kill switch (20% / 21d cooldown) flatten+halt proven under fault injection with
0 post-kill fills (G3.4a). Byte-identical equity path on rerun (G3.5). **HONEST HEADLINE:** the
book is drawdown-control, not return — mean exposure ~15% (conservative budget) → combo OOS CAGR
2.2% vs buy&hold 15.5%, but full-period maxDD -12.9% vs buy&hold -75.3%. The risk budget is a
DIAL; idle cash earns 0 in the sim (understates absolute return by ~+5% CAGR). Lesson: ATR stops
are the first line of defense (they flatten before the kill switch on a price crash — kill switch
is the portfolio backstop + re-entry halt). Handoffs: `data/swing_risk_panel.parquet`,
`data/swing_equity.parquet`, `artifacts/p3_{risk,portfolio}.json`.

**Phase 4 live paper trader (`p10_swing_feed.py` + `p11_swing_paper.py`) — DONE, 6 gates PASS in LIVE.**
The shadow pattern from integrated_mm_v2: TWO run modes (REPLAY/LIVE), ONE code path. p9's day
loop refactored into a shared `step_day(BookState, DayCtx)` — G4.1 proves online==batch
BYTE-FOR-BYTE (equity err 0.0, 2,848/2,848 trades identical, same sha `feb7869eeb87dda8`).
Daily-bar feed discipline (dup/out-of-order/malformed/holiday/dead-man, all fault-injected,
G4.2); paper fills = next-open + slippage + full NSE delivery costs (hand-check exact, G4.3);
state persists to `paper_state.json` + `paper_ledger.parquet`, kill/reload/resume ==
uninterrupted (G4.3); online cap-binding + kill-switch under injected crash (G4.4); byte-identical
rerun + presence-only secrets (G4.5). **G4.6 LIVE (2026-07-17): 20/20 fresh Kite bars, source
diff vs yfinance tape 0.0bp, 100 bars backfilled (tape lagged a week), 20 fresh GARCH-EVT
snapshots, book Rs.22.48L with 6 open positions, decision armed AXISBANK (ts_mom, Rs.15,112).**
FINDING: first live decision exposed the `tom` LIVE-EDGE BUG — "last trading day of month" was
decided vs the NEXT bar's month, and on the live edge (no next bar) every day counted as
turn-of-month; invisible in backtest (final-bar entries never fill). Fixed with a next-business-
day stand-in for the final bar; equity hash UNCHANGED = zero-impact proof. Daily ritual:
`kite_login.py` (token expires each morning), then after 15:30 IST run p11 with RUN_MODE=LIVE.
Caveats: two data sources (yfinance adjusted vs Kite) — agreement measured every run, <2% gate;
`tom` business-day stand-in can mislabel holiday-adjacent month-ends (Phase-6 fix = NSE holiday
calendar); paper fills are modeled — Phase 5 reconciles.

**2026-07-18 (final) — Option B adopted: Rs.1,800/position (3%), max 4 concurrent.** All gates
PASS, hash `bb1a6ecb40656ab7`; OOS CAGR 1.06% / Sharpe 0.23 / maxDD -10.7% (~3x the 8x900
variant — fewer round trips, less flat-cost drag); G3.6 verdict restored (risk-parity beats
ts_mom-alone on both axes at this size). Pending: user's RSI strategies to be pre-registered
and tested as trials 10+ under the deflation protocol.

**2026-07-18 (superseded) — first re-baseline to Rs.60k capital (user's real future budget).** Per-position risk
Rs.900 (1.5%), portfolio Rs.4,800 (8%); full chain rerun, all gates PASS, new equity hash
`75cddb653e9beaa6`. **HONEST FINDING: at Rs.60k the edge is nearly cost-bound** — OOS combo
CAGR 0.3% / Sharpe 0.10 (vs 2.2%/0.70 at the Rs.10L research size): the flat DP charge
(Rs.15.34/sell) is ~0.35% on ~Rs.4.3k positions, and price granularity squeezes expensive
names (INDIGO-class) out of the Rs.900 budget. G3.6 verdict flipped at this size (risk-parity
no longer dominates ts_mom-alone). Open decision: a concentration variant (3%/position, max
3-4 names) would halve flat-cost drag at the same total risk — pending user choice. The Rs.10L
results remain documented in docs/p3-p4 docx.

**2026-07-17/20 — LIVE ERA.** Config `PAPER_ERA_START=2026-07-17`: the live paper book starts
FLAT at Rs.60,000 (pre-era history = warmup/evidence only). **Day 1 (Mon 2026-07-20):** 4 fills
at the open from Friday's signals (AXISBANK 7@1275.38, ICICIBANK 8@1444.93, SBIN 6@1041.91,
BAJFINANCE 2@1057.32; INDIGO+SHRIRAMFIN cap-skipped), true-close equity **Rs.60,114.55
(+114.55)**; AXISBANK armed trend-exit + INDIGO/SHRIRAMFIN armed buys for Tue open. Two live
bugs caught+fixed: decision-durability (risk snapshots now computed for EVERY new bar date and
persisted, dedup by symbol+date) and a premature morning run on a partial bar (timer quoting
bug; p11 now REFUSES today-dated bars before 15:45 — plus clock-vs-web-API verification rule).
**Ops tooling:** `Desktop\Hedge funds\DAILY_KICKOFF.md` (paste-into-fresh-chat daily handoff; lives one level UP from the project, in the Hedge funds root, per user preference),
`p11b_swing_report.py` → `reports/paper_dashboard.html` (Kite-style book: NAV/KPIs, positions
marked to close, armed orders, blotter with per-trade NSE cost breakdown; auto-refreshes after
every LIVE run; NAV history in `data/paper_equity_curve.parquet`). Daily ritual: user runs
`kite_login.py` (morning), says "run it" (after 15:30). Pending: user's RSI/GFS strategy
confirmation (see `Desktop\stock market\*.STRATEGY_NOTES.txt` + `COURSE_VIDEO_CATALOG.txt`).

**2026-07-21 — LIVE DAY 2 (first realized trade) + dashboard/doc upgrades.** AXISBANK
**SOLD** at the open 1,244.6265 (raw open 1,245.00, `trend` exit, held 1 day): price
−215.29, costs −34.96, **net −Rs.250.25** — the flat DP charge (Rs.15.34) was 44% of all
costs, STT both sides 50%. INDIGO **BOUGHT** 1 @ 5,250.57. SHRIRAMFIN cap-skipped a
second day (the AXISBANK exit freed only ONE slot). Book 4/4 at cap, cash Rs.34,573.60,
**NAV Rs.59,971.40** (day −143.15; −28.60 vs the Rs.60k start). 20/20 fresh Kite bars,
source diff 0.0 bp, all 6 gates PASS.
**New:** `DECISIONS.md` (append-only change/decision log — exists to stop the system
being redesigned in reaction to single bad days) and two dashboard sections in
`p11b_swing_report.py`: **per-stock trade charts** (SVG close line, BUY/SELL markers,
shaded hold span, stop line) and a **Library** of per-symbol aggregates. Both are
LIVE-ERA ONLY and never merged with backtest trades. Chart price source is
`swing_risk_panel.parquet` `ref_px` — the only store carrying live-era bars
(`swing_daily.parquet` stops at 2026-07-10). Display-only, proven by re-running the
chain: equity hash `bb1a6ecb40656ab7` **unchanged**.
**Deferred (DECISIONS #001-c/d):** faster exits on fast drops, and an intraday minutes
parameter. Finding: the ATR stop *already* rests intraday and books same-day exits off
the bar's low — only the MA50 trend exit is next-open by design. The real weakness is
that stops are modeled as filling *exactly* at the stop price (optimistic), so the fix
is more conservative fill assumptions, not faster exits — blocked on Phase 5 measuring
it. Intraday is blocked by the absence of intraday validation data (a 9-year edge cannot
be re-validated on 60 days of 5m bars), the daily-fitted GARCH-EVT engine, and the ops
jump. Noted fairly: intraday exit *timing* does not change tax — a multi-day hold is a
delivery sale either way.
**Open finding (DECISIONS #002):** SBIN's stop moved DOWN 990.95 → 990.35 across runs,
though in-run it is a hard ratchet `max(stop, close − 3.5×atr)` — meaning the replayed
history itself changed. G4.1/G4.5 cannot catch this (they hash the pre-era backtest
tape, not the live book). Assigned to Phase 5; do NOT clamp the stop before the cause is
understood.

**2026-07-21 (cont.) — "Costs paid" section + `SESSION_LOG.md`.** Dashboard gained a full
friction breakdown (STT / DP / stamp / exchange / SEBI / GST + slippage reconstructed out
of the fill prices), shown in TWO columns: *charged to NAV* vs *incurred but not yet
charged*. `SESSION_LOG.md` added as the per-session narrative record (`DECISIONS.md` =
why the system is built this way; `SESSION_LOG.md` = what happened that day). Both
display/doc only — chain re-run, hash `bb1a6ecb40656ab7` unchanged.
**Open finding (DECISIONS #003):** `_do_exit` charges the ENTIRE round-trip cost at EXIT
and nothing at entry, so marked NAV is **overstated** while positions are open by the
unbooked buy-side STT + stamp (~Rs.29.88 on Rs.25,176 of open notional today, 0.05% of
NAV). Lifetime cost is identical — this is an accrual error, not a P&L error — but it
always flatters the book mid-flight and scales with the number of open positions. The
engine is deliberately NOT changed (that would touch trading logic); Phase 5 decides.
**Why the book holds so much cash (answered):** cash is 57.6% of NAV but **94.3% of the
risk budget is used** (Rs.4,527.26 of Rs.4,800). Sizing is by tail risk, not capital —
median VaR ~21% of price means Rs.1 of risk budget supports only ~Rs.4.8 of stock, so
Rs.4,800 caps notional near Rs.23,000 no matter how much cash is idle. Full investment
would require a ~Rs.12,600 budget (21% of book at 99%/12d) against today's 8%. The cash
IS the drawdown control.

*Intraday NIFTY track (later): `p1_data.py` DONE (4,205 NIFTY 5m bars, 4 gates PASS) — parked.*

## Data strategy (unblocked)

Development uses **yfinance** free intraday for NIFTY (`^NSEI`) / BANKNIFTY (`^NSEBANK`) — real
data, no keys — to build and honestly gate Phases 1-3. The index is an intraday proxy for the
near-month future. yfinance history is short (5m ~60 days), so a longer walk-forward comes from
**Kite historical (paid)** later; **Kite is required for the LIVE feed (Phase 4+)** regardless.
The VaR model's existing DAILY multi-year NIFTY/BANKNIFTY data feeds the risk layer (Phase 3).

## Conventions (inherited)

Gated asserts (a failed gate is a finding — never loosen a threshold); typed
parquet/npz/json handoffs + reload smoke test; prose as `.docx` via `md_to_docx.py` (write
`.md` → convert → delete `.md`; only `.py` stays source); ASCII-only prints (Windows cp1252);
fixed seed; concept-first teaching + checkpoint exercise. Secrets from env vars only —
never in chat/logs/commits.
