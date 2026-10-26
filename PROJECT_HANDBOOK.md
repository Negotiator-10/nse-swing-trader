# PROJECT HANDBOOK — nse_paper_v1

**The one document to read when you have forgotten everything.**
Written 2026-07-21. Covers the project from its first line of code to the current live
paper-trading book.

---

## 0. HOW TO READ THIS, AND HOW RELIABLE IT IS

Read section 1 to remember what this is, section 4 to remember how it makes money
decisions, and section 9 to operate it tomorrow. The rest is reference.

**Where the facts here come from, so you know what to trust:**

| Source | Reliability |
|---|---|
| The code itself (read directly) | **Fact.** Verified while writing this. |
| `PROGRESS.md`, `DECISIONS.md`, `artifacts/*.json` | **Fact.** Written at the time by the runs themselves. |
| Session of 2026-07-21 (questions + answers) | **Fact.** Recorded in `SESSION_LOG.md`. |
| Questions asked in sessions BEFORE 2026-07-21 | **NOT RECOVERABLE — see below.** |

**The one honest gap.** You asked for "every question I asked". For sessions before
2026-07-21 that does not exist and cannot be reconstructed. Those chats were never
captured — `SESSION_LOG.md` only begins on 2026-07-21, which is the very limitation that
prompted creating it. For the earlier period this handbook documents **what was built and
decided** (reliable, from code and artifacts) but does **not** invent a list of questions
you supposedly asked. From 2026-07-21 onward, every question is recorded — see section 8.

---

## 1. WHAT THIS PROJECT IS

A **swing paper-trading system for Indian stocks (NSE)**.

- **Paper** means: it makes real decisions on real live market data, and simulates the
  fills locally. **No order ever reaches Zerodha.** Nothing appears in your real account.
- **Swing** means: it decides once a day after the close, and holds positions ~10–15 days
  (hard max under a month). It is not intraday and not high-frequency.
- **Systematic** means: rules decide everything. No discretion, no overriding a signal
  because it feels wrong.

**One platform:** Zerodha Kite Connect (market data in) + this Python code. Kite's paid
plan is needed **only for live data**, not for orders.

**Universe:** 20 NIFTY-50 stocks, chosen by liquidity, not by past returns (section 3.1).

**Capital:** Rs.60,000 — your real intended budget, not a fantasy number.

### What it is NOT
- Not a market maker. Retail cannot market-make NSE (no maker rebates, throttled data,
  SEBI algo rules). This was investigated and ruled out.
- Not intraday. That track exists (`p1_data.py`, NIFTY 5-minute bars) but is **parked**.
- Not live trading. Real orders would trigger SEBI's retail-algo framework (Algo-ID,
  static IP, broker onboarding — mandatory since 2026-04-01). That is Phase 7 and a
  separate, deliberate decision.

### The lineage
You previously built a **GARCH-EVT VaR engine** (`Desktop/VaR Model`) and a **crypto
market maker** (`integrated_mm_v2`, finished). This project reuses the VaR engine's risk
maths and the market maker's architectural discipline (shadow/replay pattern, gates).

---

## 2. THE CORE PHILOSOPHY (why the project is built this way)

Everything here follows one idea: **it is easy to fool yourself into believing a strategy
works.** Nearly every rule exists to make self-deception harder.

1. **A failed gate is a FINDING, never something to loosen.** If a test fails, the test
   is right and the code is wrong. You do not adjust the threshold.
2. **Select on in-sample, confirm ONCE out-of-sample, then deflate.** You may look at the
   hold-out data once. Testing many ideas until one passes is just overfitting the
   hold-out, so results are Bonferroni-deflated by the number of ideas screened.
3. **A "no edge" result is a valid, successful outcome.** Several ideas were killed. That
   is the system working.
4. **Causality / no look-ahead.** Nothing may use information it could not have had at the
   time. Enforced by automated gates, not good intentions.
5. **State caveats, never bury them.** Every known weakness is written down (section 7).

---

## 3. THE JOURNEY — WHAT WAS BUILT, PHASE BY PHASE

*(Note: file numbering `p0…p11` does NOT match phase numbering. Mapping given below.)*

### Phase 0 — Scaffold + universe (`config.py`, `p0_universe.py`) — DONE
Built the configuration spine (trading hours, NSE cost stacks, risk parameters, secrets
handling) and chose the watchlist.

**The key idea — the bias trap.** Picking the stocks that performed best historically and
then backtesting on them is survivorship bias; the backtest looks good for a fake reason.
So the 20 stocks were screened on **ex-ante tradability only** — liquidity (median daily
traded value), length of history, data quality — and explicitly **not** on returns.

**Watchlist:** HDFCBANK, ICICIBANK, RELIANCE, INFY, BHARTIARTL, SBIN, ETERNAL, TCS, M&M,
AXISBANK, LT, BAJFINANCE, KOTAKBANK, MARUTI, BEL, SHRIRAMFIN, INDIGO, ITC, TATASTEEL,
HINDALCO. (JIOFIN dropped — insufficient history.)

### Phase 1 — Data (`p1_swing_data.py`) — DONE
Daily OHLCV for all 20 stocks from yfinance, deep history (1996/2002 → 2026).
**125,737 bars.** 4 gates PASS.

**Two decisions worth remembering:**
- **Per-symbol walk-forward split.** Each stock's last 30% is its test set — *not* one
  common calendar cutoff, which would break recently-listed names like ETERNAL (2021).
- **Data-quality finding:** 68 pre-1997 bars (0.19%, INFY/HDFCBANK) had adjustment-rounding
  artifacts where high/low were inconsistent with open/close. Repaired by clamping, and
  the count was reported rather than hidden.

### Phase 2 — Signals (`p2`, `p3`, `p4`, `p5`, `p6`, `p7`) — DONE, the intellectual core
This phase asked one question honestly: **does any rule actually predict anything, after
costs?**

**Results:**
- **`breakout_mom` — NO EDGE.** Out-of-sample +18.4bp with a confidence lower bound of
  −29.2bp. In-sample had flattered it at +62bp. Killed. *(This is the discipline working.)*
- **`ts_mom` — THE WINNER (primary signal).** Buy when: close > MA200 **and** 126-day
  return > 0 **and** close > MA50. Trailing ATR stop at 3.5×, max hold 25 days.
  - **OOS +54.5 bp per trade**, net of full delivery costs
  - Deflated confidence lower bound **+11.5 bp** (survives the multiple-testing tax)
  - **17 of 20 stocks positive**, average hold 12.4 days
- **Mean-reversion family (`mr_rsi`, `mr_bb`, `mr_down3`) — NO PROVEN EDGE.** All OOS
  confidence intervals include zero. `vol_squeeze` also failed.
- **`tom` — SECONDARY, MARGINAL.** Turn-of-month effect with a trend filter. OOS +39bp,
  deflated lower bound **+2.8bp** — it survives, but barely. Its value is that it is
  **uncorrelated** with ts_mom (+0.22), lifting combined Sharpe 0.71 → 0.75.

**Hardening (`p5`) — ts_mom survived all four stress tests:**
| Test | Result |
|---|---|
| R1 parameter sensitivity | 16/16 nearby settings positive → a plateau, not a lucky peak |
| R2 cost stress | Break-even needs ~+28bp extra slippage (~10× what is modeled) |
| R3 random-entry null | p ≈ 0.000 — entry timing adds ~48bp over random. **Real signal, not just market beta.** |
| R4 out-of-universe | +79.6bp on 12 Next-50 names it was never selected on, 9/12 positive → generalizes |

**The stopping rule.** The signal hunt was **deliberately stopped at 9 ideas screened**,
because every extra idea raises the multiple-testing tax on everything else.

### Phase 3 — Risk engine (`p8_swing_risk.py`, `p9_swing_portfolio.py`) — DONE, 7 gates PASS
A per-trade edge is **not** a P&L. This phase turned edges into a book.

**Per stock, per day it answers: "how much can one share lose over ~12 days?"**
1. **GJR-GARCH(1,1)** volatility filter (asymmetric — crashes raise volatility more than
   rallies do), numba-accelerated
2. **EVT / Generalized-Pareto** fit on the tail of standardized residuals
3. Aggregate to a **99% loss over a 12-day horizon** → `var_per_unit`

Measured across **105,717 snapshots** (92,108 tradable): median 99%/12-day VaR = **21.1%
of price**, expected shortfall 27.8%, tail index xi +0.13.

**Deliberately zero-drift** — no expected return is baked into the risk number, keeping
the edge out of the risk estimate.

**Sleeve weighting: risk-parity** (inverse realized volatility, walk-forward) replaced
Phase 2's hand-tuned 50/50. Mean weights ts_mom 0.42 / tom 0.58. The combined book beat
ts_mom-alone on **both** Sharpe (0.70 vs 0.49) and drawdown (−5.0% vs −7.3%) — which is
how `tom` earned its place.

**THE MOST IMPORTANT HONEST FINDING OF THE PROJECT:**
> The book is **drawdown control, not return**. Mean exposure ~15% → OOS CAGR 2.2% versus
> buy-and-hold 15.5%. But full-period max drawdown **−12.9% versus buy-and-hold −75.3%**.
> The risk budget is a **dial**. Idle cash earns 0% in the simulation, understating real
> returns by roughly +5% CAGR.

### Phase 4 — Live paper trader (`p10_swing_feed.py`, `p11_swing_paper.py`) — DONE, 6 gates PASS
Turned the backtest into something that runs on live data.

**The reconciliation spine (why this is trustworthy).** The batch backtest and the live
online loop call the **identical** `step_day()` function — one implementation, zero drift
possible. Gate **G4.1 proves** replaying the tape one day at a time reproduces the batch
equity path and trade list **byte-for-byte** (same SHA). Once that identity holds, feeding
it a live Kite bar instead of a tape row is a *data swap, not new logic*.

**Feed discipline** — duplicates, out-of-order bars, malformed bars, holidays, and a
dead-man timer are all detected and fault-injected in tests. (A broken token looks exactly
like a holiday; this makes the difference loud.)

**The `tom` LIVE-EDGE BUG — caught by the first live decision.** "Last trading day of the
month" was decided by comparing against the *next* bar's month. On the live edge there is
no next bar, so **every live day looked like turn-of-month**. Invisible in backtest,
because final-bar entries never fill. Fixed with a next-business-day stand-in; the equity
hash was **unchanged**, proving zero backtest impact.

### The LIVE ERA — 2026-07-17 onward
Config `PAPER_ERA_START = "2026-07-17"`: the live book starts **flat at Rs.60,000** on that
date. All earlier history is warm-up and evidence only.

**Day 1 — Mon 2026-07-20.** 4 fills at the open from Friday's signals: AXISBANK 7 @
1275.38, ICICIBANK 8 @ 1444.93, SBIN 6 @ 1041.91, BAJFINANCE 2 @ 1057.32. INDIGO and
SHRIRAMFIN were cap-skipped. Close NAV **Rs.60,114.55**.

**A critical bug caught on day 2 — decision durability.** Live runs recomputed risk
snapshots only for the newest day and never persisted them, so a previous day's armed
decisions silently vanished from later replays. Fixed: snapshots are now computed for
**every** new bar date and persisted. *Lesson: decisions must survive across process runs.*

**Day 2 — Tue 2026-07-21 (today).** First realized trade:
- **AXISBANK SOLD** at the open 1,244.63 (trend exit — closed below MA50), held 1 day.
  Price −215.29, costs −34.96 → **net −Rs.250.25**
- **INDIGO BOUGHT** 1 @ 5,250.57
- **SHRIRAMFIN skipped again** — the AXISBANK exit freed only *one* slot
- **NAV Rs.59,971.40** (day −143.15; −28.60 versus the Rs.60,000 start)

---

## 4. HOW A TRADE ACTUALLY HAPPENS (the part worth re-reading)

> **Current as of 2026-08-09, baseline hash `d3a7e18d4002dde2`.** This section was rewritten
> after DECISIONS #010–#018 changed the chain. If you are reading a printout that says
> "max 4" or "Rs.1,800", it predates #011/#010 and is wrong.

### Step 1 — At the close, a signal fires
`ts_mom`: close > MA200 **and** 126-day return > 0 **and** close > MA50.
Skipped if the stock is already held, already armed, or already pending exit.
Single sleeve since #013 — `tom` was retired for failing its own gate.

### Step 2 — Candidates are RANKED against each other  *(new, #018)*
Roughly **37 names signal on a typical day and there are only 6 slots**, so the order they
are served in decides the book. They are sorted by

    score = (126-day return) / (stdev of daily log returns over 126 days)

— risk-adjusted momentum, best first. NaN sorts last, ties break by symbol index.
The score is captured at the decision close, so tomorrow's ordering cannot peek at
tomorrow's tape. **Before #018 this order was the alphabet**, and every position the live
book had ever taken began with A or B.

`ts_mom` asks "is this name trending?" (time-series momentum). The score asks "which of the
trending names is best?" (cross-sectional). You need both.

### Step 3 — Risk sizing decides the size
`budget = Rs.5,400 × sleeve weight`. Single sleeve ⇒ weight is 1.0, so the budget is the
flat Rs.5,400. Then `shares = floor(budget / var_per_unit)`.

**The single most important thing to understand about the system: positions are sized by
TAIL RISK, not by cash.** Median 99%/12-day VaR is ~21% of price, so **one rupee of risk
budget supports only about Rs.4.80 of stock.**

### Step 4 — The order is ARMED for the next open
Nothing executes at the close. The decision is recorded and waits.

### Step 5 — At the next open, five caps apply (each can only SHRINK the size)
Applied in this order, best-ranked candidate served first:

1. **Per-position risk** — `floor(5,400 / var_per_unit)`
2. **Per-name notional** — ≤ 20% of equity in any single stock *(both inside `size_position`)*
3. **Position count** — max **6** concurrent names
4. **Portfolio risk room** — `floor((14,400 − open_risk) / var_per_unit)`
5. **Gross exposure ≤ equity, then cash** — and since #016 the cash test must also cover the
   **buy-side cost charged at entry**, not just the notional. **No leverage, ever.**

Zero shares → logged as a skip **with its reason, its score, and the incumbents it lost
to**. Otherwise it fills at `open × 1.0003` (3bp slippage) and the stop is set at
`entry_px − 3.5 × ATR`.

Note that caps 3 and 4 are *portfolio* constraints: a perfectly good candidate is turned
away because the book is full, not because anything is wrong with it. That is the queue the
skip log exists to measure.

### Step 6 — While the position is open, two different exits watch it
- **ATR trailing stop — rests INTRADAY.** If the day's low breaches it, the exit is booked
  that same day at the stop price; a gap-down fills at the open. **This is the crash
  defence and it is not a day late.**
- **Trend exit (close < MA50) or max-hold** — arms a market exit at the *next* open.
  Deliberately slower; it is a regime filter, not a risk stop.

### Step 7 — Portfolio backstop
A rolling-drawdown **kill switch** (20% from peak, 21-day halt) flattens the book and halts
re-entry. Note the ordering: ATR stops are the first line of defence and usually flatten a
crashing book *before* the kill switch ever fires.

**Know this limitation cold — it is the sharpest question the project attracts.** The switch
bounds a single **leg**, not the cumulative path: it flattens at −20% from the *running*
peak, halts 21 days, then re-arms with the peak **reset to current equity**. In a grinding
bear market a sequence of bounded legs compounds well past 20% from the original high.
That is exactly what happened in 2008–09: the switch fired (4 kill exits in 2008; 44 across
the whole history) and the book still went from Rs.153,513 on 2005-07-28 to Rs.74,545 on
2009-03-06 — **a −51.4% drawdown under a 20% kill switch.**

The generalizable lesson: ATR stops plus a drawdown switch handle **fast** crashes well and
**slow** ones badly. The OOS max drawdown of −19.2% partly reflects that the worst event in
that window was COVID — a V-shaped crash, the shape this machinery is best at. Do not claim
−19.2% generalizes to a 2008-shaped market.

### WHERE THE MONEY SITS, AND WHY (rewritten 2026-08-09)
This answer **inverted** when the risk dial was raised (#010: per-position 3%→9%, portfolio
8%→24%) and the book widened (#011: 4→6 names).

- **Before**, at the 8% dial: cash was ~58% of NAV while the *risk* budget was 94% used. The
  cash was not idle by oversight — it was the arithmetic consequence of the risk cap. Being
  fully invested would have needed a risk budget of ~Rs.12,600, not Rs.4,800.
- **Now**, at the 24% dial: the book runs ~95% invested (cash Rs.2,895 on 2026-08-07) and the
  **binding constraint has moved from the risk budget to the 6-position count cap** — which
  is precisely why ~31 candidates get turned away every day.

**The general principle survives both regimes: sizing is by tail risk, and whatever is not
deployed is the drawdown cushion.** What changes with the dial is *which* cap binds. Being
able to say which one binds today, and why, is the answer that demonstrates you understand
your own book.

### THE COST STACK (every rupee of friction)
| Component | Rate | Note |
|---|---|---|
| **STT** | **0.1% on BOTH sides** | Delivery trades are taxed on both legs. Intraday would be 0.025% sell-side only — but a multi-day hold is always delivery. |
| **DP charge** | **Flat Rs.15.34 per sell** | Does not scale. On a Rs.8,900 position it was **44% of all costs.** The tax on being small. |
| Stamp duty | 0.015% (buy only) | |
| Exchange txn | 0.00297% | |
| SEBI turnover | 0.0001% | |
| GST | 18% on (exchange + SEBI) | |
| Brokerage | **Rs.0** | Zerodha delivery is free |
| Slippage | 3 bp per side | Modeled, embedded in fill prices |

**Worked example — the AXISBANK round trip:** bought 7 @ raw open 1,275.00 (filled
1,275.3825), sold 7 @ raw open 1,245.00 (filled 1,244.6265). Price loss −215.29, costs
−34.96 (STT 17.64 + DP 15.34 + stamp 1.34 + exchange 0.52 + GST 0.10 + SEBI 0.02), plus
~5.29 slippage inside the prices. **Net −Rs.250.25.**

---

## 5. EVERY FILE AND WHAT IT HOLDS

### 5.1 Source code (the engine, in dependency order)
| File | What it holds |
|---|---|
| `config.py` | **The spine.** Every constant: trading hours, both NSE cost stacks, risk budgets, caps, era start, secret variable *names* (never values). Change parameters here, never in phase scripts. |
| `p0_universe.py` | Screens NIFTY 50 by ex-ante tradability → the 20-stock watchlist. Deliberately never looks at returns. |
| `p1_swing_data.py` | Daily OHLCV pipeline (yfinance) → `swing_daily.parquet` + per-symbol walk-forward split. |
| `p2_swing_signal.py` | **Signal library.** The SPECS registry (ts_mom, tom, breakout_mom, mean-reversion family…), causal builders, and the no-look-ahead gate. Contains the `tom` live-edge fix. |
| `p3_swing_backtest.py` | **Backtest engine.** Next-open fills, intraday ATR stop, trend/max-hold exits, and the full NSE delivery cost model (`delivery_roundtrip_cost`). |
| `p4_signal_search.py` | The honest search: select on IS, confirm once on OOS, deflate by trial count. |
| `p5_swing_robustness.py` | The four hardening tests (R1–R4). |
| `p6_swing_combine.py` | Mean-reversion diversification attempt (found: no proven stand-alone edge). |
| `p7_diversifier_search.py` | Pre-registered diversifier hunt → found `tom`. |
| `p8_swing_risk.py` | **GARCH-EVT risk engine.** GJR-GARCH + GPD tail fit, walk-forward, → `var_per_unit`. Contains `size_position()`. |
| `p9_swing_portfolio.py` | **THE BOOK.** `step_day()` — the single day-loop shared by backtest and live. Entries, exits, stops, caps, kill switch, cost booking (`_do_exit`). **The most important file in the project.** |
| `p9b_swing_charts.py` | Phase-3 chart pack; refuses to draw if its recompute diverges from saved data. |
| `p10_swing_feed.py` | **Feed layer.** REPLAY and LIVE (Kite) sources behind one interface + feed discipline (duplicate / out-of-order / malformed / holiday / dead-man). |
| `p11_swing_paper.py` | **The harness you run each evening.** Online loop, paper fills, state persistence, and the 6 gates. |
| `p11b_swing_report.py` | **The dashboard renderer.** Builds `reports/paper_dashboard.html`. Pure view — no API calls, no engine run. |
| `kite_login.py` | **You** run this each morning for the daily Kite token. |
| `md_to_docx.py` | Converts markdown deliverables to Word. |
| `p1_data.py` | Intraday NIFTY 5-minute pipeline — **parked track**, not used by the swing system. |

### 5.2 Data (`data/` — git-ignored, 14 MB, regenerable)
| File | What it holds |
|---|---|
| `swing_daily.parquet` | The research tape: 125,737 daily bars × 20 stocks. **Ends 2026-07-10** — live-era bars are NOT here. |
| `swing_risk_panel.parquet` | Per-stock, per-day risk snapshots (`var_per_unit`, `ref_px`, sigma, tail params). **The only store carrying live-era bars** — the dashboard charts read prices from here. |
| `swing_trades.parquet` | 2,747 backtest trades. |
| `swing_equity.parquet` | Backtest equity curve. |
| **`paper_state.json`** | **THE LIVE BOOK.** Cash, open positions, armed entries/exits, stops, kill state. |
| **`paper_ledger.parquet`** | **Closed live trades** (currently 1: AXISBANK). |
| `paper_equity_curve.parquet` | Live NAV history, one row per decision day. |
| `watchlist.json` | The 20 symbols. |
| `NIFTY_5m.parquet` | Parked intraday track. |

### 5.3 Artifacts (`artifacts/` — tracked in git)
Machine-written run records: `universe_screen.json`, `p1_swing_meta.json`,
`p2_backtest.json`, `p2_signal_search.json`, `p2_robustness.json`, `p2_combine.json`,
`p2_diversifier.json`, `p3_risk.json`, `p3_portfolio.json`, `p4_feed_selftest.json`,
**`p4_paper.json`** (the latest decision record — gates, fills, armed orders, equity),
`p3_charts.png`.

### 5.4 Documents
| File | What it holds |
|---|---|
| **`PROJECT_HANDBOOK.md`** | **This file.** The complete reference. |
| `PROGRESS.md` | Phase-by-phase status table with all headline numbers. |
| `DECISIONS.md` | **Append-only decision log.** Every proposal, its reasoning, the verdict, and the evidence. Read before proposing changes so old ground is not re-litigated. |
| `SESSION_LOG.md` | Per-session narrative (starts 2026-07-21). |
| `docs/p0…p4_{summary,learning}.docx` | Formal per-phase write-ups (10 files). |
| `../DAILY_KICKOFF.md` | Your paste-into-a-fresh-chat daily handoff. *(Lives one level up, currently unversioned.)* |
| `../EXPLAIN_MODE.md` | Paste-prompt for teaching-style deep dives. |
| `reports/paper_dashboard.html` | The visual book — NAV, positions, armed orders, blotter, per-stock charts, Library, Costs paid. Rebuilt every run. |
| `secrets.env` | **Your Kite credentials. Git-ignored. Never printed, never committed.** |
| `secrets.env.example` | Placeholder template (safe, committed). |

---

## 6. BUGS CAUGHT (the project's real track record)

| # | Bug | Why it mattered |
|---|---|---|
| 1 | **`tom` live-edge bug** | Every live day counted as turn-of-month. Invisible in backtest. Caught by the *first* live decision. |
| 2 | **Decision durability** | Armed decisions silently vanished from later replays because risk snapshots weren't persisted. |
| 3 | **Premature run on a partial bar** | A shell-quoting bug fired a timer 6 hours early. Now guarded: p11 refuses today-dated bars before 15:45. |
| 4 | **PC clock 28 hours wrong** | You caught it. Clock is now cross-checked against web sources before any run. |
| 5 | **SBIN stop drifted DOWN** (OPEN) | Stops are a hard ratchet in-run, so a decrease across runs means the replayed history changed. Assigned to Phase 5. |
| 6 | **Cost timing** (OPEN) | The whole round-trip cost is charged at *exit*, so NAV is overstated by unbooked buy-side costs while positions are open (~Rs.30 today). |

**Pattern worth noticing:** almost every bug was a **live-vs-backtest divergence** — things
that were invisible in historical simulation and only appeared on the live edge. That is
precisely why Phase 5 (reconciliation) exists.

---

## 7. CARRIED CAVEATS (state these, never bury them)

1. **Survivorship bias.** The universe is the *current* NIFTY 50; delisted names are
   absent. R4 (Next-50 test) mitigates but does not fix this. A real fix needs paid
   point-in-time index data.
2. **Sample period 2017–2026** is strong-ish, with 2018 and 2022 as down years.
3. **Per-trade edge ≠ total return.** +54.5bp per trade does not mean beating buy-and-hold.
4. **`tom` is MARGINAL** (deflated lower bound +2.8bp). Provisional.
5. **At Rs.60k the edge is nearly cost-bound.** OOS CAGR 1.06%, Sharpe 0.23, maxDD −10.7%.
   The flat DP charge is the main culprit. Performance improves materially with book size.
6. **Two data sources** (yfinance research tape vs Kite live) — agreement measured every
   run, currently 0.0bp, but will diverge on the next dividend or split.
7. **Paper fills are MODELED, not real.** Stops are assumed to fill exactly at the stop
   price, which is optimistic in fast markets. Phase 5 measures the gap.
8. **Idle cash earns 0% in simulation** — real returns understate by roughly +5% CAGR.

---

## 8. EVERY QUESTION ASKED — 2026-07-21 SESSION

*Complete and verbatim-faithful for this session. Earlier sessions were never captured
(section 0).*

| # | You asked | What happened | Status |
|---|---|---|---|
| 1 | (Pasted kickoff to start the day) | Verified clock against 3 sources; reported book state | DONE |
| 2 | Confirmed morning `kite_login.py` done | Acknowledged; token valid | DONE |
| 3 | "Which positions are we holding and what is the status?" | Read `paper_state.json`; 4 positions + live mark-to-market | DONE |
| 4 | "We agreed to exit AXISBANK at the open — why wait till close? Read the file again" | **You were right about execution.** Verified in code: fill price IS today's open; only the *recording* waits for the completed bar | DONE — clarified |
| 5 | "So we have exited AXISBANK and bought the stocks, it just shows on the afternoon run?" | Confirmed, with the honest caveat that buys remain subject to caps at fill time | DONE |
| 6 | "Is this included in the HTML interface — today's trades?" | No — dashboard was last built Monday 18:16; showed orders as *armed* | DONE |
| 7 | "Is today's session over?" | Two sources disagreed by 9 min; broke the tie (system clock was correct, `timeapi.io` was wrong) | DONE |
| 8 | "Run it" | Ran p11 LIVE. All 6 gates PASS. AXISBANK sold, INDIGO bought, SHRIRAMFIN skipped | DONE |
| 9 | "Tell me the whole AXISBANK trade, buy and sell prices" | Full reconstruction + cost stack to the paisa | DONE |
| 10 | "What is the exact time the system sold it?" | Honest answer: none exists. Daily-bar system; the ledger stores a date. Closest is the 09:15 open | DONE |
| 11 | "Why is STT on both sides?" | Delivery trades are taxed on both legs (intraday is sell-side only) | DONE |
| 12 | **6 proposals:** per-stock charts, library, faster exits, intraday parameter, change document, "tell me your thoughts first" | Assessed all 6 *before* implementing. 3 applied, 2 deferred with reasoning | DONE — `DECISIONS.md` #001 |
| 13 | "What changes will you make? Then I'll confirm" | Gave a precise spec + a self-imposed hash gate | DONE |
| 14 | "Do it" | Built `DECISIONS.md`, charts, library. Hash verified unchanged | DONE |
| 15 | "Why so much cash? Explain the buying logic. Add a costs section. Document every chat" | Answered with numbers (section 4); built Costs section + `SESSION_LOG.md`; **found the cost-timing bug** | DONE — #003, #004 |
| 16 | "Should I raise effort from medium?" | Honest answer: helps for analysis, not for the daily ritual | DONE |
| 17 | "I mean medium/high/max or ultracode" | Explained the levels; `/code-review ultra` is separate and needs git | DONE |
| 18 | "Suggest effort per task, and save it so you don't forget" | Saved to persistent memory | DONE |
| 19 | "Can you auto-switch effort?" | **No** — I cannot change or even see the setting. Proposed the handshake instead | DONE |
| 20 | "Yes, do the handshake" | Saved to memory | DONE |
| 21 | "Update `DAILY_KICKOFF.md`" | Added closing step, "HOW CLAUDE SHOULD WORK", refreshed threads | DONE |
| 22 | "Can we swap a weak stock for the next buy?" | Assessed: real inefficiency, but converts the strategy family and costs ~Rs.38/swap vs Rs.636/yr return | **DEFERRED** — #005 |
| 23 | "Start git init — what is it?" | Explained, then initialised. 2 commits, secrets verified safe | DONE — #006 |
| 24 | "Make a full project document" | **This file** | DONE |

**Not done / deliberately deferred:** faster exits (#001-c), intraday parameter (#001-d),
rotation (#005), live-book-state versioning (open question), skipped-candidate logger.

---

## 9. HOW TO OPERATE IT (the only two things that happen)

**MORNING (you, ~2 minutes):**
```
cd "C:\Users\Admin\Desktop\Hedge funds\nse_paper_v1"
C:\Users\Admin\anaconda3\python.exe kite_login.py
```
Browser login (Client ID BW0770 + password + 2FA) → paste `request_token` → "session
verified". Tokens expire every morning. Skipping a day is safe — the book self-heals by
replay at correct historical prices.

**EVENING (after 15:30 IST — you say "run it"):** the assistant verifies the clock, runs
`p11_swing_paper.py` with `RUN_MODE=LIVE`, and reports fills, exits, armed orders and NAV.
**Never before 15:45 IST** — p11 refuses partial bars, and the clock is cross-checked
against web sources first.

**Then:** open `reports/paper_dashboard.html`.

**Safety rules that must never be relaxed:**
1. Never run the decision before 15:45 IST; verify the clock first.
2. Secrets live only in `secrets.env` — presence-checked, never read, printed or committed.
3. A failed gate is a FINDING. Investigate; never loosen a threshold.
4. ASCII-only `print()` (Windows cp1252 crashes on Unicode).
5. **Real orders do not exist in this system.** Nothing ever appears in Zerodha.

---

## 10. WHERE THINGS STAND, AND WHAT IS NEXT

**Right now:** NAV Rs.59,971.40. Cash Rs.34,573.60. Four positions at the cap — ICICIBANK
8, SBIN 6, BAJFINANCE 2, INDIGO 1. SHRIRAMFIN armed but likely to skip again. Kill switch
not tripped. All gates PASS. Equity hash `bb1a6ecb40656ab7`.

**Open items, roughly by value:**
1. **Your RSI/GFS strategies** — parked awaiting your answers to 5 questions in
   `Desktop\stock market\rsi and gfs.STRATEGY_NOTES.txt`. **Only you can unblock this**,
   and it is the highest-value thread because it is *your* strategy. Must enter through
   the honest protocol as trials 10+.
2. **SBIN stop finding (#002)** — investigate while still traceable.
3. **Live book state has no backup** — a one-line `.gitignore` exception would version it.
4. **Skipped-candidate logger** — cheap instrumentation that would let the rotation
   question (#005) be answered with data in 2–3 months.
5. **Phase 5 — reconciliation.** *Deliberately blocked.* Needs ~2–3 weeks of live days;
   you currently have 2 days and 1 closed trade. **Two data points reconcile to nothing.**
6. **Phase 6** — scheduled evening run (needs an NSE holiday calendar).
   **Phase 7** — go-live readiness (SEBI algo framework). A separate, user-gated decision.

**The discipline that matters most from here:** you are two days into live paper trading
with one closed trade, which lost Rs.250. That is not evidence of anything. The strongest
temptation now is to redesign the system in response to a single bad day — which is
exactly why `DECISIONS.md` exists.
