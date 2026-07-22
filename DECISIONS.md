# DECISIONS.md - change & decision log (nse_paper_v1)

Append-only. One entry per proposal that reaches a decision. Newest entries go at the
BOTTOM. Never rewrite history here - if a decision is reversed, add a NEW entry that
supersedes the old one and link back to it.

Why this file exists: to stop the system being redesigned in response to single bad
days. A rule should change because evidence says so, not because a trade lost money.
Six months from now this file answers "why is it built this way?" without re-litigation.

**Status vocabulary:** APPLIED (in the code now) | REJECTED (decided against) |
DEFERRED (good idea, blocked on evidence or prerequisites) | SUPERSEDED (replaced by a
later entry) | OPEN (a finding under investigation, no decision yet).

---

## Entry #001 - 2026-07-21 - Dashboard & documentation upgrades (6 proposals)

**Context.** Live paper day 2. The day's only closed trade (AXISBANK, -Rs.250.25, trend
exit) prompted six improvement proposals from the user. All six are recorded here
together; three were applied, two deferred, one applied as this file.

### 001-a - Per-stock trade charts in the dashboard --- APPLIED

*Proposal.* For every stock traded, show a chart of the price with the buy and sell
points marked, so the trade can be seen rather than read from a table.

*Reasoning.* Purely presentational. Reads existing handoffs, touches no decision path.
A trade you can see is a trade you can sanity-check; tables hide shape (e.g. "did we
exit into a bounce or a collapse?").

*Evidence / prerequisite resolved.* `swing_daily.parquet` stops at 2026-07-10 and does
NOT carry live-era bars. The usable source is `swing_risk_panel.parquet`, which holds
`ref_px` (daily close) for all 20 symbols through the current decision date and is
already loaded by the dashboard. Charts are built from `ref_px`.

*Applied as.* `svg_trade_chart()` in `p11b_swing_report.py` + a "Per-stock trade charts"
section: close line over the hold window plus context either side, green BUY marker,
red SELL marker, shaded hold span, dashed trailing-stop line for open positions.

*Known limitation.* Line chart of CLOSES only - not OHLC candles. Fills happen at the
OPEN, so a marker can sit visibly off the line on a gap day. This is honest (the chart
does not have open prices in its source) but must not be mistaken for a bad fill.

### 001-b - Library section of every stock traded --- APPLIED

*Proposal.* A section listing every stock ever traded.

*Reasoning.* Cheap, presentational, and the natural home for per-symbol aggregates.

*Applied as.* `library_rows()` + a "Library" section: per symbol - trades, open/closed
status, wins/losses, gross P&L, costs paid, net P&L, avg days held.

*Hard rule attached.* LIVE-ERA TRADES ONLY, explicitly labelled with the era start.
Backtest trades (2,747 rows in `swing_trades.parquet`) are NEVER merged into this view.
Blending a 2,747-trade backtest with a 1-trade live record produces a statistic that
means nothing and flatters whichever story the reader wants. If a backtest view is ever
wanted it goes in a separate, separately-labelled section.

*Expected appearance.* Nearly empty for months. That is correct, not a bug.

### 001-c - Faster exits for fast-falling stocks --- DEFERRED

*Proposal.* "We sell on the day bar; for a stock falling fast this is too late and we
may sell at the lowest, hurting P&L."

*Finding - the premise is half wrong.* The system already has intraday downside
protection. There are TWO exits:
  - ATR trailing stop: rests INTRADAY (`p9_swing_portfolio.py` section (C)). If the
    day's low breaches the stop, the exit is booked THAT SAME DAY at the stop price; a
    gap-down fills at the open (`stop_gap`). This is the crash defence and it is not a
    day late.
  - Trend exit (MA50 break): signal at close -> sell at next open. Deliberately slower;
    it is a regime filter, not a risk stop.
AXISBANK exited via `trend`, not `stop`: -2.4% never approached its stop at 1,175
(~8% below entry). So "sell at the lowest on a fast drop" is not the live failure mode.

*The real weakness underneath.* The model assumes a stop fills EXACTLY at the stop
price. In a genuinely fast decline price gaps through it and the real fill is worse.
Paper P&L is therefore OPTIMISTIC on stop exits by an amount we have never measured.

*Decision.* DEFERRED. The correct fix is not faster exits but more conservative
stop-fill assumptions - which makes reported results WORSE, the right direction for an
honest correction. Blocked on Phase 5 (live-vs-model reconciliation) producing the
measurement first.

*Guard-rail recorded.* The +54.5bp/trade edge (deflated CI LB +11.5bp) was measured
WITH the current exit rules. Changing exits invalidates that number. Any exit change
must be pre-registered and deflated as trials 10+, like every other rule.

### 001-d - Intraday settings (minutes parameter, mid-day exits) --- DEFERRED

*Proposal.* Add an intraday/minutes parameter so a position can be sold mid-session
rather than at the next open.

*Blockers.*
  1. EVIDENCE DOES NOT EXIST (fatal for now). The entire 2017-2026 walk-forward
     validation is DAILY. yfinance intraday is ~60 days; Kite intraday history is paid.
     A 9-year edge cannot be re-validated on 60 days of 5-minute bars. This would swap
     a measured rule for an assumed one.
  2. The risk engine is daily. GARCH-EVT is fitted on daily returns over a 12-day
     horizon. Intraday exits require refitting on a different time scale - a rebuild of
     Phase 3, not a parameter.
  3. Ops jump. EOD batch (2-minute ritual) -> a live in-hours process: monitoring,
     connectivity, rate limits, partial fills. More frequent automated actions also move
     toward SEBI algo thresholds sooner if real orders are ever placed.

*Stated fairly in favour.* Tax does NOT change: selling a multi-day position at 13:00 is
still a DELIVERY sale (0.1% STT both sides). Intraday exit TIMING does not convert this
into an intraday trade. And a cheap middle path exists - a single price poll near 15:00
to act on a stop same-day, rather than full intraday bars.

*Note on what it would actually buy.* The model already books stop exits on the correct
DAY (it uses the completed bar's low). So live intraday polling would mainly close a
live-vs-model FILL-QUALITY gap, not add a new strategy. That is Phase 5's measurement.

*Decision.* DEFERRED. Measure before building the machinery.

### 001-e - This decision log --- APPLIED

*Proposal.* Document every change, the logic behind it, and whether it was applied.

*Decision.* APPLIED as this file. Kept as `.md` (operational docs stay source; only
prose deliverables ship as `.docx`).

### 001-f - Ask for assessment before implementing --- APPLIED (as practice)

*Proposal.* "Tell me your thoughts and then we will decide whether to implement."

*Decision.* Adopted as standing practice: proposals get an honest assessment - including
disagreement - BEFORE code is written. Entry 001-c above is the first example; the
premise was challenged rather than implemented.

### Scope guarantee for Entry #001

NO trading logic was modified. Untouched: `p9_swing_portfolio.py` (`step_day`, exits,
stops), `p2`/`p3` signals, `p8` risk, and all trading parameters in `config.py`.

*Self-imposed acceptance gate.* Full chain re-run after the change must reproduce
equity hash `bb1a6ecb40656ab7`. A changed hash would mean a "cosmetic" change altered
the book - a finding, and cause to revert.

**VERIFICATION (2026-07-21, after implementation).** Full chain re-run in LIVE mode:
all 6 gates PASS; G4.5 `sha256=bb1a6ecb40656ab7... identical=True`; book state unchanged
(cash Rs.34,573.60, k_next 7671, ledger 1 trade, NAV Rs.59,971.40). Gate PASSED - the
display changes provably did not alter a single decision. Files changed:
`p11b_swing_report.py` (renderer only) and this file. Independent cross-check: the new
Library section reproduces the AXISBANK round trip as gross -215.29 / costs 34.96 /
net -250.25, matching the hand-computed cost stack to the paisa.

---

## Entry #002 - 2026-07-21 - SBIN trailing stop moved DOWN across runs --- OPEN

**Observation.** SBIN's stored trailing stop fell from 990.9499725341796 (2026-07-20
state) to 990.3499725341796 (2026-07-21 state) - a decrease of exactly 0.60. The two
values share an identical fractional tail, implying the same ATR against a close that
differs by 0.60.

**Why this is a finding.** Within a run the stop is a HARD RATCHET:
`p["stop"] = max(p["stop"], close - STOP_MULT * atr)` (`p9_swing_portfolio.py`, section
(D)). A stop therefore cannot fall. A decrease ACROSS runs means the replayed history
itself changed - i.e. a prior day's bar as re-fetched today differs from the value used
that evening.

**Why the gates did not catch it.** G4.1 / G4.5 hash the pre-era BACKTEST tape, not the
live-era book. They are blind to live-era replay instability by construction.

**Suspected cause (unverified).** Two data sources - the yfinance research tape vs Kite
live bars - for the same historical day. G4.6 reports source diff 0.0bp on all 20
symbols, which does NOT obviously fit a 0.60 discrepancy, so this is not yet explained.

**Impact today.** Negligible in money terms (0.06% on one stop, no exit was triggered by
it). Recorded because a silently-mutating stop is a correctness question, not a
cosmetic one - a stop that can drift DOWN is strictly worse than one that cannot.

**Status.** OPEN. Assigned to Phase 5 (live-vs-backtest reconciliation). Do not "fix" by
clamping the stop until the CAUSE is understood - clamping would hide a data problem
rather than solve it.

### DIAGNOSED 2026-07-21 - CAUSE FOUND. Not a code bug: KITE REVISED A PAST BAR.

Decisive measurement (read-only diagnostic against live Kite):

| SBIN close for 2026-07-20 | value |
|---|---|
| risk panel `ref_px`, written Monday evening | **1060.60** |
| Kite `historical_data`, asked Tuesday | **1060.00** |
| difference | **-0.60** |

Stop reconstruction confirms the chain exactly - both stops were CORRECT for the data
each run saw, and the ratchet worked as written:

```
2026-07-20  close=1060.00  atr=19.900008  close-3.5*atr = 990.3499725341796  (Tue value)
            close=1060.60  atr=19.900008  close-3.5*atr = 990.9499725341796  (Mon value)
```

**Mechanism.** `p11` rebuilds `panel2` - the ENTIRE live era - from Kite on every run and
never persists it (line 378); `swing_daily.parquet` is frozen at the research cutoff
2026-07-10. So each run re-derives live history from whatever Kite currently returns.
Monday's 18:16 run almost certainly captured a PROVISIONAL candle (NSE's official close
is a 30-minute weighted average, settled afterwards). Compounding it, the risk panel
dedups `keep="first"` while the tape takes latest - **so the two stores now disagree with
each other**: the dashboard marks positions with one and the engine decides with the other.

### SECOND FINDING (arguably more serious) - the source-consistency check is BLIND

G4.6 reported "source diff 0.0bp on all 20 symbols", which is what made this look
impossible. Line 342:

```python
common = kh[kh["date"] == pd.Timestamp(last_panel_date)]
```

`last_panel_date` is the RESEARCH TAPE's last date (2026-07-10). The gate compares Kite
against a bar from eleven days ago that never changes, and will report 0.0bp forever. **It
cannot see the live era at all.** That is false assurance, and it is why this went
unnoticed. Fix must compare OVERLAPPING LIVE-ERA dates.

### What is actually at risk (the 0.60 itself is trivial)

- Replay is NOT reproducible - "the book self-heals by replay" assumes stable history.
- If a revision ever touches an OPEN price, a past FILL price changes retroactively, so
  realized P&L in the ledger could silently shift.
- Mark-to-market (risk panel `ref_px`) and decision data (tape close) can diverge.

### Fix options - user decision required (design fork, not a detail)

(a) **Freeze first-seen** - immutable history, reproducible replay; but may enshrine a
    provisional value.
(b) **Always take latest** - most accurate prices; history keeps mutating, reproducibility
    lost.
(c) **Freeze first-seen AND log every revision loudly** - RECOMMENDED. A decision made on
    Monday was made on Monday's data and cannot be un-made, so the book should reflect
    what it actually saw; a later revision becomes a VISIBLE finding rather than a silent
    rewrite. Plus repair the G4.6 check to compare live-era overlap.

**Honest limitation of any fix:** Monday's original OHLC is already lost. Only the close
survives (risk panel, 1060.60) plus fill prices in the ledger. A persisted live tape
necessarily starts with partial history for 2026-07-17..21.

### RESOLVED 2026-07-22 - option (c) implemented (user choice): freeze-first-seen + log.

**Point-in-time live tape.** New store `data/paper_live_tape.parquet` holds live-era bars
FROZEN at first-seen; new `data/paper_bar_revisions.parquet` logs every later Kite
restatement (never applied). `p11.reconcile_live_bars()` does the freeze; `p11.live_smoke`
now builds `panel2` from the frozen tape instead of raw Kite, so replay is reproducible.
Revisions are content-deduped so a persistent restatement is logged once.

**The seed exposed how big this is.** `seed_live_tape.py` reconstructed the frozen tape
(close = risk-panel first-seen `ref_px`; O/H/L/V from current Kite, clamped) and logged
**38 restatements** across nearly every symbol on both 2026-07-20 and 2026-07-21, up to
~38bp. So the silent rewrite was NOT an SBIN one-off - every run has been rebuilding the
whole live era from restated Kite data. All diffs < 0.4% (< the 2% "same series" gate) =
genuine settlement restatement, not bad data.

**G4.6 source check repaired.** Now compares Kite against the frozen tape on the most
recent OVERLAPPING live-era date, not the stale research boundary - so it can actually see
the live era. (Old check compared against 2026-07-10 forever and reported 0.0bp always.)

**Verification (all offline, no state touched):**
- Backtest equity hash **bb1a6ecb40656ab7 UNCHANGED** (REPLAY gates via run_gates).
- `reconcile_live_bars` unit test: keeps frozen 07-20 close (1060.60), takes first-seen
  new 07-21 (1044.00), logs exactly 1 revision, grows the tape.
- Read-only rebuild from the frozen tape reproduces the live book to the paise
  (cash 34573.5992, NAV 59971.3992) and RESTORES SBIN's stop to **990.9500** (Monday's
  first-seen value) vs the current 990.3500. Tonight's run applies this authoritatively.

**Honest limitation confirmed:** for the seed period O/H/L are today's possibly-restated
values (original intraday never persisted); only the CLOSE (which drives signals, stops,
and mark-to-market) is genuine first-seen. Every bar from 2026-07-22 onward is frozen at
TRUE first-seen. `paper_live_tape.parquet` + revisions log live in gitignored `data/`
(same status as the book state, DECISIONS #006 open question).

---

## Entry #007 - 2026-07-22 - G4.3 resume gate CLOBBERS the live book state --- FIXED

**Found while testing the #002 fix** (running `run_gates` standalone corrupted
`paper_state.json`). NOT caused by #002 - a latent bug the test surfaced.

**The bug.** G4.3's kill/reload/resume test did
`save_state(part1["state"], half, mkt.syms)` then `load_state(mkt.syms)` - both default to
the PRODUCTION paths. The gate suite runs on EVERY invocation, so any run that executes
the gates without the live leg overwrites the real live book with a mid-history backtest
book (observed: `paper_state.json` stamped with k=3832, a 2010 date, equity Rs.75,811).

**Why it stayed hidden.** A normal LIVE run's `live_smoke` rewrites the correct state at
the end (line ~522), so the clobber was invisible - until this session ran the gates ALONE
to verify the hash, leaving the corrupted state behind.

**Severity.** High latent: anyone running `python p11_swing_paper.py` in REPLAY (e.g. to
check the hash) silently destroys the live book. The ledger was spared (the resume test
never wrote it).

**Fix.** Route the resume round-trip through a TEMP path
(`data/_gate_resume_tmp.json`, unlinked after), never the production state. Verified: after
the fix, running the full gate suite leaves `paper_state.json` INTACT (k_next 7671, the
4 correct positions, NAV 59971.40).

**Recovery.** The clobbered state was restored to the exact last-known-good Tuesday book
(read from the file earlier in the same session, before the clobber). Since tonight's run
recomputes the book authoritatively from the frozen tape, the state file is only a
snapshot/fallback in the interim.

**Lesson (recorded so it is not repeated):** test/gate code must NEVER write production
data paths. Audit the rest of the suite for other default-path writes.

---

## Entry #003 - 2026-07-21 - "Costs paid" dashboard section --- APPLIED
## ... and a cost-TIMING finding uncovered while building it --- OPEN

*Proposal (user).* A dashboard section showing every cost paid.

*Applied as.* `cost_breakdown()` + `cost_rows()` in `p11b_swing_report.py` and a "Costs
paid (live era)" section: four KPI tiles (charged to NAV, per closed trade, incurred but
not yet charged, total friction as % of NAV) over a per-component table - STT, DP,
stamp, exchange, SEBI, GST, plus modeled slippage reconstructed out of the fill prices.

*Live-era only*, same rule as the Library (#001-b).

### The finding that changed the design --- OPEN

While reading `_do_exit` to build this, I found the engine charges the **entire
round-trip cost at EXIT**:

```
cost = delivery_roundtrip_cost(buy_val, sell_val)   # p9_swing_portfolio.py:171
st.cash += sell_val - cost
```

Entry deducts only `shares * entry_px` - **no costs are charged at entry**. So an open
position has paid nothing yet, even though its buy-side STT (0.1%) and stamp duty
(0.015%) are already economically incurred.

*Consequence.* Marked-to-market NAV is **overstated** while positions are open, by the
unbooked buy-side costs. Measured today: **~Rs.29.88 on Rs.25,176 of open notional**
(0.05% of NAV). Each open position additionally still owes a full sell leg (0.1% STT +
the flat Rs.15.34 DP charge) whenever it exits.

*Is it wrong?* Total lifetime cost is identical either way - only the TIMING differs. It
is not a P&L error, it is an accrual error, and it always flatters the book mid-flight.
The distortion scales with the number of open positions, so it will not stay at Rs.30.

*Decision.* The dashboard now shows BOTH columns ("charged to NAV" vs "incurred, not yet
charged") rather than hiding the gap. The engine itself is NOT changed: altering cost
timing touches `_do_exit`, which is trading logic, and would break the equity-hash
guarantee for a cosmetic gain. Whether to accrue buy-side costs at entry is a **Phase 5**
question, decided with reconciliation evidence, not now.

*Cross-check.* Section reproduces the AXISBANK stack exactly: STT 17.64 + DP 15.34 +
stamp 1.34 + exchange 0.52 + GST 0.10 + SEBI 0.02 = 34.96, slippage 5.29 - matching the
hand computation to the paisa, from an independent code path.

---

## Entry #004 - 2026-07-21 - `SESSION_LOG.md` (per-session narrative) --- APPLIED

*Proposal (user).* "Document every chat in another new document."

*Applied as.* `SESSION_LOG.md` - chronological plain-English record per session:
questions asked, answers given, what was run, what was decided, what broke.

*Boundary with this file (stated so both do not rot into duplicates).*
`DECISIONS.md` = durable decisions, terse, "why is it built this way?".
`SESSION_LOG.md` = the story of each day, "what happened that week?".

*HONEST LIMITATION, recorded rather than glossed.* Chat transcripts are **not** captured
automatically - no hook writes that file. Entries are composed by hand at the end of a
session from memory of it. Therefore: an abruptly-ended session leaves NO entry (a gap
means "not written", never "nothing happened"); entries are summaries, not transcripts;
and the file only stays current if writing it is part of the daily ritual. A verbatim
auto-captured log would need a Claude Code hook - not built, and out of scope today.

*Decision.* Accepted WITH that limitation, as a lab notebook rather than an audit log.
`DAILY_KICKOFF.md` should gain a closing step so the write-up is prompted; until that is
added, this file depends on it being remembered.

*Follow-up (same day).* `DAILY_KICKOFF.md` updated: closing SESSION_LOG step added to the
ritual, plus a "HOW CLAUDE SHOULD WORK" section (effort suggestions + the handshake,
assess-before-implementing, the equity-hash rule) and `DECISIONS.md` added to the
read-before-acting line.

---

## Entry #005 - 2026-07-21 - Stock swap / rotation when the book is full --- DEFERRED

*Proposal (user).* "If we are holding a weak stock, can we exchange it for the next one
we are going to buy?" i.e. when the book is at 4/4 and a new signal fires, drop the
weakest holding and take the new candidate.

*The inefficiency is REAL - stated plainly because it is a fair point.* With the cap
binding, a new signal is simply DISCARDED (SHRIRAMFIN twice now). So the book holds the
FIRST four candidates that happened to appear, not the BEST four. That selection is
path-dependent and arbitrary. Choosing deliberately would, in principle, be better.

*Why it is deferred anyway - three reasons.*

1. **It changes the strategy family.** `ts_mom` is TIME-SERIES momentum: each stock is
   judged only against its own history (close>MA200, 126d ret>0, close>MA50). There is no
   cross-stock ranking anywhere in the system. Rotation makes it CROSS-SECTIONAL - a
   different family. The measured +54.5bp/trade (deflated CI LB +11.5bp) was earned under
   the current rules and DOES NOT TRANSFER. A rotation rule is a new rule and needs its
   own pre-registration, IS/OOS, and deflation as trials 10+.

2. **The cost arithmetic is hostile at Rs.60k.** Rotation raises turnover by
   construction. One round trip costs ~Rs.38 (measured on AXISBANK: 34.96 costs + 5.29
   slippage). Two extra swaps a month = ~Rs.912/yr against an OOS CAGR of 1.06% =
   Rs.636/yr on this book. **The rotation cost would exceed the entire modeled return.**
   Direct precedent: the 8 x Rs.900 variant lost ~3x to the 4 x Rs.1,800 variant purely
   on round-trip count and flat-cost drag. Rotation pushes back toward the losing end.

3. **Whipsaw.** Trend systems earn by letting winners run; forced rotation can cut
   positions that have not broken their trend, repeatedly.

*TRAP recorded for whoever builds this.* Do NOT rank "weak" by unrealized P&L. The market
does not know your entry price; ranking on it is anchoring - a behavioural bias wearing a
rule's clothing. Any rank must be a MARKET metric (e.g. 126d momentum).

*Cheap next step instead (recommended, not yet built).* **Log every skipped candidate** -
symbol, date, signal, and the incumbent it lost to. In 2-3 months that answers the
question with data: did the skipped candidates actually outperform the positions we kept?
If yes, rotation has a measurable prize and gets tested properly; if no, we avoided
adding turnover to a cost-bound book. Same instrument-first pattern that caught the `tom`
bug and #003.

*Decision.* DEFERRED pending that measurement. Note the idea gets BETTER as the book
grows - flat-cost drag shrinks with position size, so at Rs.5-10 lakh the arithmetic
could flip. Worth revisiting at a larger book size even if it fails now.

---

## Entry #006 - 2026-07-21 - Version control (`git init`) --- APPLIED

*Proposal.* The project had NO version control at all.

*Reasoning.* On a codebase with real correctness stakes this was the cheapest available
safety win. Concretely: in this one session four edits were made to
`p11b_swing_report.py` with no diff and no revert path - only the equity-hash gate stood
between a silent regression and the book, and that gate covers the book, not the
renderer. Separately, the config has been through three re-baselines (Rs.10L -> 60k
8x900 -> 60k 4x1800) whose reasoning survives in prose but whose CODE STATE is gone.

*Repo scope decision.* Initialised at **`nse_paper_v1/`**, NOT at the `Hedge funds` root.
Reason: the sibling `integrated_mm_v2/` is **2.6 GB** with no `.gitignore` of its own, so
a root repo would risk committing gigabytes of tick data on one wrong ignore rule.
CONSEQUENCE, stated so it is not forgotten: `DAILY_KICKOFF.md`, `EXPLAIN_MODE.md` and
`PROJECT_SUMMARY.*` live in the root and remain UNVERSIONED. Worth solving later (a
second small repo, or a root repo with strict ignores).

*Ignore rules.* Pre-existing `.gitignore` already covered secrets and `data/`; added
`*.log` (stray `p11_*.log` scratch) and `reports/` (re-rendered from state every run).

*Secret-safety procedure followed.* Staged everything, then grepped the staged list for
secret-like names BEFORE committing. The check fired on `secrets.env.example` - inspected
it rather than assuming: placeholders only (`your_api_key_here`), safe to commit. Real
`secrets.env` confirmed ignored via `git check-ignore -v` (`.gitignore:3`). **Always
verify rather than trust the filename.**

*Result.* Commit `9122d6a`, 48 files tracked, `.git` 1.1 MB, clean tree. Nothing is
pushed anywhere - this is a purely LOCAL repository, no remote configured.

*Unlocks.* `/code-review ultra` now becomes possible (it requires a git repo). It is
user-triggered and separately billed; Claude cannot launch it. Recommended target when
the user wants it: `p9_swing_portfolio.py` and `p11_swing_paper.py`, where every
correctness bug so far has lived.

*Open question, deliberately NOT decided unilaterally.* The live book state
(`data/paper_state.json`, `paper_ledger.parquet`, `paper_equity_curve.parquet`, ~12 KB
total) sits inside the ignored `data/` folder and therefore has NO backup or history. A
`!` exception in `.gitignore` would give a daily audit trail of the actual trading
record. Offered to the user; awaiting their call.
