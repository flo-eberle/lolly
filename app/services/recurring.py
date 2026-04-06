"""
Detect recurring payments (subscriptions, rent, etc.).

Strategy: for each payee, if there are ≥3 transactions with similar amounts
(within 5%) appearing roughly monthly (25-35 days apart), mark them as recurring.
"""

from decimal import Decimal
from itertools import combinations
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import Transaction


def detect_recurring(db: Session, account_id: int | None = None) -> int:
    """
    Mark recurring transactions. Returns count of newly marked transactions.
    """
    query = db.query(Transaction)
    if account_id is not None:
        query = query.filter(Transaction.account_id == account_id)

    all_txs = query.order_by(Transaction.payee, Transaction.date).all()

    # Group by payee (non-empty)
    groups: dict[str, list[Transaction]] = {}
    for tx in all_txs:
        key = tx.payee.strip().upper()
        if not key:
            continue
        groups.setdefault(key, []).append(tx)

    newly_marked = 0

    for payee, txs in groups.items():
        if len(txs) < 3:
            continue

        # Sort by date
        txs.sort(key=lambda t: t.date)

        # Check amount similarity (all within 5% of median)
        amounts = [abs(tx.amount) for tx in txs]
        median = sorted(amounts)[len(amounts) // 2]
        if median == 0:
            continue

        similar = [a for a in amounts if abs(a - median) / median <= 0.05]
        if len(similar) < 3:
            continue

        # Check date intervals (roughly monthly: 25-40 days)
        dates = [tx.date for tx in txs]
        intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
        monthly_intervals = [d for d in intervals if 20 <= d <= 45]

        if len(monthly_intervals) < len(intervals) * 0.6:
            continue

        # Mark all matching transactions as recurring
        for tx in txs:
            if not tx.is_recurring:
                tx.is_recurring = True
                newly_marked += 1

    db.commit()
    return newly_marked
