from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Request, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Transaction, Category

router = APIRouter(prefix="/transactions")
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def list_transactions(
    request: Request,
    db: Session = Depends(get_db),
    account_id: Optional[int] = None,
    category_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    month: Optional[str] = Query(None),  # YYYY-MM
    page: int = Query(1, ge=1),
):
    PAGE_SIZE = 50

    query = db.query(Transaction).order_by(Transaction.date.desc())

    if account_id:
        query = query.filter(Transaction.account_id == account_id)
    if category_id == 0:
        query = query.filter(Transaction.category_id.is_(None))
    elif category_id:
        query = query.filter(Transaction.category_id == category_id)
    if q:
        term = f"%{q}%"
        query = query.filter(
            Transaction.payee.ilike(term) | Transaction.purpose.ilike(term)
        )
    if month:
        try:
            year, mon = int(month[:4]), int(month[5:7])
            from calendar import monthrange
            last_day = monthrange(year, mon)[1]
            query = query.filter(
                Transaction.date >= date(year, mon, 1),
                Transaction.date <= date(year, mon, last_day),
            )
        except (ValueError, IndexError):
            pass

    total = query.count()
    txs = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    accounts = db.query(Account).all()
    categories = db.query(Category).all()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return templates.TemplateResponse("transactions.html", {
        "request": request,
        "transactions": txs,
        "accounts": accounts,
        "categories": categories,
        "selected_account": account_id,
        "selected_category": category_id,
        "q": q or "",
        "month": month or "",
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@router.post("/{tx_id}/categorize", response_class=HTMLResponse)
def categorize_transaction(
    tx_id: int,
    request: Request,
    category_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if tx:
        tx.category_id = category_id if category_id else None
        db.commit()

    # HTMX: return just the row fragment
    categories = db.query(Category).all()
    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    return templates.TemplateResponse("partials/tx_row.html", {
        "request": request,
        "tx": tx,
        "categories": categories,
    })


@router.post("/{tx_id}/note", response_class=HTMLResponse)
def update_note(
    tx_id: int,
    request: Request,
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    if tx:
        tx.note = note
        db.commit()
    categories = db.query(Category).all()
    tx = db.query(Transaction).filter(Transaction.id == tx_id).first()
    return templates.TemplateResponse("partials/tx_row.html", {
        "request": request,
        "tx": tx,
        "categories": categories,
    })
