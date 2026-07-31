# SESSION_LOG.md - narrative record of every working session (nse_paper_v1)

What this is: a chronological, plain-English record of what was discussed, asked,
decided and run in each chat session. Newest entries at the BOTTOM.

**How this differs from `DECISIONS.md`** (keep them distinct or both rot into
duplicates):
- `DECISIONS.md` = the WHAT and WHY of durable decisions. Terse, append-only, the place
  you look to answer "why is the system built this way?"
- `SESSION_LOG.md` = the STORY of each day. Questions asked, answers given, what was run,
  what was learned, what broke. The place you look to answer "what happened that week?"

**HONEST LIMITATION - read this before relying on the file.** Chat transcripts are NOT
captured automatically. There is no hook writing to this file; entries are composed by
hand at the end of a session, from memory of that session. Consequences:
1. If a session ends abruptly (crash, closed window, context cleared), the entry is
   simply MISSING. A gap here means "not written", never "nothing happened".
2. Entries are a SUMMARY, not a transcript. Quotes are approximate unless marked exact.
3. This only stays current if writing it is part of the ritual. It is now listed in
   DAILY_KICKOFF.md; if that step is skipped, this file silently goes stale.
Treat it as a lab notebook, not an audit log.

---

## Session 2026-07-21 (Tue) - live day 2, first realized trade, dashboard + docs

**Context at start.** Live paper day 2. Book from Monday: 4 positions (AXISBANK,
ICICIBANK, SBIN, BAJFINANCE), NAV Rs.60,114.55, with AXISBANK armed to trend-exit and
INDIGO + SHRIRAMFIN armed to buy at Tuesday's open.

**Morning.** Clock verified against multiple web sources before anything time-sensitive.
NOTE for future sessions: `timeapi.io` disagreed with the system clock by ~9 minutes;
Google and Cloudflare HTTP `Date` headers both AGREED with the system clock, so
`timeapi.io` was the faulty source and the PC clock was fine. Lesson: one disagreeing
source is not proof of drift - break the tie with a third before acting. User confirmed
the `kite_login.py` ritual was done (session verified, user BW0770).

**Mid-session - a genuine misunderstanding, worth recording.** User asked why we wait
until the close to act when we had agreed AXISBANK would be sold at the morning open,
and told me to re-read the code. Resolution: the user was RIGHT about execution - the
fill price IS today's open (`cx.o[j]`, `p9_swing_portfolio.py` sections A/B). My earlier
phrasing had blurred two different things. The clarification that settled it:
- fill PRICE = today's open, locked at 09:15, unchangeable
- fill RECORDING = tonight's batch, because the engine needs the COMPLETE daily bar to
  fill at the open, check intraday stops, and generate tomorrow's signals off the close
Waiting until after the close does NOT produce a worse fill price. Also confirmed: the
dashboard is a once-a-day snapshot, so during the day it correctly showed Monday's book
with three orders marked "armed".

**Evening run (after the 15:45 guard, clock re-verified).** All 6 gates PASS in LIVE.
- AXISBANK SOLD at the open 1,244.6265 (raw open 1,245.00), `trend` exit, held 1 day:
  price -215.29, costs -34.96, **net -Rs.250.25** - the first realized trade of the live
  era.
- INDIGO BOUGHT 1 @ 5,250.57.
- SHRIRAMFIN cap-skipped a SECOND time. **I predicted wrongly** that the AXISBANK exit
  would free room for both armed buys; it freed exactly ONE slot (4 -> 3 -> INDIGO in ->
  4 again). Recorded because the wrong prediction is more instructive than the outcome.
- NAV Rs.59,971.40 (day -143.15; -28.60 vs the Rs.60,000 era start). 20/20 fresh Kite
  bars, source diff 0.0 bp.

**AXISBANK post-mortem (user asked for the full trade).** Reconstructed both raw opens
from the fill prices and the 3bp slippage: bought at raw 1,275.00, sold at raw 1,245.00
(-2.35%). Cost stack reconciled to the paisa: STT 17.64 + DP 15.34 + stamp 1.34 +
exchange 0.52 + GST 0.10 + SEBI 0.02 = 34.96, plus ~5.29 of modeled slippage inside the
fill prices. Verdict: a correctly-executed losing trade - the rule cut a broken trend in
one day without discretion.

**User asked for the exact SELL TIME.** Honest answer: none exists. This is a daily-bar
system; the ledger stores a DATE (`2026-07-21 00:00:00` - a null time, not a midnight
sale). The closest thing to an execution time is the opening bell, 09:15. The run that
recorded it happened at 15:48 IST. And no order existed anywhere - Kite is data-in only.

**User question: "why are we sitting in huge cash?"** Answered with numbers: cash is
57.6% of NAV, but the RISK budget is 94.3% used (Rs.4,527.26 of Rs.4,800). Positions are
sized by tail risk, not by capital - median VaR ~21% of price means Rs.1 of risk budget
supports only ~Rs.4.8 of stock, so Rs.4,800 of risk caps notional near Rs.23,000
regardless of idle cash. Being fully invested would require a ~Rs.12,600 risk budget
(21% of book at 99%/12d) instead of 8%. The cash IS the drawdown control.

**Six improvement proposals from the user** - assessed BEFORE implementing at the user's
request, then three applied and two deferred. Full reasoning in DECISIONS.md #001.
The substantive disagreement: the user believed exits are "too late for fast-falling
stocks". Half wrong - the ATR stop already rests intraday and books same-day exits; only
the MA50 trend exit is next-open by design. The real weakness is that stops are modeled
as filling EXACTLY at the stop price, which is optimistic. Deferred to Phase 5 rather
than implemented.

**Built this session:** `DECISIONS.md`, per-stock trade charts, Library section, Costs
Paid section, and this file. All display/documentation only - verified by re-running the
full chain and confirming equity hash `bb1a6ecb40656ab7` UNCHANGED.

**Findings opened:** #002 SBIN stop moved down across runs (replay instability, Phase 5).
#003 the engine charges the whole round-trip cost at EXIT, so NAV is overstated by
~Rs.30 of unbooked buy-side costs while positions are open.

**Later in the same session - working-method changes.** User asked whether raising the
reasoning effort level (medium/high/max) would help. Answer given: most of this project's
usage is mechanical (daily ritual, edits, docs) where higher effort buys nothing; the
minority that is reasoning-bound (correctness debugging, Phase 5 design, RSI/GFS
statistical validation) is where it pays. Agreed a STANDING RULE, saved to auto-memory so
it survives chat clears: Claude suggests an effort level at the start of every task, and
for tasks needing more than routine depth PAUSES first ("this wants high effort - switch
and re-ask, or tell me to proceed?"). Two honesty guards written in: Claude cannot change
the setting AND cannot see what is currently set, so it must phrase suggestions as what
the task NEEDS; and if an answer turns on arithmetic rather than reasoning depth, say so
rather than offering a paid upgrade for the same answer.

**Stock-swap / rotation proposal (user).** "Can we exchange a weak holding for the next
stock we want to buy?" Assessed, not built - see DECISIONS.md #005 when logged. Summary:
the inefficiency is REAL (with the book at 4/4 the system holds the FIRST four candidates
that appeared, not the BEST four - SHRIRAMFIN has now been discarded twice), and the idea
has a proper name (cross-sectional rotation). But it converts time-series momentum into
cross-sectional momentum, so the measured +54.5bp/trade does not transfer; and the cost
arithmetic is brutal at this size - ~Rs.38 per swap versus an OOS CAGR of Rs.636/yr on
Rs.60k, so roughly two extra swaps a month would erase the entire modeled return. Also
flagged the trap: never rank "weak" by unrealized P&L, because the market does not know
your entry price. Recommended cheap next step: LOG every skipped candidate now, and
decide with real data in 2-3 months. Rotation gets better as the book grows.

**`DAILY_KICKOFF.md` updated** (user approved): closing SESSION_LOG step added to the
ritual; new "HOW CLAUDE SHOULD WORK" section (effort suggestions, the handshake, assess-
before-implementing, the equity-hash rule); DECISIONS.md added to the read-before-acting
line; dashboard entry expanded for the three new sections; pending threads refreshed with
git, the two open findings, and the rotation idea.

**Also raised, not yet done:** the project has **no version control at all**. Flagged as
the cheapest available safety win (today four files were edited with no diff and no
revert path; only the equity-hash gate stood between the user and a silent regression).
`git init` also unlocks `/code-review ultra`, which requires a git repo, is user-triggered
and billed, and cannot be launched by Claude.

**Version control added.** `git init` at `nse_paper_v1/` (NOT the Hedge funds root - the
sibling `integrated_mm_v2` is 2.6GB with no gitignore, so a root repo risked committing
gigabytes). Baseline commit `9122d6a`, 48 files, .git 1.1MB, purely local, no remote.
Secret-safety procedure: grepped the STAGED name list before committing - it fired on
`secrets.env.example`, which was INSPECTED rather than assumed (placeholders only, safe);
real `secrets.env` confirmed ignored via `git check-ignore -v`. Second commit `7f5a118`
logged DECISIONS #005 (rotation) and #006 (git).

**`PROJECT_HANDBOOK.md` written** (user: "I am forgetting things - make a document from
the start of this project, every question I asked, every file and what it holds"). Covers
the whole project: what it is, the philosophy, Phases 0-4 with headline numbers, how a
trade happens end to end, the cost stack, a complete file inventory, the bug track record,
carried caveats, every question from this session with its status, daily operating
instructions, and open items. **Effort call given honestly: medium, not high** - the task
was bounded by reading files carefully rather than by reasoning depth, so upselling would
have been wrong. **Limitation stated in the document itself:** questions from sessions
BEFORE 2026-07-21 are unrecoverable (never captured), so the handbook documents what was
BUILT for that period rather than inventing a plausible question list.

**State at end of session.** 4/4 positions at cap: ICICIBANK 8, SBIN 6, BAJFINANCE 2,
INDIGO 1. Cash Rs.34,573.60, NAV Rs.59,971.40. SHRIRAMFIN armed for Wed but will likely
skip again - the book is full and nothing is armed to exit.

---

## Session 2026-07-22 (Wed) - fixing #002, and a bug the fix uncovered

**Effort handshake used.** User asked to "solve these bugs". Flagged that #002 was a
DIAGNOSIS (cause unknown), high effort; user said proceed at current level. Also gave the
honest recommendation NOT to fix #003 now (cost-timing accrual, ~Rs.30, understood) and to
keep the change budget on #002.

**Before starting, user asked what real hedge funds do about data revisions.** Answered:
(1) point-in-time / bitemporal storage - store both the value and when it was known, never
overwrite; this IS option (c). (2) They decide off the exchange's OFFICIAL settlement
price (NSE = VWAP of the last 30 min, in the bhavcopy published after close), not a near-
close snapshot - which is the root cause of our incident (Monday's 18:16 run caught a
provisional candle). User then chose option (c).

**#002 DIAGNOSED then RESOLVED.** Read-only diagnostic proved the cause: Kite REVISED
SBIN's 2026-07-20 close from 1060.60 (what Monday's run saved) to 1060.00 (Tuesday) - a
data restatement, not a code bug; the stop ratchet worked correctly on each run's data.
Mechanism: p11 rebuilt the entire live era from Kite every run and never froze it. Fix
(option c): freeze-first-seen live tape + logged revisions + panel2 built from the frozen
tape + repaired the blind G4.6 source check. The SEED revealed the scale - 38 restatements
across nearly every symbol on 07-20 and 07-21, so every past run had silently rebuilt live
history. Read-only rebuild restores SBIN's stop to 990.95.

**#007 - a serious bug the fix uncovered.** Running the gate suite standalone (to verify
the hash) CLOBBERED the live book: G4.3's resume test wrote the PRODUCTION state path, so
any REPLAY run without the live leg overwrote paper_state.json with a mid-backtest book
(caught it stamped with a 2010 date, equity Rs.75,811). Fixed to a temp path; audited and
guarded a second REPLAY default-path write (the bootstrap). Restored the live book to the
exact last-known-good Tuesday state. Lesson recorded: test/gate code must never write
production paths.

**All verified offline** (no live decision - it was ~15:00, pre-close): equity hash
bb1a6ecb40656ab7 UNCHANGED, reconcile unit test, frozen rebuild reproduces the book to the
paise, gate suite now leaves live state intact. Committed b5814e2. **First live exercise =
tonight's post-15:45 run**, which will show SBIN's stop move 990.35 -> 990.95 as the fix
applies. Book state unchanged and correct going into it.

---

## Session 2026-07-22 (Wed, evening) - RSI/GFS backtested to a verdict, + live day 3

**Two threads:** the user's RSI/GFS strategy (pending thread #1), and the evening day-3
decision run (the "first live exercise" of the #002 fix the earlier entry anticipated).

**RSI/GFS - built, tested, RESOLVED (verdict: no robust edge).** At the user's direction
("make this a whole different project, don't mix it with the live system"), built a
STANDALONE `gfs_backtest/` (own data/code/results; copies the NSE cost model; shares no
trading logic with nse_paper_v1). Pulled NIFTY 200 via yfinance (178 names pass an ex-ante
liquidity+history screen), pre-registered GFS exactly (monthly&weekly RSI>=60 + daily RSI
crosses up through 40 + green candle; trigger-candle-low stop + 3-candle trail; higher-TF
RSI uses only COMPLETED bars = no look-ahead), ran it honestly (per-symbol 70/30, deflate as
trial #10).
- **As pre-registered: NOT SIGNIFICANT.** OOS +13.8 bp/trade net @Rs50k but CI includes 0,
  and negative in-sample. Weaker than ts_mom (+54.5, significant). Causality gate 25/25 pass.
- Diagnosed the leak (user's sharp questions): the 3-candle exit shakes trades out of live
  trends (72% of exits are followed by new highs within 20d). Compared 5 exits; E4 (ATR
  trail) looked great (+197 all-history). **A fallen-angels survivorship test killed it:**
  same GFS+E4 makes +197 on survivors vs **-330** on 21 collapsed names (YESBANK/RCOM/RPOWER
  ...); on losers, let-it-run rides them down. The exit "fix" was mostly survivorship.
- **Lessons taught along the way:** (1) IS/OOS splitting guards against overfitting the TIME
  period but NOT survivorship - both halves used winner-stocks. (2) "Pick the exit with the
  highest return" is the p-hacking trap; shown live (best-on-all-data +325 vs honest-OOS
  +187, the shrinkage = the overfit premium). (3) Clearing a chat does NOT un-peek data - the
  data is fixed and the rules were designed from it; only forward paper-trade or a genuinely
  untouched universe is clean.
- Frozen record: `gfs_backtest/GFS_FINAL_REPORT.md` (+ RESULTS.md, prereg_gfs_v1.md, memory
  `gfs-backtest-verdict`). **PENDING THREAD #1 resolved: GFS tested, not proven, not
  deployed.** Open decision left with user: walk away, or forward paper-trade the frozen rules.

**Evening run (day 3; clock verified ~21:2x IST Wed, well past the 15:45 guard).** All 6
gates PASS in LIVE; token present (morning login done). **#002 fix's first live application
worked: SBIN stop now 990.95** (was 990.35), as predicted. Equity hash bb1a6ecb40656ab7
UNCHANGED.
- No trades (ledger still 1). Same 4 held: ICICIBANK 8, SBIN 6, BAJFINANCE 2, INDIGO 1.
- Armed for Thu open: ETERNAL + SHRIRAMFIN (both ts_mom, ~Rs.1,541) - both likely skip at
  the 4/4 cap (nothing armed to exit).
- NAV Rs.59,464.40, day -507.00 (-0.85%), PURE mark-to-market on the 4 holdings (cash
  unchanged ~Rs.34,574). Live era: 60,000 -> 59,464.40 = -0.89% over 3 trading days. 20/20
  fresh bars, source diff <2% (max ~38bp HINDALCO/ETERNAL), 38 restatements handled (0 new).

**Broken/caught (mine, not the system's):** first p11 launch used a stdin heredoc + os.chdir
that left the project dir off sys.path -> `ModuleNotFoundError: config`. Fixed with an
explicit `sys.path.insert`, re-ran clean. The blessed kickoff command (bash `cd` + `python
-c`) avoids this; my variant did not.

**State at end:** 4/4 at cap, NAV Rs.59,464.40, cash ~Rs.34,574, all gates green, SBIN stop
corrected to 990.95. Thursday's two armed buys likely skip.

---

## Session 2026-07-23 (Thu) - live day 4, afternoon routine, armed buys skip as predicted

**Afternoon ritual only** (user: "run the afternoon routine" then "run it"). Effort call
given up front: mechanical daily ritual, routine effort, no upgrade - the only judgment was
whether we were past the 15:45 guard.

**Clock verified** against Google + Cloudflare HTTP Date headers (both 11:08 UTC = 16:38
IST), agreeing with the system clock to the second. Well past 15:45. `DAILY_KICKOFF.md` does
NOT exist at the repo path the handbook references (`../DAILY_KICKOFF.md`, one level up,
unversioned) - operated from PROJECT_HANDBOOK.md section 9 instead.

**First p11 launch fell back to REPLAY** - key/token were not in the shell env. `secrets.env`
was present and freshly written 09:41 today (this morning's `kite_login.py`), so login WAS
done; the file just wasn't loaded into my shell. Sourced it CR-stripped
(`source <(tr -d '\r' < secrets.env)`, values never printed) and re-ran - LIVE armed clean.

**LIVE decision run - all 6 gates PASS in LIVE.** Equity hash bb1a6ecb40656ab7 UNCHANGED.
- **No trades** (ledger still 1). Wednesday's two armed buys ETERNAL + SHRIRAMFIN **skipped
  at the 4/4 cap** exactly as predicted - nothing armed to exit, so no slot freed.
- Same 4 held: ICICIBANK 8, SBIN 6, BAJFINANCE 2, INDIGO 1 (all ts_mom). Cash unchanged
  Rs.34,573.60.
- **NAV Rs.59,228.20, day -236.20 (-0.40%)**, pure mark-to-market. Live era 60,000 ->
  59,228.20 = -1.29% over 4 trading days; -1.47% from the Rs.60,114.55 peak.
- Armed for Fri open: ETERNAL + SHRIRAMFIN AGAIN (~Rs.1,541 each). **SHRIRAMFIN now armed a
  THIRD time** (skipped 07-21, 07-22 arming, now 07-23) - will skip Friday too unless a
  holding exits. This is the cross-sectional-rotation inefficiency from DECISIONS #005 in
  the wild: the book holds the first four candidates, not the best four.
- Data: 20/20 fresh Kite bars, source diff <2% (max ETERNAL -176bp), 56 revisions detected /
  18 newly logged, frozen-first-seen kept (#002 fix). Kill switch not tripped.

**State at end:** 4/4 at cap, NAV Rs.59,228.20, cash Rs.34,573.60, all gates green in LIVE.
Friday's two armed buys likely skip a third time. Dashboard refreshed.

**Same session (cont.) - "what is our status + I want to make advancements".** Gave an
honest status: the MACHINE is healthy (gates green, #002/#007 fixes holding live) but
PERFORMANCE is unmeasurable at 4 days / 1 realized trade - anyone reading a trend into
-1.29% is fooling themselves. Restated the real constraint: at Rs.60k the strategy is
nearly cost-bound (OOS CAGR ~1.06% / Sharpe ~0.23), it is drawdown-control not a return
engine; the biggest lever is CAPITAL + TIME, not a feature. Laid out four advancement
directions with honest cost/benefit; user picked ALL FOUR.

**Sequenced and built the KEYSTONE (DECISIONS #008): the skipped-candidate logger.** Chosen
first because it is both a cheap integrity win AND the hard prerequisite the DEFERRED
rotation idea (#005) was blocked on - #005's own recommended next step was "log every
skipped candidate now." Added `BookState.skip_log` + a capture at the p9 skip point +
`config.PAPER_SKIPS_PATH` + a live-era freeze in p11. Acceptance gate = equity hash
bb1a6ecb40656ab7 UNCHANGED (proven: the hash is over the equity array, a diagnostic log
can't move it); all 6 gates PASS LIVE. First capture = 6 rows, SHRIRAMFIN skipped all four
days, each row carrying the incumbents it lost to. In 2-3 months a forward-return join
answers the rotation question with data instead of a guess.

**Corrected my own error, recorded honestly.** Early in the session I said DAILY_KICKOFF.md
was "missing" - it was not; it lives at the Hedge funds ROOT (one level up, DECISIONS #006),
I had only globbed the project dir. Read it before touching it, found several stale threads
(git listed as "not done" though #006 did it; #002 listed OPEN though resolved), and
refreshed them rather than overwriting the file.

**Queued (designed, not built this session):** (b) risk-budget dial = run CAGR-vs-maxDD at
8/12/16% so capital efficiency is a numbers decision (needs the user's risk appetite); (c)
strategy expansion (rotation / MR overlay / bigger universe) = needs pre-registration +
more live data, must NOT be fit on 4 days (the GFS p-hacking lesson); (d) Phase 5
reconciliation (still data-starved) + Phase 6 EOD scheduler (buildable now). Changes not yet
committed to git (user hasn't asked).

---

## Session 2026-07-24 (Fri) - risk dial, rotation Piece 1 (shadow tracker), live day 5

**Same rolling session as 07-23** (it crossed midnight into Friday). Threads this day:

**Risk-budget dial (advancement b) - RAN.** Sweep script reused p9's exact machinery;
self-check reproduced production (8% row = 1.06% CAGR / 0.23 Sharpe). Findings: raising the
PORTFOLIO budget 8%->16% roughly TRIPLES OOS CAGR (1.06->2.91%) and doubles Sharpe
(0.23->0.51) while drawdown stays ~flat (-10%) - because at 8% the budget binds hard (94%
used) and throttles positions below their VaR size; raising it un-throttles the risk engine
(Sharpe up = risk-ADJUSTED, not just leverage). Past 16% = diminishing returns. Raising
max_positions instead (Track B) HURT (dilutes into weaker signals + cost). Ceiling: even at
24% the book is only ~38% invested - the limit is SIGNAL AVAILABILITY, not budget; stays
drawdown-control (vs buy&hold 15.5%/-42%). Caveats stated: same 2017-26 OOS period (not new
edge, just sizing closer to it); bigger budget = bigger absolute tail loss (16% risks ~Rs.9.6k
vs 4.8k). It's a risk-APPETITE call (3-number config change, WILL change the hash correctly).
NOT applied - left with the user. Sweep script kept in scratchpad (offered to promote as
p9c_risk_dial.py).

**Clarified a user misunderstanding.** User thought "raise budget => hold more stocks". No:
budget = bigger positions in the SAME 4 names (max_positions is the separate knob), and the
data showed MORE names hurts. Also explained the idle-cash logic step by step (loss budget
not spending budget; ~21% VaR means Rs.1 budget supports ~Rs.4.8 stock; Rs.4,800 caps
notional ~Rs.23k so ~58% stays cash = the drawdown cushion, on purpose).

**Rotation Piece 1 = the SHADOW / what-if tracker - BUILT (DECISIONS #009).** User's own
advancement, two pieces: (1) a column that buys each SKIPPED candidate hypothetically to see
what it would have done; (2) the real stock-swap (rotation) later. Built Piece 1 as
`p11c_swing_shadow.py`: a shadow book runs the IDENTICAL engine with caps lifted + huge cash
so nothing is skipped; the entries the real book never took = the waiting stocks, tracked
with the same entry/stop/exit. Wired into p11.live_smoke (runs every LIVE decision), persists
`data/paper_shadow.parquet`, and added a "Waiting stocks (what-if)" dashboard section. Guards:
writes ONLY the new parquet (never a production path, #007 rule); standalone self-check
reproduces the real ledger+state before trusting the shadow; acceptance gate = full LIVE run,
6 gates PASS, hash bb1a6ecb40656ab7 UNCHANGED. **Teaching point that landed:** the edge
FLIPPED +1.14% (07-23) -> -0.33% (07-24) in ONE day = proof the number is noise; the value is
the verified mechanism, real answer needs 2-3 months. Piece 2 (the swap) still DEFERRED, now
measurable, must be pre-registered (converts ts-mom into cross-sectional; edge won't transfer).

**Afternoon routine (day 5, Fri close).** Clock verified (Google+Cloudflare 18:17 IST). All 6
gates PASS LIVE, hash unchanged. NAV Rs.59,167.00 (day -61.20, -0.10%; live era 60,000 ->
59,167 = -1.39% over 5 days), cash Rs.34,573.60, same 4/4 holds. No trades (ledger 1). Armed
Monday: SHRIRAMFIN only (ts_mom ~Rs.1,541; ETERNAL's signal dropped) - likely a 5th skip.
20/20 fresh bars, 0.0bp source diff, 0 new revisions (76 detected). Shadow read: waiting
-3.33% vs holds -3.00%, edge -0.33%.

**State at end:** 4/4 at cap, NAV Rs.59,167.00, all gates green LIVE, shadow tracker live on
the dashboard. **UNCOMMITTED:** p11c_swing_shadow.py + the #009 wiring/dashboard/config +
#008 logger are in the working tree; offered to commit, user ran the routine instead - commit
still pending. NEXT candidates: commit; the risk-dial decision; rotation Piece 2 (pre-reg).

**COMMITTED (later same session):** all of the above committed as `17bf13b` (#009 shadow +
#010 re-baseline); the #008 logger was already `92f16a7`. Secret scan clean, secrets.env
confirmed ignored. Working tree clean afterward.

---

## Session 2026-07-28 (Tue) - token-expiry incident + two-day catch-up run at 24%

**Token-expiry incident (worth recording).** Monday's afternoon routine was never run. Tue
morning the user asked to run "yesterday's" routine; the LIVE run FAILED G4.6 with
`TokenException: Incorrect api_key or access_token`. Diagnosed WITHOUT printing secrets:
`secrets.env` mtime was 2026-07-27 09:56 = MONDAY's token, expired overnight (Kite tokens die
"early tomorrow morning"). api_key (794bypra16kbpknp, semi-public, in the login URL) + secret
(32 char) were fine - purely a stale daily token. NOT a code bug. Lesson reinforced: the
daily kite_login.py must be done ON the day you run; the presence-only G4.5 check passes on a
stale token, only the live G4.6 call reveals expiry. Book untouched by the failure (gates
that need no Kite passed, state intact). Also caught (clock ritual working): an earlier "run
the afternoon routine" at 09:26 IST was correctly HELD - morning, pre-15:45 guard.

**Two-day catch-up run (Tue 07-28, 16:21 IST, fresh token confirmed mtime 07-28 09:36).** All
6 gates PASS LIVE, hash 0e3e8313789f284a unchanged. ONE run folded in BOTH Monday 07-27 and
Tuesday 07-28 (k_next 7674->7676) - the "skipping a day self-heals by replay" promise proven
live. **NAV Rs.59,108.83, +686.60 (+1.18%) vs Friday's re-based Rs.58,422** - the 24% book
RECOVERED over Mon+Tue after the down week (bigger swings, this time up; live era 60k ->
59,109 = -1.49%). 70% invested, cash Rs.17,661. No trades (ledger 1); same 4 holds (SBIN 11,
ICICIBANK 8, BAJFINANCE 8, INDIGO 2). Armed Wed: ETERNAL + SHRIRAMFIN (~Rs.4,488 each) -
**ETERNAL is BACK** (re-qualified after dropping 07-24 when its 126d return went negative) =
another live proof the armed queue tracks the trend, not a stale-from-start list. 20/20 fresh
bars, 20 revisions logged, max src diff 56bp. Shadow read: waiting +3.25% vs holds -1.24%,
edge +4.49% - the edge has now swung +1.14 -> -0.33 -> +4.49 across three readings = textbook
proof the number is NOISE at this sample; the tracker is the deliverable, not the figure.

**Also this session (mechanics Q answered).** User suspected SHRIRAMFIN was a stale
"recommended-at-the-start" leftover. Proved with data it is NOT: ts_mom entry (close>MA200 &
126d ret>0 & close>MA50) is re-checked FRESH every day; SHRIRAMFIN PASSED all 3 on 07-22/23/24
(genuinely re-qualifying), while ETERNAL PASSED 07-23 then FAILED 07-24 (126d ret -1.2%) and
dropped off instantly. Two separate gates: "want it?" (signal, passes daily) vs "room to buy?"
(4/4 cap, fails daily) - SHRIRAMFIN clears the first, fails the second, hence armed-but-skipped.

**State at end:** 4/4 at cap, NAV Rs.59,108.83, 70% invested at 24% budget, all gates green
LIVE, hash 0e3e8313789f284a. Wed's two armed buys (ETERNAL+SHRIRAMFIN) likely skip at the cap.

---

## Session 2026-07-29 (Wed) - max-pos 6 (#011), UNIVERSE EXPANSION to 95 (#012), first live run

**Three advancements in one day, each verified before the next.** Full detail in DECISIONS
#011/#012; this is the story.

**Morning/afternoon:** P&L tiles explained (Total = NAV-60000 = realized -317 + unrealized
-574; Day = vs previous curve point, stretched over the Mon gap). NAV-curve NaT-date bug
found+fixed+committed (7c7d125). User-authored README.md found untracked (their call
pending). Then #011: user insisted on 6 slots; the untested 24%/6 sweep cell proved the BEST
row (4.05%/0.56/-10.8%) - user right, my Track-B generalization wrong at 24% (which-cap-binds
depends on budget regime; lesson recorded). Applied, gates PASS, hash 118f70b0ea7f5e4f.
Explained the 6-slot allocation process with the exact preview table (each stock Rs.7-11.6k,
gross cap binds, cash ~0). Answered "are we screening daily?" (yes, all names re-tested daily
vs own history; universe fixed; no cross-ranking) and "what happens to the shadow column?"
(ETERNAL/SHRIRAMFIN graduate to real holds; column empties until 6/6 + a 7th signal).

**#012 UNIVERSE EXPANSION 20 -> 95 (user: "expand the screening stocks"; chose NIFTY 100 /
all-passing).** Built+verified in one afternoon: official niftyindices CSV -> 95/100 pass the
unchanged ex-ante screen; new SWING_RESEARCH_CUTOFF=2026-07-10 so the frozen live tape stays
the only live-era source; full backup at data/backup_pre_012/; live-era snapshots (240 rows)
merged back verbatim; p1 gates PASS (536,145 bars), p8 gates PASS (441,050 snapshots,
causality clean), p9+p11 ALL PASS. New baseline hash 45a27ba622b6e021. OOS scoreboard
accepted: combo 2.2%/0.32/-26.9%, ts_mom-ALONE 4.0%/0.59/-21.0% (window now ~2015-26 incl
COVID - not comparable to old numbers). **G3.6 FLIPPED: tom no longer earns its seat on 95
names - drop-tom recommended, USER DECISION STILL PENDING.** Whole-model recap given at
user's request (they were confused - fair, 3 re-baselines in a week).

**First LIVE run on 95 (16:03 IST, token fresh).** ALL 6 GATES PASS LIVE, 95/95 fresh Kite
bars, every symbol resolved, 19 new revisions logged, max source diff 125.5bp (<2% gate).
**The re-based book is a NEW book:** 6/6 = ABB 1, ADANIENSOL 6, APOLLOHOSP 1, ASIANPAINT 4,
BAJAJ-AUTO 1, BAJFINANCE 7; 95% invested, cash Rs.2,895; ledger now 4 trades (retro-replay:
ADANIGREEN -434.82, ADANIPORTS -473.06, ADANIENT -476.24 all trend exits + ASIANPAINT
+137.63). NAV Rs.58,824.56. **Labelled honestly: the NAV drop vs Monday's 59,108 is the
retroactive-replay artifact** - the book now pretends 95 names were always watched, and that
pretend history bought three Adani names on 07-20 that trend-exited at losses. Real money
was not lost this week; the baseline changed.

**QUIRK SURFACED (worth a future decision): alphabetical tie-break.** With 95 candidates,
many qualified simultaneously on 07-20; section (F) iterates symbols in sorted order, so the
first seats went to ABB + three ADANI* names purely by alphabet. Deterministic and unbiased
by returns, but arbitrary - and the 3 realized losses came from exactly those fills. A
pre-registered smarter tie-break (liquidity? momentum?) is a candidate future trial - NOT
changed now (it is a rule change; protocol applies).

**Queue explosion, as designed:** 29 stocks armed for Thu open at ~Rs.6,087 budget each -
ALL will skip (6/6 full, nothing armed to exit). Shadow column reborn at scale: 39 waiting
rows, incl. our FORMER holds (ICICIBANK, SBIN, SHRIRAMFIN, INDIGO, ETERNAL). Early read:
waiting +1.18% vs holds +0.11%. The rotation question (#005, Piece 2) is now MUCH louder at
95 names - the skip/shadow instruments will accumulate evidence fast.

**State at end:** 6/6 (new names), NAV Rs.58,824.56, 95% invested, hash 45a27ba622b6e021,
all gates green LIVE. PENDING: tom decision (drop vs keep), commit of #011+#012 (large
uncommitted set: p0/p1/config/p9-p11 docs), README.md call, Thu ritual.

---

## Session 2026-07-30 (Thu) - dashboard field guide + routine day 10

**Dashboard explained end-to-end (user request).** Built a "Dashboard Field Guide" -- every
section of paper_dashboard.html with the live 07-29 book as the worked example -- delivered
as a rendered file + private artifact
(https://claude.ai/code/artifact/d833cbc6-a682-4cdc-96cf-ed019bf0739c). Also re-explained
P&L mechanics in chat (Total = realized + unrealized; sell = lock, not create).

**Afternoon routine (15:48 IST, token fresh 09:22).** All 6 gates PASS LIVE, hash unchanged,
95/95 fresh bars. **NAV Rs.58,910.86, day +86.30 (+0.15%)** - first green day on the
95-universe book. No trades (ledger 4); same 6 holds; stops ratcheted up on APOLLOHOSP /
BAJAJ-AUTO / BAJFINANCE. Nothing armed to exit; 30 buys armed for Fri - all will skip at
6/6. Shadow: 42 waiting, edge +0.87% (readings so far +1.14 / -0.33 / +4.49 / +0.87 =
noise, accumulating). 145 revisions logged - the one-time settlement restatement of the
75 new symbols' first-seen bars; expect ~20/day again from tomorrow.

**State at end:** 6/6, NAV Rs.58,910.86, 95% invested, all gates green LIVE. Still PENDING:
tom decision, the #011+#012 commit, README.md call.

---

## Session 2026-07-31 (Fri) - week close, best live day yet, tom fires for the first time

**Afternoon routine (16:43 IST, token fresh 09:41).** All 6 gates PASS LIVE, hash
45a27ba622b6e021 unchanged, 95/95 fresh bars. **NAV Rs.59,323.56, day +412.70 (+0.70%) -
best day of the live era.** Two weeks: 60,000 -> 59,323.56 = -1.13%. No trades (ledger 4);
same 6 holds; BAJFINANCE +797.94 / BAJAJ-AUTO +516.20 unrealized, ADANIENSOL -525.10.
Nothing armed to exit.

**`tom` fired live for the first time** (last trading day of July): 45 buys armed for Monday,
15 of them tom + 30 ts_mom - all will skip at 6/6, so it is moot in practice, but it is the
first live illustration of the second sleeve at exactly the moment the drop-tom decision
(#012 open sub-finding) sits undecided. Shadow: 43 waiting, edge +0.26% (five readings:
+1.14/-0.33/+4.49/+0.87/+0.26 - noise, accumulating). 94 revisions logged (new-symbol
settlement restatements still washing through).

**State at end (week close):** 6/6, NAV Rs.59,323.56, 95% invested, all gates green LIVE.
Carries into Monday unchanged unless a stop hits. STILL PENDING: tom decision, the
#011+#012 commit (now 3 days of verified uncommitted work), README.md call.
