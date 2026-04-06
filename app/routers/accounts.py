from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, FintsCredential
from app.crypto import encrypt_pin
from app.services.fints_service import sync_account, test_connection, EASYBANK_FINTS_URL, EASYBANK_BANK_CODE

router = APIRouter(prefix="/accounts")
templates = Jinja2Templates(directory="app/templates")

ACCOUNT_COLORS = ["#6366f1", "#22c55e", "#f97316", "#3b82f6", "#ec4899", "#f59e0b"]


@router.get("/", response_class=HTMLResponse)
def list_accounts(request: Request, db: Session = Depends(get_db)):
    accounts = db.query(Account).all()
    return templates.TemplateResponse("accounts.html", {
        "request": request,
        "accounts": accounts,
        "easybank_url": EASYBANK_FINTS_URL,
        "easybank_blz": EASYBANK_BANK_CODE,
    })


@router.post("/add", response_class=HTMLResponse)
def add_account(
    request: Request,
    name: str = Form(...),
    iban: str = Form(...),
    fints_url: str = Form(EASYBANK_FINTS_URL),
    bank_code: str = Form(EASYBANK_BANK_CODE),
    login: str = Form(...),
    pin: str = Form(...),
    db: Session = Depends(get_db),
):
    iban = iban.replace(" ", "").upper()

    # Check duplicate
    existing = db.query(Account).filter(Account.iban == iban).first()
    if existing:
        accounts = db.query(Account).all()
        return templates.TemplateResponse("accounts.html", {
            "request": request,
            "accounts": accounts,
            "error": f"IBAN {iban} ist bereits vorhanden.",
            "easybank_url": EASYBANK_FINTS_URL,
            "easybank_blz": EASYBANK_BANK_CODE,
        })

    color = ACCOUNT_COLORS[db.query(Account).count() % len(ACCOUNT_COLORS)]
    account = Account(name=name, iban=iban, color=color)
    db.add(account)
    db.flush()

    cred = FintsCredential(
        account_id=account.id,
        fints_url=fints_url,
        bank_code=bank_code,
        login=login,
        pin_encrypted=encrypt_pin(pin),
    )
    db.add(cred)
    db.commit()

    return RedirectResponse("/accounts", status_code=303)


@router.post("/{account_id}/sync", response_class=HTMLResponse)
def sync_now(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404)

    result = sync_account(account, db)

    accounts = db.query(Account).all()
    return templates.TemplateResponse("partials/account_list.html", {
        "request": request,
        "accounts": accounts,
        "sync_result": result,
        "synced_id": account_id,
    })


@router.post("/{account_id}/delete")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if account:
        db.delete(account)
        db.commit()
    return RedirectResponse("/accounts", status_code=303)


@router.post("/{account_id}/test-connection", response_class=HTMLResponse)
def test_fints_connection(account_id: int, request: Request, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account or not account.credentials:
        raise HTTPException(status_code=404)

    result = test_connection(account.credentials)
    return templates.TemplateResponse("partials/connection_result.html", {
        "request": request,
        "result": result,
        "account": account,
    })
