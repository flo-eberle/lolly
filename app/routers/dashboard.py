from datetime import date, timedelta
from decimal import Decimal
from calendar import month_name

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, extract
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Transaction, Category

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    today = date.today()
    month_start = today.replace(day=1)

    accounts = db.query(Account).all()

    total_balance = sum(a.balance or 0 for a in accounts)

    # Monthly income and expense
    month_txs = (
        db.query(Transaction)
        .filter(Transaction.date >= month_start)
        .all()
    )
    income_month = sum(t.amount for t in month_txs if t.amount > 0)
    expenses_month = sum(t.amount for t in month_txs if t.amount < 0)

    # Last 6 months bar chart data
    bar_labels = []
    bar_income = []
    bar_expenses = []
    for i in range(5, -1, -1):
        d = today - timedelta(days=i * 30)
        m_start = d.replace(day=1)
        if m_start.month == 12:
            m_end = m_start.replace(year=m_start.year + 1, month=1, day=1)
        else:
            m_end = m_start.replace(month=m_start.month + 1, day=1)

        txs = db.query(Transaction).filter(
            Transaction.date >= m_start,
            Transaction.date < m_end,
        ).all()
        bar_labels.append(f"{month_name[m_start.month][:3]} {m_start.year}")
        bar_income.append(float(sum(t.amount for t in txs if t.amount > 0)))
        bar_expenses.append(float(abs(sum(t.amount for t in txs if t.amount < 0))))

    # Category donut chart (current month, expenses only)
    cat_data: dict[str, float] = {}
    cat_colors: dict[str, str] = {}
    for tx in month_txs:
        if tx.amount >= 0:
            continue
        if tx.category:
            label = f"{tx.category.icon} {tx.category.name}"
            cat_data[label] = cat_data.get(label, 0) + float(abs(tx.amount))
            cat_colors[label] = tx.category.color
        else:
            label = "❓ Unkategorisiert"
            cat_data[label] = cat_data.get(label, 0) + float(abs(tx.amount))
            cat_colors[label] = "#94a3b8"

    cat_labels = list(cat_data.keys())
    cat_values = [cat_data[k] for k in cat_labels]
    cat_bg_colors = [cat_colors[k] for k in cat_labels]

    # Recurring payments
    recurring = (
        db.query(Transaction)
        .filter(Transaction.is_recurring == True, Transaction.amount < 0)
        .order_by(Transaction.payee)
        .all()
    )
    # De-duplicate by payee, keep most recent
    seen_payees: set[str] = set()
    unique_recurring = []
    for tx in sorted(recurring, key=lambda t: t.date, reverse=True):
        if tx.payee not in seen_payees:
            seen_payees.add(tx.payee)
            unique_recurring.append(tx)

    # Recent transactions
    recent_txs = (
        db.query(Transaction)
        .order_by(Transaction.date.desc())
        .limit(10)
        .all()
    )

    # Uncategorized count
    uncategorized_count = (
        db.query(func.count(Transaction.id))
        .filter(Transaction.category_id.is_(None))
        .scalar()
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "accounts": accounts,
        "total_balance": total_balance,
        "income_month": income_month,
        "expenses_month": expenses_month,
        "bar_labels": bar_labels,
        "bar_income": bar_income,
        "bar_expenses": bar_expenses,
        "cat_labels": cat_labels,
        "cat_values": cat_values,
        "cat_bg_colors": cat_bg_colors,
        "recurring": unique_recurring,
        "recent_txs": recent_txs,
        "uncategorized_count": uncategorized_count,
        "today": today,
    })
