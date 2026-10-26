"""
nse_paper_v1 - global configuration.

A real-time NSE paper-trading system: live Zerodha Kite Connect market data ->
directional/systematic signal -> VaR-based sizing -> LOCAL paper-fill simulator ->
reconciliation. SINGLE platform (Kite + this Python code). No market making
(retail cannot on NSE); no TradingView in the core.

Everything the program needs to be reproducible lives here. Recalibrate constants
per phase; never hardcode them in the phase scripts.

PAPER STAGE = no real orders reach the exchange, so SEBI's retail-algo framework
(Algo-ID / static IP / broker onboarding, mandatory since 2026-04-01) does NOT
apply here. It only applies when/if this is later pointed at LIVE order placement.
"""

from pathlib import Path

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"          # cached historical + captured live tapes (parquet)
ARTIFACTS_DIR = ROOT / "artifacts"  # phaseN_*.json / .npz handoffs
DOCS_DIR = ROOT / "docs"          # summary + learning docs (.docx)
LOG_DIR = ROOT / "logs"           # live-session run logs
for _d in (DATA_DIR, ARTIFACTS_DIR, DOCS_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- instrument (v1)
# v1 = intraday trend/momentum on the NIFTY current-month FUTURE, square-off before
# close. Single liquid instrument isolates the plumbing from strategy complexity.
# The tradingsymbol resolves per-expiry via Kite's instruments dump (Phase 1).
EXCHANGE = "NFO"                  # NSE F&O segment (index futures live here)
UNDERLYING = "NIFTY"             # vs "BANKNIFTY"
INSTRUMENT_KIND = "FUT"          # current-month future for v1
LOT_SIZE = 75                    # NIFTY fut lot (VERIFY current value on Kite instruments;
                                 #   NSE revises lot sizes -- do not trust this blindly)

# ---------------------------------------------------------------- session (IST)
# NSE normal market: 09:15-15:30 IST. We quote/trade only inside the session and
# force a flat book before close. Times are IST (Asia/Kolkata, UTC+5:30).
MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:30"
SQUARE_OFF = "15:20"             # force-flat intraday book before the close auction
NO_NEW_ENTRY_AFTER = "15:00"     # stop opening new positions late in the session
TIMEZONE = "Asia/Kolkata"

# ---------------------------------------------------------------- NSE cost stack
# ALL VALUES ARE DATED APPROXIMATIONS -- the Phase-5 gate reconciles the model to a
# real Zerodha contract note to the paisa. VERIFY against the current Zerodha
# brokerage calculator before trusting backtest net-of-cost numbers (the Phase-2
# gate hinges on these). Rates are fractions of turnover unless noted.
COSTS_FUT = {
    "brokerage_per_order_inr": 20.0,   # Zerodha flat Rs.20/order (or 0.03%, whichever lower)
    "brokerage_pct": 0.0003,           # 0.03% cap side of the "whichever lower"
    "stt_sell_pct": 0.0002,            # STT 0.02% on SELL side (futures)
    "exch_txn_pct": 0.0000173,         # NSE F&O exchange txn charge (~0.00173%); VERIFY
    "sebi_pct": 0.000001,              # SEBI turnover fee Rs.10/crore = 0.0001%
    "stamp_buy_pct": 0.00002,          # stamp duty 0.002% on BUY side
    "gst_pct": 0.18,                   # 18% GST on (brokerage + exch_txn + sebi)
}

# ================================================================ SWING (v1)
# v1 is now a SWING system: per-stock daily signals on a small WATCHLIST, hold ~10-15
# days (hard max < 1 month), VaR-sized. End-of-day decisions (no real-time streaming
# needed for the backtest; a daily scheduled job drives live later). ONE signal+risk
# brain on the underlying, with two pluggable paper-execution backends: equity
# DELIVERY (cash, long-only) first, then STOCK FUTURES (leverage + shorting, expiry).
SWING_WATCHLIST = ["RELIANCE", "HDFCBANK", "INFY", "TCS", "ICICIBANK"]  # base names; ".NS" for yfinance
SWING_YF_SUFFIX = ".NS"
SWING_HISTORY = "max"            # yfinance daily history to pull (deep: many symbols to 1996/2002)
SWING_OOS_FRAC = 0.30           # last 30% of the common date range held out (walk-forward)
SWING_MIN_DAYS = 750            # a symbol needs >= ~3y of daily bars to be included
# RESEARCH-TAPE CUTOFF (DECISIONS #012). Any p1 rebuild truncates here so the research
# tape can never overlap the live era: bars after this date are governed EXCLUSIVELY by
# the frozen first-seen live tape (paper_live_tape.parquet, DECISIONS #002). Without
# this, a re-pull would overwrite frozen live-era history with restated yfinance bars.
SWING_RESEARCH_CUTOFF = "2026-07-10"

# hold horizon (trading days)
SWING_HOLD_TARGET_DAYS = 12     # intended hold
SWING_HOLD_MAX_DAYS = 25        # hard max (< 1 month) -> forced exit

# SELECTED signal (Phase 2 honest search: select-on-IS, confirm-on-OOS, deflate x4 trials).
# ts_mom = close>MA200 AND 126d return>0 AND close>MA50; ATR trailing stop, <=25d hold.
# OOS +54.5bp/trade net, deflated CI LB +11.5bp, 17/20 stocks positive. CAVEAT: universe
# is CURRENT NIFTY 50 -> survivorship bias; per-trade edge != beating buy&hold.
SWING_SELECTED_SIGNAL = "ts_mom"
# first signal family = momentum/breakout with a trend filter + ATR stop (tune in Phase 2)
SWING_TREND_MA = 50             # trend filter: price above its N-day MA
SWING_BREAKOUT_LOOKBACK = 20    # entry: close breaks N-day high
SWING_ATR_LEN = 14
SWING_ATR_STOP_MULT = 2.5       # trailing/initial stop = entry - mult*ATR

# ---- cost stacks (DATED approximations; Phase-5 reconciles to a real contract note) ----
# Equity DELIVERY (Zerodha: zero brokerage on delivery, but STT 0.1% BOTH sides is the big one).
COSTS_EQUITY_DELIVERY = {
    "brokerage_per_order_inr": 0.0,    # Zerodha delivery = free brokerage
    "brokerage_pct": 0.0,
    "stt_pct_each_side": 0.001,        # STT 0.1% on BUY and SELL (delivery)
    "exch_txn_pct": 0.0000297,         # NSE equity exchange txn (~0.00297%); VERIFY
    "sebi_pct": 0.000001,              # Rs.10/crore
    "stamp_buy_pct": 0.00015,          # stamp duty 0.015% on BUY
    "dp_charge_sell_inr": 15.34,       # depository (DP) charge per scrip on SELL (approx, incl GST)
    "gst_pct": 0.18,                   # GST on (brokerage + exch_txn + sebi)
}
# Stock FUTURES (leverage + shorting; monthly expiry -> roll if a hold crosses it).
COSTS_STOCK_FUT = {
    "brokerage_per_order_inr": 20.0,   # Zerodha Rs.20/order (or 0.03%, whichever lower)
    "brokerage_pct": 0.0003,
    "stt_sell_pct": 0.0002,            # STT 0.02% on SELL (futures)
    "exch_txn_pct": 0.0000173,         # NSE F&O exchange txn (~0.00173%); VERIFY
    "sebi_pct": 0.000001,
    "stamp_buy_pct": 0.00002,          # 0.002% on BUY
    "gst_pct": 0.18,
}
SWING_EXEC_BACKEND = "delivery"        # "delivery" (v1 default) or "stock_fut"

# ---- backtest execution assumptions (Phase 2) ----
BACKTEST_NOTIONAL_INR = 200000.0       # nominal capital per trade (fixes % impact of the flat DP fee)
SLIPPAGE_BPS = 3.0                     # per-side slippage on market fills at the open
BACKTEST_WARMUP_DAYS = 60             # bars needed before signals are valid (>= max lookback)

# ---------------------------------------------------------------- risk (reuse VaR engine)
# The daily GARCH-EVT VaR engine at Desktop/VaR Model already models NIFTY & BANKNIFTY
# (GJR-GARCH + GPD tails). Phase 3 ports it for per-day risk sizing + an intraday cap
# and a rolling-drawdown kill switch, scaled to the intraday holding horizon.
VAR_MODEL_DIR = Path(r"C:\Users\Admin\Desktop\VaR Model")
VAR_Q = 0.99
RISK_HORIZON_MIN = 15.0          # intraday holding horizon for vol scaling (tune in Phase 3)
VAR_BUDGET_INR = 50000.0         # per-day risk budget (placeholder; size in Phase 3)

# ---------------------------------------------------------------- Kite Connect (env only)
# NEVER hardcode/echo/log/commit secrets. Read from environment variables (or a
# gitignored local .env the user provides). See secrets.env.example.
KITE_API_KEY_ENV = "KITE_API_KEY"
KITE_API_SECRET_ENV = "KITE_API_SECRET"
KITE_ACCESS_TOKEN_ENV = "KITE_ACCESS_TOKEN"   # daily token from the login flow
# Historical + live websocket data require the paid Kite Connect plan (Rs.500/mo/key).

# ---------------------------------------------------------------- safety rails (Phase 7)
MAX_POSITION_LOTS = 5            # hard inventory cap (paper AND any future live)
MAX_ORDER_LOTS = 1              # max lots per single order
MAX_ORDERS_PER_SEC = 2          # stay far under SEBI's 10/sec algo threshold
STALE_DATA_KILL_S = 5.0         # dead-man switch: flatten if data stalls this long

# ================================================================ PHASE 3 - RISK LAYER
# Turn the per-trade edge (Phase 2) into a risk-controlled BOOK. Per-stock daily
# GARCH-EVT VaR sizes each position to a fixed rupee loss budget; two signal sleeves
# (ts_mom primary + tom secondary) are combined by RISK-PARITY (not the hand-tuned
# 50/50 of Phase 2); a portfolio cap + rolling-drawdown kill switch bound the book.
# All values are DATED design choices -- a failed gate is a finding, not a knob to loosen.

# ---- holding-horizon risk (swing = multi-day; distinct from the parked intraday track) ----
# The VaR is a loss over the HOLDING horizon, not one day. RISK_HORIZON_MIN above is the
# leftover intraday constant; swing sizing uses the daily holding horizon below.
RISK_HORIZON_DAYS = SWING_HOLD_TARGET_DAYS   # 12 trading days -> GARCH multi-step vol scaling
VAR_DRIFT_ZERO = True            # size VaR with zero mean drift (conservative; keeps the
                                 #   signal's positive edge OUT of the risk estimate)

# ---- GARCH-EVT engine (ported from Desktop/VaR Model, numba-accelerated GJR path) ----
GARCH_MIN_WINDOW = 1000          # need >= ~4y of returns before the first risk snapshot
                                 #   (also guarantees >= 80 tail exceedances at a 10% threshold
                                 #    so the GPD shape parameter is stable from the first fit)
GARCH_REFIT_EVERY = 63           # refit params quarterly on an EXPANDING prior-only window
                                 #   (between refits the variance recursion rolls forward -
                                 #    still strictly causal); deterministic cadence => testable
EVT_MIN_EXCEEDANCES = 80         # Gate G3.2: below this the GPD shape xi is unstable
EVT_THRESHOLD_PCTS = [0.90, 0.925, 0.95]   # candidate GPD thresholds (percentile of losses)

# ---- position sizing + portfolio caps (long-only equity DELIVERY book) ----
# 2026-07-18: capital set to the USER'S REAL intended trading budget (Rs.60k) so the
# paper stage rehearses reality. Risk budgets keep the validated proportions of the
# Rs.10L research book (1.5% / 8% of capital). KNOWN CONSEQUENCES at this size,
# measured not hidden: flat costs (DP Rs.15.34/sell) weigh more per trade, and stocks
# whose single-share VaR exceeds the per-position budget become unsizeable (skipped).
INITIAL_CAPITAL_INR = 60_000.0         # starting book equity = real future budget
# 2026-07-18 OPTION B (superseded): CONCENTRATED book, per-pos 3% / portfolio 8%, max 4.
# 2026-07-24 RE-BASELINE to TRACK A 24% (user choice, DECISIONS #010). The risk-dial sweep
# (p9c/scratchpad) showed the 8% budget BINDS hard (~94% used), throttling positions below
# their VaR size. Raising the dial un-throttles the risk engine: OOS CAGR 1.06%->3.52%,
# Sharpe 0.23->0.54, maxDD ~flat (-10.7%->-10.9%), mean exposure 22%->38%. Same 4-name
# structure (Track B = more names HURT). Ratio 3%-pos:8%-port preserved -> 9%-pos:24%-port.
# NOTE: raising the dial sizes closer to an edge already measured (2017-26 OOS); it does not
# create new edge, and a bigger budget means a bigger absolute tail loss. This is a risk-
# APPETITE choice; it re-bases the whole live book and mints a new equity hash (both correct).
# 2026-07-29 (DECISIONS #011, user choice): max positions 4 -> 6. The originally-untested
# 24%/6 sweep row turned out the BEST config: OOS CAGR 4.05% / Sharpe 0.56 / maxDD -10.8%
# (vs 3.43%/0.51/-10.9% at 4). Mechanism: at 24% the budget was only ~68% used -- the name
# count/notional caps bound, not risk -- so slots 5-6 deploy the surplus productively.
# (At 8-16% budgets the OPPOSITE held: budget bound, extra names diluted. Both recorded.)
PER_POSITION_RISK_BUDGET_INR = 5_400.0 # 99% horizon loss budget PER position (9%)
PORTFOLIO_RISK_BUDGET_INR = 14_400.0   # 99% horizon loss budget across open positions (24%)
MAX_CONCURRENT_POSITIONS = 6           # hard cap on simultaneous open names
MAX_POSITION_NOTIONAL_FRAC = 0.20      # <= 20% of current equity in any single name
MAX_GROSS_EXPOSURE_FRAC = 1.00         # long-only, no leverage: invested <= equity

# ---- entry ranking: WHICH candidate gets a scarce slot (DECISIONS #018) ----
# Until now the answer was ALPHABETICAL, by accident: Market.syms is sorted(), step_day
# section (F) armed entries in ascending symbol index, and section (B) consumed that dict
# in insertion order. Harmless at 20 names where signals were scarce; decisive after #012
# widened the universe to 95, where ~37 candidates compete for 6 slots EVERY day. Evidence:
# all 10 slot-fills of the live era begin with A or B, 4 of them Adani-group.
#
# ts_mom is a TIME-SERIES momentum filter (Moskowitz-Ooi-Pedersen): it says "is this name
# trending?", not "which trending name is best". The standard fix is a CROSS-SECTIONAL
# overlay (Jegadeesh-Titman): score every armed candidate, fill free slots best-first.
#
# SCORE = (L-day return) / (stdev of daily log returns over the same L) -- risk-adjusted
# momentum. L reuses ts_mom's OWN 126-day lookback, so this introduces NO new tuned
# parameter. Any constant scaling (e.g. sqrt(252)) is monotone and would not change the
# ranking, so it is omitted. Scored at the decision CLOSE = same causality as the signal.
#
# SCOPE: ranking allocates FREE slots only. It never displaces an incumbent -- that is
# rotation (DECISIONS #005), a separate feature awaiting the skip/shadow evidence.
# REVERSIBLE: set ENTRY_RANKING="alpha" to restore the legacy alphabetical order exactly.
ENTRY_RANKING = "xs_score"       # "xs_score" (cross-sectional) | "alpha" (legacy)
ENTRY_SCORE_LOOKBACK = 126       # == ts_mom's ret126 horizon; NOT independently tuned

# ---- rolling-drawdown kill switch ----
KILL_DD_THRESHOLD = 0.20         # flatten ALL + halt when drawdown from peak breaches 20%
KILL_COOLDOWN_DAYS = 21          # stay halted this many trading days, then re-arm (peak reset)

# ---- sleeve risk-weighting (the principled replacement for Phase 2's 50/50) ----
# 2026-08-03 (DECISIONS #013, user choice): `tom` DROPPED. On the 95-name universe the
# pre-registered gate G3.6 flipped -- ts_mom-alone dominates the 2-sleeve risk-parity combo
# on BOTH axes (OOS Sharpe 0.59 vs 0.32, maxDD -21.0% vs -26.9%). tom was always flagged
# MARGINAL (deflated CI LB +2.8bp) and its turn-of-month edge dilutes across 95 names, yet
# risk-parity was handing it ~55% of the risk budget. Single-sleeve => weights are trivially
# 1.0 and budget_fn returns the flat per-position budget (n * 1.0 * base = base).
# REVERSIBLE: restore ["ts_mom", "tom"] and re-run the chain to recover the 2-sleeve book.
SLEEVES = ["ts_mom"]             # primary only (tom retired -- see DECISIONS #013)
SLEEVE_WEIGHT_METHOD = "risk_parity"   # inverse realized-vol; walk-forward, prior-only
SLEEVE_VOL_WINDOW = 252          # trailing window (days) for each sleeve's realized vol
SLEEVE_REWEIGHT_EVERY = 21       # re-estimate sleeve weights monthly
SLEEVE_MIN_HISTORY = 63          # until this much sleeve history exists, fall back to 50/50

# ================================================================ PHASE 4 - LIVE PAPER
# EOD paper trader: TWO run modes, ONE code path (the shadow/replay pattern from
# integrated_mm_v2 phase7). REPLAY = offline, replays swing_daily.parquet through the
# same step_day the batch book uses (gate suite). LIVE = after 15:30 IST on a trading
# day, pull the latest DAILY bar per symbol from Kite, run one decision, record a
# PAPER fill. NO real orders at the paper stage.
RUN_MODE_DEFAULT = "REPLAY"          # "REPLAY" | "LIVE" (LIVE also needs keys present)
KITE_INTERVAL = "day"                # EOD system: daily bars only
KITE_HISTORICAL_LOOKBACK_DAYS = 400  # bars pulled per symbol in LIVE (enough for MA200 warmup)
KITE_EXCHANGE_EQ = "NSE"             # cash-equity segment for instrument lookup
FEED_STALE_MAX_DAYS = 5              # dead-man: halt if the newest bar is older than this
                                     #   many calendar days (holiday-safe; a week of silence
                                     #   means the feed is broken, not a long weekend)
PAPER_STATE_PATH = DATA_DIR / "paper_state.json"      # engine state (positions/cash/halt)
PAPER_LEDGER_PATH = DATA_DIR / "paper_ledger.parquet" # append-only paper-fill ledger
# POINT-IN-TIME live tape (DECISIONS #002): live-era Kite bars FROZEN at first-seen, so
# replay is reproducible and a later Kite revision cannot silently rewrite a past
# decision. Revisions are appended to a log (never applied) -- the bitemporal discipline
# real desks use. swing_daily.parquet (research tape) is frozen at the research cutoff;
# this file carries everything after it.
PAPER_LIVE_TAPE_PATH = DATA_DIR / "paper_live_tape.parquet"    # frozen first-seen live bars
PAPER_REVISIONS_PATH = DATA_DIR / "paper_bar_revisions.parquet" # logged Kite restatements
# Skipped-candidate log (DECISIONS #005 prerequisite): every armed entry the caps
# discarded, with the incumbents it lost to. Answers "did the skips outperform the
# holds?" with real data in 2-3 months, so rotation can be decided by evidence not guess.
PAPER_SKIPS_PATH = DATA_DIR / "paper_skips.parquet"           # live-era discarded candidates
# Shadow / what-if tracker (DECISIONS #005 Piece 1, p11c_swing_shadow.py): each skipped
# candidate tracked as a hypothetical position vs the stocks we kept -- the evidence for
# the eventual stock-exchange (rotation) feature. Rebuilt in full each run, like the ledger.
PAPER_SHADOW_PATH = DATA_DIR / "paper_shadow.parquet"         # waiting-vs-holds what-if table
# MANUAL override orders (DECISIONS #014). A persistent LOG, not a queue: the live era is
# replayed from scratch every run, so a manual exit must be re-applied on its original fill
# day every time or it would vanish on the next run. Exits land in the ledger tagged
# reason="manual" so discretionary trades stay separable from the strategy's own record.
PAPER_MANUAL_ORDERS_PATH = DATA_DIR / "manual_orders.json"    # user-issued discretionary sells
# The LIVE paper book starts FLAT with INITIAL_CAPITAL on this date (2026-07-18 user
# ruling: "my capital is 60k, not the simulation's grown equity"). History before this
# date is signal/risk warmup + backtest evidence only; the live ledger begins here.
PAPER_ERA_START = "2026-07-17"        # first live decision day (Fri close -> Mon fills)

# ---- ERA 2: grandfathered handover to the cleaned data (DECISIONS #019, user ruling) ----
# The live era is REPLAYED FROM SCRATCH every run, so any input change rewrites the whole
# recorded past. Cleaning the tape (#019) would therefore have re-based the book and moved
# holdings the user never sold. Their ruling: "whatever current holdings are, let them be --
# when we sell and then buy new stock, buy with the updated system."
#
# Era 2 implements exactly that. A SEED file (a snapshot of paper_state.json taken at the
# handover) carries the open positions across untouched -- real entry prices, entry dates,
# stops, share counts, cash and drawdown peak. The replay then starts at the seed's OWN
# k_next, so era 1 is never recomputed and every decision from the handover onward uses the
# cleaned data.
#
# Consequences, stated rather than buried:
#   * era 1 (2026-07-17..handover) ran on CONTAMINATED data. Its 11 closed trades are
#     archived under data/era1/ and are NOT mixed into era 2's ledger.
#   * Phase 5's 30-trade verdict counts ERA 2 ONLY. Era 1 cannot be pooled with it.
#   * the grandfathered positions were selected by the OLD numbers. Bounded: every one
#     exits within the 25-day max hold, so the book is fully clean-selected within ~5 weeks.
#   * the drawdown peak carries over truthfully (the book really did peak at ~60,124), so
#     the kill switch is NOT given a fresh start.
# Delete the seed file to fall back to the original single-era replay.
PAPER_ERA2_SEED_PATH = DATA_DIR / "paper_era2_seed.json"   # presence activates era 2
PAPER_ERA1_ARCHIVE_DIR = DATA_DIR / "era1"                 # era-1 ledger/state/curve

# ================================================================ DATA CLEANING (#019)
# 117 bars across 37 of the 95 symbols carry moves that cannot be real prices. They enter
# the GARCH/EVT fit as ordinary observations and dominate it: ABB's single +443% bar gave
# it skew 28.3 / excess kurtosis 1,555 and collapsed the GJR fit to alpha=0, gamma=0.
#
# yfinance auto_adjust=True is ALREADY applied in p1 and handles every split Yahoo knows
# about. Verified 2026-08-25: NOT ONE flagged jump matches a known split -- what survives
# are DEMERGERS (VEDL, TMPV) and older actions missing from Yahoo's database. So the feed
# cannot classify these; the tape has to.
#
# Three-way classification (user ruling 2026-08-25, DECISIONS #019):
#   jumps and REVERTS next day      -> bad print. Exclude both returns from fitting; the
#                                      bar is marked untradeable. Nothing is interpolated:
#                                      we do not invent a price that never traded.
#   one-way, matches a known split  -> already handled upstream by auto_adjust.
#   one-way, large, no known split  -> CORPORATE ACTION. Back-adjust prior prices onto one
#                                      continuous series and KEEP TRADING the symbol.
#
# The classifier leans on a physical fact: NSE price bands cap most scrips at 20% a day, so
# a genuine one-way move past 40% in a NIFTY-100 name is effectively impossible.
# HONEST LIMIT: the adjustment ratio is INFERRED from the observed jump, so it absorbs that
# day's real market move -- an error of roughly 1.5% on a 65% jump. Every adjustment is
# logged so a hand-verified ratio can replace it. A demerger also splices two different
# entities into one series (pre-demerger VEDL was a bigger company); vendors do the same,
# but state it, never bury it.
CLEAN_ONEWAY_LOGRET = 0.40    # one-way bar: |log-return| above this = corporate action
CLEAN_REVERT_LOGRET = 0.25    # spike-revert: looser, because the reversal is self-verifying
CLEAN_REVERT_FRAC = 0.70      # next bar must undo >= this fraction of the move to count
CLEAN_TAPE_PATH = DATA_DIR / "swing_daily_clean.parquet"      # cleaned research tape
CLEAN_REPORT_PATH = ARTIFACTS_DIR / "p1b_clean_report.json"   # full audit of every change
# Master switch. False = the original tape, byte-for-byte previous behaviour (reversible).
# True = p8/p9 read the cleaned tape and honour its drop_from_fit column.
USE_CLEAN_TAPE = True

# ---------------------------------------------------------------- reproducibility
SEED = 42
