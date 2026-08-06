"""
manual_sell.py -- queue a DISCRETIONARY sell for the next evening run (DECISIONS #014).

WHAT THIS IS
------------
The one place a human decision can enter an otherwise mechanical book. You name a symbol
you currently hold; it is recorded as a manual exit, armed at tonight's decision and
FILLED AT THE NEXT SESSION'S OPEN -- exactly the same timing as every rule-driven exit.
No same-day selling exists anywhere in this system.

WHY IT IS A PERSISTENT LOG, NOT A ONE-SHOT QUEUE
------------------------------------------------
p11 rebuilds the entire live era by replay on EVERY run. A manual exit that was merely
"executed once" would silently vanish from the book on the next run, and the ledger would
disagree with itself. So each order is stored permanently with the decision date it was
issued on, and the replay re-applies it on its original fill day forever.

WHY MANUAL EXITS ARE TAGGED
---------------------------
They land in the ledger with reason="manual". The paper trial exists to answer "does this
STRATEGY work?" -- a discretionary sell answers a different question. Tagging keeps the two
measurable apart, and the dashboard reports both a strategy-only and an actual P&L. Losing
that separation would cost more than the override is worth.

USAGE
    python manual_sell.py BAJFINANCE          # queue a sell
    python manual_sell.py BAJFINANCE --undo   # cancel it (only if not yet executed)
    python manual_sell.py --list              # show queued/executed manual orders

ASCII-only prints (Windows cp1252).
"""
from __future__ import annotations

import json
import sys

import pandas as pd

import config as C


def _load() -> list:
    if not C.PAPER_MANUAL_ORDERS_PATH.exists():
        return []
    try:
        doc = json.loads(C.PAPER_MANUAL_ORDERS_PATH.read_text())
    except json.JSONDecodeError:
        print("  [warn] manual_orders.json is unreadable; treating as empty")
        return []
    return doc.get("orders", []) if isinstance(doc, dict) else list(doc)


def _save(orders: list) -> None:
    C.PAPER_MANUAL_ORDERS_PATH.write_text(json.dumps(
        {"note": "Discretionary sells. Filled at the next session's open, tagged "
                 "reason='manual' in the ledger. Written by manual_sell.py.",
         "orders": orders}, indent=2))


def held_symbols() -> dict:
    """Currently open positions from the live book state (symbol -> shares)."""
    if not C.PAPER_STATE_PATH.exists():
        return {}
    doc = json.loads(C.PAPER_STATE_PATH.read_text())
    return {s: int(p["shares"]) for s, p in doc.get("pos", {}).items()}


def cmd_list(orders: list) -> int:
    if not orders:
        print("  no manual orders on file")
        return 0
    print("  %-12s %-8s %-12s %s" % ("SYMBOL", "SIDE", "ISSUED", "STATUS"))
    for o in orders:
        print("  %-12s %-8s %-12s %s" % (o["symbol"], o.get("side", "SELL"),
                                         o["issued_date"], o.get("status", "pending")))
    return 0


def cmd_queue(sym: str, orders: list) -> int:
    pos = held_symbols()
    if not pos:
        print("  [refused] no live book state found -- run the evening routine first")
        return 1
    if sym not in pos:
        print(f"  [refused] {sym} is not currently held.")
        print(f"            held: {', '.join(sorted(pos)) or '(none)'}")
        return 1
    for o in orders:
        if o["symbol"] == sym and o.get("status", "pending") == "pending":
            print(f"  [skip] a pending manual sell for {sym} already exists "
                  f"(issued {o['issued_date']})")
            return 0
    # The issue date is the decision date this order belongs to. The fill is the NEXT
    # session's open, resolved by p11 against the real trading calendar.
    issued = str(pd.Timestamp.today().date())
    orders.append({"symbol": sym, "side": "SELL", "issued_date": issued,
                   "status": "pending"})
    _save(orders)
    print(f"  QUEUED: sell {pos[sym]} share(s) of {sym}")
    print(f"  It fills at the NEXT session's open after {issued}, tagged reason='manual'.")
    print("  Run the evening routine (after 15:45 IST) to execute it.")
    print(f"  To cancel:  python manual_sell.py {sym} --undo")
    return 0


def cmd_undo(sym: str, orders: list) -> int:
    keep, removed = [], 0
    for o in orders:
        if o["symbol"] == sym and o.get("status", "pending") == "pending":
            removed += 1
            continue
        keep.append(o)
    if not removed:
        print(f"  nothing pending for {sym} (already executed orders cannot be undone --"
              f" the fill is part of the book's history)")
        return 1
    _save(keep)
    print(f"  cancelled {removed} pending manual sell(s) for {sym}")
    return 0


def main(argv: list) -> int:
    orders = _load()
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}

    if "--list" in flags or not args:
        return cmd_list(orders)
    sym = args[0].strip().upper()
    if "--undo" in flags:
        return cmd_undo(sym, orders)
    return cmd_queue(sym, orders)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
