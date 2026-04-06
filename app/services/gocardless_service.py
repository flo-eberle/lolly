"""
GoCardless Bank Account Data API (formerly Nordigen).
PSD2-based transaction fetching — works with easybank/BAWAG Austria.

Free tier: unlimited requisitions, 90 days transaction history per connection.
Registration: https://bankaccountdata.gocardless.com/

API docs: https://developer.gocardless.com/bank-account-data/overview
"""

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.models import Account, GoCardlessSettings, Transaction
from app.crypto import encrypt_pin, decrypt_pin

log = logging.getLogger(__name__)

BASE_URL = "https://bankaccountdata.gocardless.com/api/v2"


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def get_settings(db: Session) -> GoCardlessSettings | None:
    return db.query(GoCardlessSettings).first()


def save_settings(db: Session, secret_id: str, secret_key: str) -> GoCardlessSettings:
    settings = db.query(GoCardlessSettings).first()
    if settings:
        settings.secret_id = secret_id
        settings.secret_key_encrypted = encrypt_pin(secret_key)
        # Invalidate tokens on key change
        settings.access_token = None
        settings.refresh_token = None
    else:
        settings = GoCardlessSettings(
            secret_id=secret_id,
            secret_key_encrypted=encrypt_pin(secret_key),
        )
        db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def _ensure_token(db: Session) -> str:
    """Return a valid access token, refreshing or re-obtaining as needed."""
    settings = get_settings(db)
    if not settings:
        raise RuntimeError("GoCardless API keys not configured. Go to Settings.")

    now = datetime.now(timezone.utc)

    # Access token still valid?
    if (
        settings.access_token
        and settings.access_token_expires_at
        and settings.access_token_expires_at.replace(tzinfo=timezone.utc) > now + timedelta(minutes=5)
    ):
        return settings.access_token

    # Try refresh token
    if (
        settings.refresh_token
        and settings.refresh_token_expires_at
        and settings.refresh_token_expires_at.replace(tzinfo=timezone.utc) > now + timedelta(minutes=5)
    ):
        data = _api_post(
            "/token/refresh/",
            json={"refresh": settings.refresh_token},
        )
        _store_access_token(db, settings, data)
        return settings.access_token

    # Full re-auth with secret_id + secret_key
    secret_key = decrypt_pin(settings.secret_key_encrypted)
    data = _api_post(
        "/token/new/",
        json={"secret_id": settings.secret_id, "secret_key": secret_key},
    )
    _store_both_tokens(db, settings, data)
    return settings.access_token


def _store_access_token(db: Session, settings: GoCardlessSettings, data: dict) -> None:
    now = datetime.now(timezone.utc)
    settings.access_token = data["access"]
    settings.access_token_expires_at = now + timedelta(seconds=data.get("access_expires", 86400))
    db.commit()


def _store_both_tokens(db: Session, settings: GoCardlessSettings, data: dict) -> None:
    now = datetime.now(timezone.utc)
    settings.access_token = data["access"]
    settings.access_token_expires_at = now + timedelta(seconds=data.get("access_expires", 86400))
    settings.refresh_token = data["refresh"]
    settings.refresh_token_expires_at = now + timedelta(seconds=data.get("refresh_expires", 2592000))
    db.commit()


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------

def _api_post(path: str, *, json: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{BASE_URL}{path}", json=json, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _api_get(path: str, token: str, *, params: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{BASE_URL}{path}", headers=headers, params=params or {})
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_institutions(db: Session, country: str = "AT") -> list[dict]:
    """Return all supported banks for a country."""
    token = _ensure_token(db)
    return _api_get("/institutions/", token, params={"country": country})


def create_requisition(db: Session, institution_id: str, redirect_url: str) -> dict:
    """
    Start the bank authorization flow. Returns a dict with:
      - id: requisition ID (store this)
      - link: URL to redirect the user to for bank consent
    """
    token = _ensure_token(db)
    data = _api_post(
        "/requisitions/",
        token=token,
        json={
            "redirect": redirect_url,
            "institution_id": institution_id,
            "reference": f"lolly-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "user_language": "DE",
        },
    )
    return data


def get_requisition(db: Session, requisition_id: str) -> dict:
    """Get requisition status and linked account IDs."""
    token = _ensure_token(db)
    return _api_get(f"/requisitions/{requisition_id}/", token)


def get_account_details(db: Session, gc_account_id: str) -> dict:
    """Return account metadata (IBAN, owner name, currency)."""
    token = _ensure_token(db)
    return _api_get(f"/accounts/{gc_account_id}/details/", token)


def get_account_balances(db: Session, gc_account_id: str) -> list[dict]:
    """Return current balances."""
    token = _ensure_token(db)
    data = _api_get(f"/accounts/{gc_account_id}/balances/", token)
    return data.get("balances", [])


def sync_account(account: Account, db: Session) -> dict:
    """
    Fetch new transactions from GoCardless and store them.
    Returns {"new": N, "error": None} or {"new": 0, "error": "..."}.
    """
    if not account.gc_account_id:
        return {"new": 0, "error": "Konto hat keine GoCardless-Verbindung"}

    from_date = (
        account.last_sync.date() if account.last_sync
        else date.today() - timedelta(days=90)
    )

    try:
        token = _ensure_token(db)

        tx_data = _api_get(
            f"/accounts/{account.gc_account_id}/transactions/",
            token,
            params={"date_from": from_date.isoformat(), "date_to": date.today().isoformat()},
        )
        booked = tx_data.get("transactions", {}).get("booked", [])

        # Update balance
        balances = _api_get(f"/accounts/{account.gc_account_id}/balances/", token)
        for bal in balances.get("balances", []):
            if bal.get("balanceType") in ("closingBooked", "interimAvailable", "expected"):
                account.balance = Decimal(str(bal["balanceAmount"]["amount"]))
                break

        new_count = 0
        for tx in booked:
            tx_date = _parse_date(tx.get("bookingDate") or tx.get("valueDate"))
            if not tx_date:
                continue

            raw_amount = tx.get("transactionAmount", {})
            try:
                amount = Decimal(str(raw_amount.get("amount", "0")))
            except Exception:
                continue

            payee = (
                tx.get("creditorName")
                or tx.get("debtorName")
                or tx.get("remittanceInformationUnstructured", "")[:60]
            )
            purpose = (
                tx.get("remittanceInformationUnstructured")
                or tx.get("remittanceInformationStructured")
                or tx.get("additionalInformation")
                or ""
            )

            tx_hash = Transaction.make_hash(account.iban, tx_date, amount, purpose, payee)
            if db.query(Transaction).filter(Transaction.tx_hash == tx_hash).first():
                continue

            db.add(Transaction(
                account_id=account.id,
                date=tx_date,
                amount=amount,
                payee=str(payee).strip(),
                purpose=str(purpose).strip(),
                tx_hash=tx_hash,
            ))
            new_count += 1

        account.last_sync = datetime.utcnow()
        db.commit()

        from app.services.categorizer import categorize_uncategorized
        from app.services.recurring import detect_recurring
        categorize_uncategorized(db, account_id=account.id)
        detect_recurring(db, account_id=account.id)

        return {"new": new_count, "error": None}

    except Exception as e:
        db.rollback()
        log.exception("GoCardless sync failed for account %s", account.iban)
        return {"new": 0, "error": str(e)}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
