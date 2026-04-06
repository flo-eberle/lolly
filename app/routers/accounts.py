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
# GoCardless: callback — show account selection
# ---------------------------------------------------------------------------

@router.get("/gc-callback", response_class=HTMLResponse)
def gc_callback(request: Request, db: Session = Depends(get_db)):
    """
    GoCardless redirects here after bank authorization.
    Fetch all accounts from the requisition and let the user pick which to add.
    """
    pending = db.query(Account).filter(Account.iban.like("PENDING-%")).all()

    available_accounts = []
    requisition_id = None
    errors = []

    for account in pending:
        req_id = account.gc_requisition_id
        if not req_id:
            continue
        requisition_id = req_id
        try:
            requisition = gc.get_requisition(db, req_id)
            gc_account_ids = requisition.get("accounts", [])

            if not gc_account_ids:
                errors.append("Die Bank hat noch keine Konten freigegeben. Bitte warte einen Moment und lade die Seite neu.")
                continue

            existing_ibans = {
                a.iban for a in db.query(Account).filter(~Account.iban.like("PENDING-%")).all()
            }

            for gc_id in gc_account_ids:
                try:
                    details = gc.get_account_details(db, gc_id)
                    acct = details.get("account", details)
                    iban = acct.get("iban", "")
                    available_accounts.append({
                        "gc_id": gc_id,
                        "iban": iban,
                        "owner": acct.get("ownerName", ""),
                        "product": acct.get("product", ""),
                        "already_added": iban in existing_ibans,
                        "suggested_name": acct.get("product") or acct.get("ownerName") or "Konto",
                    })
                except Exception as e:
                    log.warning("Could not fetch details for gc account %s: %s", gc_id, e)

        except Exception as e:
            log.exception("gc-callback failed")
            errors.append(str(e))

    return templates.TemplateResponse("accounts_gc_select.html", {
        "request": request,
        "available_accounts": available_accounts,
        "requisition_id": requisition_id,
        "errors": errors,
    })


# ---------------------------------------------------------------------------
# GoCardless: confirm account selection
# ---------------------------------------------------------------------------

@router.post("/gc-confirm", response_class=HTMLResponse)
async def gc_confirm(request: Request, db: Session = Depends(get_db)):
    """
    User submits the account selection form.
    Creates Account records for selected accounts and runs initial sync.
    """
    form = await request.form()
    requisition_id = form.get("requisition_id")
    selected_gc_ids = form.getlist("gc_ids")

    linked = []
    errors = []

    # Clean up all pending placeholder accounts for this requisition
    pending = db.query(Account).filter(Account.iban.like("PENDING-%")).all()
    for p in pending:
        if p.gc_requisition_id == requisition_id:
            db.delete(p)
    db.commit()

    for gc_id in selected_gc_ids:
        name = form.get(f"name_{gc_id}", "").strip() or "Konto"
        try:
            details = gc.get_account_details(db, gc_id)
            acct = details.get("account", details)
            iban = acct.get("iban") or acct.get("resourceId", gc_id)

            existing = db.query(Account).filter(Account.iban == iban).first()
            if existing:
                errors.append(f"IBAN {iban} ist bereits vorhanden.")
                continue

            color = ACCOUNT_COLORS[db.query(Account).count() % len(ACCOUNT_COLORS)]
            account = Account(
                name=name,
                iban=iban,
                color=color,
                gc_account_id=gc_id,
                gc_requisition_id=requisition_id,
            )
            db.add(account)
            db.commit()
            db.refresh(account)

            result = sync_account(account, db)
            linked.append({"account": account, "new_txs": result.get("new", 0)})

        except Exception as e:
            log.exception("gc-confirm failed for gc_id %s", gc_id)
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
