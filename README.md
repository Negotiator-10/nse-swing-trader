# NSE Equity Swing Paper-Trader (`nse_paper_v1`)

A real-time **NSE paper-trading system** — Zerodha Kite Connect + Python: live daily data → systematic swing signal → **VaR-based position sizing** → local paper-fill simulator → live-vs-backtest reconciliation. Reuses the **GARCH-EVT VaR engine** (its own repo) and the streaming shadow-harness pattern from the **`integrated_mm_v2`** market maker. **Live paper trading since 2026-07-17.**

## Why this matters (Risk Quant / Model Validation)

End-to-end research→live discipline with the single property a validation team cares about most: **the live system reproduces the backtest byte-for-byte.** Plus VaR-based sizing, a portfolio risk cap that binds, a drawdown kill switch — and an honest headline that the book is built for **drawdown control, not return-chasing**.

## Headline results

- **online == batch, byte-for-byte:** equity error **0.0**, **2,848 / 2,848 trades identical**, same SHA `feb7869e…`. This is the most convincing single artifact you can put in front of a model-validation reviewer.
- **Signal chosen by honest search** (select-in-sample / confirm-OOS / deflate ×4): **`ts_mom`** — OOS **+54.5 bp/trade**, deflated CI lower bound **+11.5 bp**, **17 / 20 stocks positive**, ~12-day hold. Robustness: **16/16** one-at-a-time parameter configs positive (a plateau, not a peak); random-entry null *p* ≈ 0.000.
- **Risk layer** (per-stock GARCH-EVT VaR, walk-forward, strictly causal — as-of recompute reproduces the panel with **0 mismatches** across **105,717 snapshots**): the **risk-parity `ts_mom`+`tom` book beats `ts_mom`-alone on both Sharpe (0.70 vs 0.49) and max drawdown (−5.0% vs −7.3%)** out-of-sample.
- **Drawdown control:** full-period max drawdown **−12.9% vs buy-and-hold −75.3%**.
- **Caught a live-only bug** (`tom` turn-of-month flag firing every live day) and hash-proved it had **zero** backtest impact.

## Honest headline (on the record)

Mean exposure ≈ 15% under a conservative risk budget → combined OOS CAGR 2.2% vs buy-and-hold 15.5%. The book is **drawdown-control, not return**; the risk budget is a dial, and idle cash earns 0 in the sim (understates absolute return). Survivorship caveat (current NIFTY universe) disclosed. *Stating this plainly is the point — it is the validation mindset.*

## Architecture (gated phases)

`NIFTY-50 universe screen (no return peeking) → daily OHLCV panel (1996–2026) → causal signal library + honest OOS search + robustness + diversifier → GARCH-EVT VaR sizing + risk-parity sleeves + portfolio cap + kill switch → live Kite EOD feed + local paper-fill engine (REPLAY/LIVE, one code path)`. Reconciliation + scheduled loop: pending.

## Reproducibility & security

Python + numba, seed-fixed, walk-forward, no look-ahead · one code path for REPLAY/LIVE · full NSE delivery cost stack hand-checked · state persists and resumes byte-identically. **Kite API credentials live in environment variables, never committed.**

## Tech stack

Python — numpy, pandas, scipy, numba · Zerodha Kite Connect · yfinance (daily). Per-phase `docs/p*_summary.docx` + `*_learning.docx`.
