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
PER_POSITION_RISK_BUDGET_INR = 5_400.0 # 99% horizon loss budget PER position (9%)
PORTFOLIO_RISK_BUDGET_INR = 14_400.0   # 99% horizon loss budget across open positions (24%)
MAX_CONCURRENT_POSITIONS = 4           # hard cap on simultaneous open names
MAX_POSITION_NOTIONAL_FRAC = 0.20      # <= 20% of current equity in any single name
MAX_GROSS_EXPOSURE_FRAC = 1.00         # long-only, no leverage: invested <= equity

# ---- rolling-drawdown kill switch ----
KILL_DD_THRESHOLD = 0.20         # flatten ALL + halt when drawdown from peak breaches 20%
KILL_COOLDOWN_DAYS = 21          # stay halted this many trading days, then re-arm (peak reset)

# ---- sleeve risk-weighting (the principled replacement for Phase 2's 50/50) ----
SLEEVES = ["ts_mom", "tom"]      # primary (strong) + secondary (marginal, uncorrelated)
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
# The LIVE paper book starts FLAT with INITIAL_CAPITAL on this date (2026-07-18 user
# ruling: "my capital is 60k, not the simulation's grown equity"). History before this
# date is signal/risk warmup + backtest evidence only; the live ledger begins here.
PAPER_ERA_START = "2026-07-17"        # first live decision day (Fri close -> Mon fills)

# ---------------------------------------------------------------- reproducibility
SEED = 42
