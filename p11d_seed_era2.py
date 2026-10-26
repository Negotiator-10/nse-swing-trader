"""
Era-2 handover: grandfather the current book onto the cleaned data (DECISIONS #019).

THE PROBLEM
-----------
The live era is REPLAYED FROM SCRATCH on every run, so changing any input rewrites the
whole recorded past. Cleaning the tape (#019) changed position sizing, so the next LIVE run
would have re-based the book and moved holdings the user never sold -- exactly what #018 did
and exactly what the user asked not to happen again.

THE USER'S RULING
-----------------
"Whatever current holdings are, let them be -- when we sell and then buy new stock, buy with
the updated system."

WHAT THIS DOES
--------------
Freezes today's book into a SEED file and archives era 1. From the next LIVE run:
  * the 6 open positions carry across VERBATIM -- entry prices, entry dates, stops, share
    counts, cash and the drawdown peak, all untouched
  * era 1 is never recomputed, so its history cannot be rewritten again
  * every NEW decision uses the cleaned data
  * era 2 keeps its own ledger, so Phase 5's 30-trade verdict is not polluted by trades
    made on data we now know was wrong

WHAT IT COSTS, STATED PLAINLY
-----------------------------
  * era 1 and era 2 ran on different data. Any statistic spanning both is a blend and must
    be labelled as one -- never quoted as a single track record.
  * the grandfathered positions were chosen by the OLD (contaminated) numbers. Bounded:
    all six exit within the 25-day max hold, so the book is fully clean-selected in ~5 weeks.
  * the drawdown peak carries over truthfully. The kill switch does NOT get a fresh start.

REVERSIBLE: delete data/paper_era2_seed.json and the engine falls back to the single-era
replay from PAPER_ERA_START.

Read-only w.r.t. the live book -- it COPIES state, never mutates it.
ASCII-only prints.  Run:  C:/Users/Admin/anaconda3/python.exe p11d_seed_era2.py
"""
from __future__ import annotations

import json
import shutil
import datetime as dt

import pandas as pd

import config as C


def main() -> int:
    print("=" * 64)
    print("  ERA-2 HANDOVER - grandfather the current book onto cleaned data")
    print("=" * 64)

    if not C.PAPER_STATE_PATH.exists():
        print("  FAIL: no live book at data/paper_state.json -- nothing to grandfather.")
        return 1
    if C.PAPER_ERA2_SEED_PATH.exists():
        print(f"  FAIL: {C.PAPER_ERA2_SEED_PATH.name} already exists. Era 2 is already active.")
        print("        Delete it deliberately if you intend to re-seed.")
        return 1

    doc = json.loads(C.PAPER_STATE_PATH.read_text())
    pos = doc.get("pos", {})
    print(f"\n  current book: NAV Rs.{doc['last_equity']:,.2f}  cash Rs.{doc['cash']:,.2f}  "
          f"k_next={doc['k_next']}")
    print(f"  drawdown peak carried forward: Rs.{doc['peak']:,.2f}  (kill switch NOT reset)")
    print(f"\n  GRANDFATHERED POSITIONS ({len(pos)}) - carried across untouched:")
    for s, p in sorted(pos.items()):
        print(f"    {s:<12s} {p['shares']:>3d} sh  entry {p['entry_px']:>10.2f}  "
              f"stop {p['stop']:>10.2f}  since {str(p['entry_date'])[:10]}")

    # ---- archive era 1 ----
    arch = C.PAPER_ERA1_ARCHIVE_DIR
    arch.mkdir(parents=True, exist_ok=True)
    archived = []
    for src in (C.PAPER_STATE_PATH, C.PAPER_LEDGER_PATH,
                C.DATA_DIR / "paper_equity_curve.parquet", C.PAPER_SKIPS_PATH):
        if src.exists():
            shutil.copy2(src, arch / src.name)
            archived.append(src.name)

    n_era1 = 0
    realized = 0.0
    if C.PAPER_LEDGER_PATH.exists():
        led = pd.read_parquet(C.PAPER_LEDGER_PATH)
        n_era1, realized = len(led), float(led["pnl"].sum())
    print(f"\n  ERA 1 ARCHIVED to {arch}/  ({', '.join(archived)})")
    print(f"    {n_era1} closed trades, realized Rs.{realized:,.2f} -- kept as history,")
    print(f"    NOT pooled with era 2 (different data; Phase 5 counts era 2 only).")

    # ---- write the seed ----
    seed = dict(doc)
    seed["_era2"] = {
        "seeded_utc": dt.datetime.utcnow().isoformat(),
        "seeded_from": str(C.PAPER_STATE_PATH),
        "reason": "DECISIONS #019 -- grandfather holdings onto the cleaned tape",
        "era1_closed_trades": n_era1,
        "era1_realized": realized,
        "use_clean_tape": bool(getattr(C, "USE_CLEAN_TAPE", False)),
        "entry_ranking": C.ENTRY_RANKING,
    }
    C.PAPER_ERA2_SEED_PATH.write_text(json.dumps(seed, indent=2, default=str))
    print(f"\n  wrote {C.PAPER_ERA2_SEED_PATH}")

    print("\n  " + "-" * 60)
    print("  ERA 2 IS ARMED. It takes effect on the NEXT LIVE RUN, which will:")
    print("    - resume at k=%d, NOT replay era 1" % doc["k_next"])
    print("    - keep the %d positions above exactly as they are" % len(pos))
    print("    - use the CLEANED tape for every new decision")
    print("    - start a fresh ledger (era 1 lives in %s/)" % arch.name)
    print("  That run will also recompute ~3,040 live-era risk snapshots, so it is SLOW.")
    print("  Reversible: delete the seed file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
