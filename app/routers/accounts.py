import os
import logging

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account
from app.services import gocardless_service as gc
from app.services.csv_import import import_csv
from app.services.gocardless_service import sync_account

log = logging.getLogger(__name__)
router = APIRouter(prefix="/accounts")
templates = Jinja2Templates(directory="app/templates")

ACCOUNT_COLORS = ["#6366f1", "#22c55e", "#f97316", "#3b82f6", "#ec4899", "#f59e0b"]


def _app_url() -> str:
    return os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")


# ---------------------------------------------------------------------------
# List accounts
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def list_accounts(request: Request, db: Session = Depends(get_db)):
    accounts = db.query(Account).all()
    gc_settings = gc.get_settings(db)
    institutions = []
    gc_error = None

    if gc_settings:
        try:
            institutions = gc.list_institutions(db)
        except Exception as e:
            gc_error = str(e)

    return templates.TemplateResponse("accounts.html", {
        "request": request,
        "accounts": accounts,
        "institutions": institutions,
        "gc_configured": gc_settings is not None,
        "gc_error": gc_error,
    })


# ---------------------------------------------------------------------------
# GoCardless: start bank authorization
# ---------------------------------------------------------------------------

@router.post("/gc-connect", response_class=HTMLResponse)
def gc_connect(
    institution_id: str = Form(...),
    account_name: str = Form(...),
    db: Session = Depends(get_db),
):
    redirect_url = f"{_app_url()}/accounts/gc-callback"
    try:
        requisition = gc.create_requisition(db, institution_id, redirect_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Temporarily store account_name in the requisition reference isn't reliable,
    # so we create a placeholder account to hold state
    color = ACCOUNT_COLORS[len(db.query(Account).all()) % len(ACCOUNT_COLORS)]
    account = Account(
        name=account_name,
        iban=f"PENDING-{requisition['id'][:12]}",
        color=color,
        gc_requisition_id=requisition["id"],
    )
    db.add(account)
    db.commit()

    # Redirect user to bank login page
    return RedirectResponse(requisition["link"], status_code=303)


# ---------------------------------------------------------------------------
# GoCardless: callback after bank authorization
# ---------------------------------------------------------------------------

@router.get("/gc-callback", response_class=HTMLResponse)
def gc_callback(
    request: Request,
    ref: str | None = None,
    db: Session = Depends(get_db),
):
    """
    GoCardless redirects here after the user completes bank authorization.
    The `ref` param contains the requisition reference we set.
    We match it to our pending account and fetch the real IBAN.
    """
    # Find accounts still pending (IBAN starts with PENDING-)
    pending = db.query(Account).filter(Account.iban.like("PENDING-%")).all()

    linked = []
    errors = []

    for account in pending:
        req_id = account.gc_requisition_id
        if not req_id:
            continue
        try:
            requisition = gc.get_requisition(db, req_id)
            gc_account_ids = requisition.get("accounts", [])
            if not gc_account_ids:
                continue

            # Use first account from the requisition
            gc_account_id = gc_account_ids[0]
            details = gc.get_account_details(db, gc_account_id)
            account_detail = details.get("account", details)

            iban = account_detail.get("iban") or account_detail.get("resourceId", gc_account_id)
            owner = account_detail.get("ownerName", "")

            # Check if IBAN already exists (different account)
            existing = db.query(Account).filter(
                Account.iban == iban,
                Account.id != account.id,
            ).first()
            if existing:
                db.delete(account)
                db.commit()
                errors.append(f"IBAN {iban} ist bereits vorhanden.")
                continue

            account.iban = iban
            account.gc_account_id = gc_account_id
            if owner and not account.name:
                account.name = owner
            db.commit()

            # Initial sync
            result = sync_account(account, db)
            linked.append({"account": account, "new_txs": result.get("new", 0)})

        except Exception as e:
            log.exception("gc-callback failed for account %s", account.id)
            errors.append(str(e))

    return templates.TemplateResponse("accounts_gc_callback.html", {
        "request": request,
        "linked": linked,
        "errors": errors,
    })


# ---------------------------------------------------------------------------
# Manual sync
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------

@router.post("/{account_id}/import-csv", response_class=HTMLResponse)
async def import_csv_upload(
    account_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404)

    content = await file.read()
    result = import_csv(content, account, db)

    accounts = db.query(Account).all()
    return templates.TemplateResponse("partials/account_list.html", {
        "request": request,
        "accounts": accounts,
        "import_result": result,
        "imported_id": account_id,
    })


# ---------------------------------------------------------------------------
# Delete account
# ---------------------------------------------------------------------------

@router.post("/{account_id}/delete")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(Account).filter(Account.id == account_id).first()
    if account:
        db.delete(account)
        db.commit()
    return RedirectResponse("/accounts/", status_code=303)
