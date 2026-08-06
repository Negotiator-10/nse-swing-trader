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

---

## Entry #008 - 2026-07-23 - Skipped-candidate logger (the #005 prerequisite) --- APPLIED

**Context.** Live day 4. User asked to "make a few advancements" and selected all four
proposed directions. This is the FIRST built - deliberately, because it is both a cheap
integrity win AND the hard prerequisite the DEFERRED rotation idea (#005) is blocked on.
#005's recommended cheap next step was verbatim: "LOG every skipped candidate now, and
decide with real data in 2-3 months." This builds exactly that.

**The gap.** At `p9_swing_portfolio.py` section (B), when the caps discard an armed entry
the engine did `st.n_skips += 1; st.skip_reasons[reason] += 1` and threw away WHICH
symbol was skipped and WHICH incumbents it lost to. So the path-dependence #005 worries
about ("we hold the first four candidates, not the best four") was invisible after the
fact. SHRIRAMFIN had been discarded 3 times with no record of it.

**Applied as.**
  - `BookState.skip_log` (new list field, `p9`) - additive, never read by the decision path.
  - At the skip point, append `{date, symbol, sleeve, reason, budget, vpu, atr_sig,
    incumbents}` where `incumbents` is the `;`-joined held symbols it lost to.
  - `config.PAPER_SKIPS_PATH = data/paper_skips.parquet`.
  - `p11.live_smoke` freezes the LIVE-ERA slice to that parquet after the ledger write -
    rebuilt in full from the frozen tape each run, exactly like the ledger. Result JSON
    gains `skips_logged`.

**Why it cannot move the book (the acceptance gate).** The equity hash is taken over the
equity array only; a log that touches no cash/pos/trade cannot change it. Verified: full
LIVE re-run, all 6 gates PASS, G4.5 `sha256=bb1a6ecb40656ab7 identical=True` UNCHANGED.
This is the same guardrail that licensed the Entry #001 display changes to touch nothing.

**First capture (6 live-era rows).** INDIGO skipped 07-20 (risk_budget, before it later
filled), ETERNAL skipped 07-23, and **SHRIRAMFIN skipped all four days** (07-20..07-23,
max_positions). Every row carries the incumbents. In 2-3 months a forward-return join
(skipped symbol vs the incumbents it lost to, off the frozen tape) answers #005 with data.

**Deliberately NOT done (kept for a later, separately-gated step):** ranking skips by a
market momentum metric at capture time (the #005 TRAP: never rank by unrealized P&L). The
forward-return study can compute momentum from the tape when run, so storing it now is
unnecessary and would bloat the row. Surfacing the log in the dashboard is a cheap p11b
follow-up (renderer only, no hash risk), not built this session.

**Status.** APPLIED. #005 stays DEFERRED but is now ACCRUING the evidence it needs.

---

## Entry #009 - 2026-07-24 - Shadow / what-if tracker: rotation Piece 1 --- APPLIED

**Context.** User's own advancement, stated in two pieces: (1) a "waiting stock" column
that BUYS the skipped candidate hypothetically to see what it would have done; (2) the real
stock-exchange (rotation) feature that swaps a weak hold for the waiting candidate. Piece 1
is the MEASUREMENT that must exist before Piece 2 is built, so rotation is decided by data.
User confirmed my read-back, then: "start with piece one", then "yes do both" (promote to a
module + put it on the dashboard).

**Applied as `p11c_swing_shadow.py`.** A SHADOW book runs the IDENTICAL engine
(`p11.online_replay` -> `p9.step_day`) with caps LIFTED and huge notional cash, so NOTHING
is ever skipped: every armed signal fills at standard VaR size -- same next-open entry, same
ATR stop, same trend/max-hold exit; kill switch OFF (each what-if runs its own course). The
shadow entries the REAL book never took = the waiting/skipped stocks. Writes a labelled
`data/paper_shadow.parquet` (kind = waiting|hold), rebuilt in full each run like the ledger.
`config.PAPER_SHADOW_PATH` added.

**Wired + surfaced.** `p11.live_smoke` calls `run_shadow(mkt2, budget_fn, era_k, st)` right
after it persists the real book -- so it runs on every LIVE decision, no manual step (result
JSON gains `shadow`). `p11b_swing_report.py` gained a "Waiting stocks (what-if)" dashboard
section (skipped stocks + hypothetical return vs the real holds + the preliminary edge).

**Read-only + trustworthy (the guards).** Writes ONLY paper_shadow.parquet, never a
production path (the #007 rule). Standalone `main()` SELF-CHECKS that the reconstructed real
book reproduces the persisted ledger + state to the paise before trusting the shadow (same
principle as the #008 hash gate). Acceptance gate: full LIVE run, all 6 gates PASS, equity
hash bb1a6ecb40656ab7 UNCHANGED -- the feature provably touches no decision.

**First readings (the point: DO NOT trust the number yet).** 07-23: waiting -1.22% vs holds
-2.36%, edge +1.22... +1.14%. 07-24 (next day): waiting -3.33% vs holds -3.00%, edge -0.33%.
The edge FLIPPED sign in ONE day -- proof that a handful of down days is pure noise. The
value delivered is the verified measurement mechanism; the honest answer needs 2-3 months.

**Deliberately deferred:** (a) partial cases -- a stock skipped then bought a day later
(INDIGO, skipped 07-20 / bought 07-21) is currently classed as a real hold, not tracked as a
"what if we had bought it a day earlier"; (b) cost/risk adjustment of the comparison (today
it is gross price return); (c) a per-day edge time-series for the eventual verdict. All fine
for Piece 1's job, which is to START accruing the record. Piece 2 (the actual swap) is the
next, separately-gated advancement and must be pre-registered (it converts time-series into
cross-sectional momentum -- the +54.5bp edge does NOT transfer; see #005).

**Status.** APPLIED. Piece 1 done. Piece 2 (rotation) still DEFERRED, now measurable.

---

## Entry #010 - 2026-07-24 - Re-baseline to TRACK A 24% risk budget --- APPLIED

**Context.** User asked "why so much idle cash"; explained sizing is by tail risk not
capital (Rs.4,800 loss budget capped notional ~Rs.23k regardless of cash). Then ran the
risk-dial sweep (advancement b): scaling the PORTFOLIO risk budget with per-position held at
the 3:8 ratio, max_positions FIXED at 4. Self-check reproduced production (8% row = 1.06%
CAGR / 0.23 Sharpe). User chose **Track A, 24%**.

**Why (the sweep's finding).** At 8% the budget BINDS hard (~94% used), throttling positions
below their VaR size. Raising it un-throttles the risk engine -- OOS CAGR 1.06%->3.52%,
Sharpe 0.23->0.54, maxDD ~flat (-10.7%->-10.9%), exposure 22%->38%. Sharpe UP = the extra
return is risk-adjusted, not naive leverage. Track B (more NAMES) was worse across the board
(dilutes into weaker signals + turnover), so max_positions stayed 4. Past 16% is diminishing
returns; user took the full 24% end of the dial.

**Applied as** `config.py`: PER_POSITION_RISK_BUDGET_INR 1_800 -> **5_400** (9%),
PORTFOLIO_RISK_BUDGET_INR 4_800 -> **14_400** (24%). max 4, notional-frac 20%, no leverage
UNCHANGED. This is the 4th baseline (10L -> 60k 8x900 -> 60k 4x1800 -> **60k 24%/9%**).

**This is NOT a display change -- it re-bases the book, correctly.** The engine rebuilds the
live era from config each run, so the whole 5-day live history re-sized at 24%. Equity hash
therefore CHANGED bb1a6ecb40656ab7 -> **0e3e8313789f284a** (the new baseline; the hash guard
is for changes that should NOT move the book -- this one should). Verified: all 6 gates PASS
at the new budget, G4.5 rerun byte-identical to the new hash.

**Effect (07-24 book, before -> after):** cash Rs.34,574 -> **Rs.17,661** (58% -> 30% of
NAV); invested Rs.24,600 -> **Rs.40,761** (42% -> **70% exposure**); positions resized
ICICIBANK 8/SBIN 6/BAJFIN 2/INDIGO 1 -> 8/**11**/**8**/**2** (ICICIBANK now bound by the 20%
notional cap, not risk); AXISBANK realized trade 7 sh -Rs.250.25 -> **9 sh -Rs.317.37**;
backtest trades 1732 -> 1711. NAV Rs.59,167 -> **Rs.58,422** -- LOWER, and honestly so: more
capital deployed into a DOWN week = bigger losses (live era -1.39% -> -2.63% over 5 days).
The long-run backtest CAGR gain (3.5% vs 1.06%) comes with bigger swings BOTH ways; this week
showed the downside half first.

**Binding constraint SHIFTED.** Risk budget now 68% used (was 94%), so MAX_POSITIONS=4 is now
the binding cap. SHRIRAMFIN armed Monday with a Rs.4,488 budget but will STILL skip -- book
full at 4 names, not a risk-budget skip. (Consistent with Track B: 4 names is the right cap.)

**Honest caveat carried:** the dial sizes closer to an edge already MEASURED on 2017-26 OOS;
it does not create new edge, and a bigger budget = bigger absolute tail loss in a real crash.
Reversible: restore the two config values + re-run to recover the 8% book exactly.

**Status.** APPLIED. New baseline hash **0e3e8313789f284a**. Supersedes the 4x1800 baseline.

---

## Entry #011 - 2026-07-29 - Max positions 4 -> 6 at the 24% budget --- APPLIED

**Context.** User, after the 24% re-baseline put the extra budget into the SAME 4 names:
"I just want to add more stock... now there are 4 but i want to invest in 6." Assessment had
been given twice (Track B: more names hurt) and the user decided anyway - risk-owner's call.

**The correction that must be recorded honestly.** Before applying, the originally-UNTESTED
exact combo (24% budget / 6 names) was run - and it is the BEST row in the whole sweep:
**OOS CAGR 4.05% / Sharpe 0.56 / maxDD -10.8%** vs 3.43%/0.51/-10.9% at 24%/4 (and 24%/5 is
worse than both on Sharpe/DD: 0.48/-12.3% - 6 is a sweet spot, not a monotone trend). So the
user's instinct was RIGHT at this budget and my Track-B generalization ("more names hurt")
was WRONG here. **Mechanism, so the lesson survives:** at 8-16% budgets the RISK BUDGET is
the scarce resource - extra names dilute it (Track B was honest at those levels). At 24% the
budget was only ~68% used because the NAME-COUNT + 20% notional caps bound instead - slots
5-6 give the surplus budget somewhere productive to go. Which constraint binds depends on the
budget level; never extrapolate a cap conclusion across budget regimes without testing the
exact cell. (Directly relevant to the user's queue complaint: with 6 slots the armed
ETERNAL + SHRIRAMFIN will actually FILL.)

**Applied as** `config.py` MAX_CONCURRENT_POSITIONS 4 -> **6** (budget levels unchanged:
9%/pos, 24% portfolio). 5th baseline. Full gate suite re-run in REPLAY (pre-close, 14:0x
IST; live leg correctly guarded): all PASS, new equity hash **118f70b0ea7f5e4f** (2,286
backtest trades, was 1,711). Live book (07-28 state) preserved by the #007 bootstrap guard;
it RE-BASES at tonight's post-15:45 LIVE run, where the armed ETERNAL + SHRIRAMFIN should
fill at Wednesday's open in the era replay -> book expected at 6/6.

**Carried caveats:** same 2017-26 OOS window as every dial number (sizing closer to a
measured edge, not new edge); turnover rises (2,286 vs 1,711 trades - flat-cost drag is
already inside the net numbers); 6 x 20% notional = 120% > 100% gross, so the gross/cash
caps will now bind at times (by design, shrink-only).

**Status.** APPLIED. New baseline hash **118f70b0ea7f5e4f**. Supersedes #010's hash.

---

## Entry #012 - 2026-07-29 - Universe expansion: NIFTY 50 top-20 -> NIFTY 100 all-passing (95) --- APPLIED

**Context.** User: "expand the screening stocks... early we were screening only 20 stocks."
Motivation is sound: after #010/#011 the binding constraint was SIGNAL SUPPLY (the dial
sweep's honest ceiling - ~40% invested even at 24%/6 because signals don't fire often
enough on 20 names), and R4 already showed ts_mom generalizes out-of-universe (+79.6bp on
12 Next-50 names). User chose (AskUserQuestion): NIFTY 100, ALL that pass the screen.

**Applied as.**
  - `p0_universe.py`: candidates now fetched from the OFFICIAL niftyindices.com
    ind_nifty100list.csv (same source/pattern as gfs_backtest; cached to
    data/nifty100_symbols.json for offline re-runs); selection = ALL passing the unchanged
    ex-ante screen (liquidity >= Rs.20cr median daily turnover, history >= 750d, data
    quality; NEVER returns). Result: **95 of 100 pass** (dropped: ENRIN, HYUNDAI, JIOFIN,
    TATACAP, TMCV - short history).
  - `config.SWING_RESEARCH_CUTOFF = "2026-07-10"` + p1 truncation: any research-tape
    rebuild ends at the original cutoff, so the frozen first-seen live tape (#002) stays
    the ONLY source for live-era bars. Without this, the yfinance re-pull would have
    silently replaced frozen live-era history.
  - Data backed up to `data/backup_pre_012/` BEFORE overwriting (tape, risk panel,
    watchlist, book state, ledger, live tape) - restores the 20-stock world in seconds.
  - Live-era risk snapshots (240 rows, the decision lineage) preserved VERBATIM by a
    post-p8 merge from the backup - recomputation is near- but not guaranteed-identical.

**Verified (all gates, full chain).** P1: 536,145 rows x 95 symbols, 4 gates PASS.
P3 risk: 441,050 snapshots, causality 40 probes 0 mismatches, all gates PASS (median VaR
21.2%, consistent with the 20-stock 21.1%). P3 portfolio + P4 REPLAY suite: ALL PASS,
online==batch 2,756/2,756 trades byte-identical. **New baseline hash `45a27ba622b6e021`**
(6th baseline; supersedes #011's 118f70b0ea7f5e4f).

**OOS scoreboard (rules frozen, wider universe - accepted as-is):** combo 2.2% CAGR /
0.32 Sharpe / -26.9% maxDD; ts_mom-ALONE 4.0% / 0.59 / -21.0%; buy&hold 25.5% / 0.96 /
-49.8%. CAVEAT: the OOS window itself widened (~2015-2026, includes the COVID crash) as
per-symbol cutoffs moved with longer histories - NOT apples-to-apples vs the 20-stock
-10.8%.

**OPEN SUB-FINDING (user decision pending): G3.6 FLIPPED - `tom` no longer earns its
seat.** On 95 names ts_mom-alone dominates the 2-sleeve risk-parity combo on BOTH axes
(Sharpe 0.59 vs 0.32, maxDD -21.0% vs -26.9%); tom's marginal edge dilutes away and its
~55% risk-parity weight drags. Options offered: (1) drop tom -> ts_mom-alone (the gate's
own verdict; recommended), (2) keep 2-sleeve pending live evidence. G3.6 exists precisely
to make this call at each re-baseline; it has flipped before (60k 8x900) and flipped back
(Option B). Awaiting the user.

**Carried caveats.** Survivorship: current NIFTY-100 membership (deeper than the 50);
same mitigation (ex-ante screen), same limitation (no point-in-time membership data).
Slippage realism thinner on the smallest names (min Rs.20cr turnover floor retained;
BAJAJHLDNG-class ~Rs.54cr/day is the thinnest). NESTLEIND 29% zero-volume bars (yfinance
quirk) - handled by the tradable flag. First LIVE run must backfill live-era Kite bars +
compute catch-up risk snapshots for ~75 new symbols (~800 GARCH fits) - EXPECT IT SLOW
(likely 10-30 min); also the live-era replay reruns with 95 candidates, so the live book
re-bases retroactively (same paper-accounting artifact as #011, will be labelled).

**Status.** APPLIED (pending first LIVE exercise tonight). Baseline `45a27ba622b6e021`.

---

## Entry #013 - 2026-08-04 - Drop the `tom` sleeve (single-sleeve ts_mom book) --- APPLIED

**Context.** Closes the OPEN sub-finding of #012. User asked what `tom` is, got the
explanation (calendar rule: buy on the last trading day of the month if close > MA200;
month-end flows), then: "drop it."

**Evidence (the gate's own verdict, not a new idea).** On the 95-name universe the
pre-registered G3.6 comparison flipped: ts_mom-alone dominates the 2-sleeve risk-parity
combo on BOTH axes -- OOS **Sharpe 0.59 vs 0.32**, **maxDD -21.0% vs -26.9%**, CAGR 4.0% vs
2.2%. `tom` was always flagged MARGINAL (Phase-2 deflated CI LB +2.8bp, and turn-of-month
effects are well-known/decaying); its edge dilutes across 95 names while risk-parity was
handing it **~55% of the risk budget**. G3.6 exists precisely to make this call at each
re-baseline; it has flipped before (60k 8x900) and flipped back (Option B).

**Applied as** `config.SLEEVES = ["ts_mom"]` (was `["ts_mom","tom"]`). Single sleeve =>
weights trivially 1.0, so `budget_fn` returns the flat per-position budget. NOTE the
side-effect: per-position budget RISES from ~Rs.4,488 (base x 2 x 0.45) to the full
Rs.5,400 base, so positions size LARGER -- the live book will re-base accordingly.

**Two code fixes the change exposed (both required, neither cosmetic).**
  1. `p9.main()` hardcoded `standalone["tom"]` when writing swing_equity.parquet -> would
     KeyError the moment SLEEVES shrank. Now writes one equity column per ACTIVE sleeve.
  2. G3.6's verdict text would have printed "risk-parity improves BOTH Sharpe and drawdown"
     when comparing the single-sleeve book **against itself** (combo == standalone, so
     `>=` is trivially true). Now detects len(SLEEVES)==1 and says the comparison is
     DEGENERATE, keeping the gate as a live check that no second sleeve is silently active.
     Recorded because a self-congratulatory vacuous PASS is exactly the kind of false
     assurance the blind G4.6 check (#002) taught us to hunt.

**Verified (REPLAY, pre-close 13:20 IST Tue - live leg correctly guarded).** p9: all
portfolio gates PASS, sleeve weight ts_mom=1.00, 2,618 trades (was 2,756). p11: all gates
PASS, online==batch 2,618/2,618 byte-identical. **New baseline hash `5a1795c42f9c605b`**
(7th baseline; supersedes #012's 45a27ba622b6e021). Live book PRESERVED untouched by the
#007 bootstrap guard (still Monday's, k_next 8984, ledger 4) - it re-bases at tonight's
post-15:45 LIVE run. Backup at `data/backup_pre_013/`.

**Reversible:** restore `SLEEVES = ["ts_mom","tom"]` and re-run the chain.

**Honest caveat.** This does NOT prove tom is worthless -- it proves tom does not earn a
~55% risk allocation on THIS universe under THIS budget. The measured 2017-26 tom edge was
real but marginal; a different universe/weighting could revive it. The retirement is a
resource-allocation decision, recorded as such.

**Status.** APPLIED. Baseline `5a1795c42f9c605b`. #012's open sub-finding is now CLOSED.

---

## Entry #014 - 2026-08-06 - Manual sell override (discretionary exit) --- APPLIED

**Context.** User: "in the dashboard I want to add one more feature where there should be
option of me manually selling any stock which I feel". Assessed before building, per #001-f.

**Two constraints stated up front.**
1. *Architectural.* The dashboard is a STATIC file with no server -- a button cannot sell
   anything, because nothing is listening. Any manual order must be picked up by p11 at the
   next evening run, filling at the next session's open like every other exit.
2. *Measurement.* The paper trial exists to answer "does this STRATEGY work?". A
   discretionary sell changes the question to "does the strategy PLUS my judgment work?",
   and with one human and a handful of trades the two cannot be separated after the fact.
   Flagged in context: the user had, within the previous hour, proposed two exit ideas
   (weekly RSI<60, a 0.52-ATR candle stop) that measurement showed would have LOST money --
   a manual button is the same impulse with no test in front of it. Also noted the user's
   own earlier observation that emotionless rule-following is the system's strength.
   **Legitimate uses exist anyway** (fraud/governance events, corporate actions the model
   cannot see, halting something while learning) and it is a paper book. Built as asked.

**Design that preserves the measurement (the reason this is acceptable).**
  - Manual exits enter the ledger tagged **`reason="manual"`**, permanently distinguishable
    from `trend` / `stop` / `stop_gap` / `maxhold` / `kill`.
  - The dashboard reports **three realized P&L figures**: strategy-only, manual-only, and
    actual. The strategy's own track record therefore survives intact, and the split
    doubles as real data about the user's discretion.
  - Confirmation dialog before anything is queued; `--undo` cancels a pending order.

**Applied as.**
  - `manual_sell.py` (new CLI): `python manual_sell.py SYMBOL` queues, `--undo` cancels,
    `--list` shows. Refuses symbols not currently held and refuses duplicates.
  - `config.PAPER_MANUAL_ORDERS_PATH` = `data/manual_orders.json`.
  - `p11.resolve_manual_exits()` maps each order's issue date to the day index of the first
    trading day AFTER it; `online_replay(..., manual_exits={k: [syms]})` injects
    `pend_exit[j] = "manual"` just before `step_day`, so section (A) fills it at that day's
    open exactly like a rule exit.
  - `p11b`: a **Sell** button per open position (copies the command to the clipboard, since
    the page is static), a manual-orders table, and the three-way P&L split.

**Why it is a persistent LOG, not a one-shot queue (the subtle part).** p11 rebuilds the
whole live era by replay on every run. An order that merely "executed once" would silently
vanish from the book on the next run and the ledger would disagree with itself. Each order
is therefore stored with its issue date and re-applied on the SAME resolved fill day in
every future replay. Status is derived from the LEDGER each run, never assumed -- so if a
rule exit beats a manual order to the punch, the order simply never fires and stays pending
rather than corrupting anything.

**Acceptance gate.** `manual_exits` defaults to `None`, so every existing path is
bit-identical. Verified: full gate suite PASS, G4.5 `sha256=5a1795c42f9c605b` **UNCHANGED**,
live book preserved. CLI guard rails tested: refuses a non-held symbol (RELIANCE), queues a
held one (ABB), no-ops on duplicate, `--undo` removes it cleanly.

**Not built, deliberately:** same-day selling (would break the next-open discipline that
every other exit follows, and the system has no intraday data), and manual BUYS (sizing
would have to be invented outside the VaR path; no request, and a far bigger hole in the
measurement).

**Status.** APPLIED. Baseline hash unchanged -- this is plumbing plus display, and it only
alters the book when the user deliberately issues an order.
