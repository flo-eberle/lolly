"""
FinTS/HBCI service for fetching transactions from easybank.

easybank (BAWAG Group) FinTS endpoint:
  https://hbci.easybank.at/Account.asmx
Bank code (Bankleitzahl): 14200 (easybank)
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Account, FintsCredential, Transaction
from app.crypto import decrypt_pin

log = logging.getLogger(__name__)

EASYBANK_FINTS_URL = "https://hbci.easybank.at/Account.asmx"
EASYBANK_BANK_CODE = "14200"


def _get_fints_client(cred: FintsCredential, product_id: Optional[str] = None):
    """Create a python-fints client from stored credentials."""
    try:
        from fints.client import FinTS3PinTanClient
    except ImportError:
        raise RuntimeError("python-fints is not installed")

    pin = decrypt_pin(cred.pin_encrypted)
    client = FinTS3PinTanClient(
        bank_identifier=cred.bank_code,
        user_id=cred.login,
        pin=pin,
        server=cred.fints_url,
        product_id=product_id,
    )
    return client


def test_connection(cred: FintsCredential) -> dict:
    """
    Test FinTS connection and return available accounts.
    Returns {"ok": True, "accounts": [...]} or {"ok": False, "error": "..."}.
    """
    try:
        client = _get_fints_client(cred)
        with client:
            sepa_accounts = client.get_sepa_accounts()
        return {
            "ok": True,
            "accounts": [
                {"iban": a.iban, "account_number": a.accountnumber}
                for a in sepa_accounts
            ],
        }
    except Exception as e:
        log.exception("FinTS connection test failed")
        return {"ok": False, "error": str(e)}


def sync_account(account: Account, db: Session) -> dict:
    """
    Fetch new transactions for an account and store them.
    Returns {"new": N, "error": None} or {"new": 0, "error": "..."}.
    """
    cred = account.credentials
    if not cred:
        return {"new": 0, "error": "No FinTS credentials configured"}

    # Fetch from last sync date or 90 days back
    from_date = (
        account.last_sync.date() if account.last_sync else
        date.today() - timedelta(days=90)
    )
    to_date = date.today()

    try:
        from fints.client import FinTS3PinTanClient
        from fints.models import SEPAAccount

        pin = decrypt_pin(cred.pin_encrypted)
        client = FinTS3PinTanClient(
            bank_identifier=cred.bank_code,
            user_id=cred.login,
            pin=pin,
            server=cred.fints_url,
        )

        with client:
            sepa_accounts = client.get_sepa_accounts()
            target = next(
                (a for a in sepa_accounts if a.iban == account.iban), None
            )
            if target is None:
                return {"new": 0, "error": f"IBAN {account.iban} not found in FinTS response"}

            transactions = client.get_transactions(target, start_date=from_date, end_date=to_date)

        new_count = 0
        for tx in transactions:
            data = tx.data
            tx_date = data.get("date") or data.get("entry_date")
            if not isinstance(tx_date, date):
                tx_date = to_date

            amount_raw = data.get("amount")
            if amount_raw is None:
                continue
            amount = Decimal(str(amount_raw.amount))

            payee = data.get("applicant_name") or ""
            purpose = data.get("purpose") or ""

            tx_hash = Transaction.make_hash(account.iban, tx_date, amount, purpose, payee)

            exists = db.query(Transaction).filter(Transaction.tx_hash == tx_hash).first()
            if exists:
                continue

            new_tx = Transaction(
                account_id=account.id,
                date=tx_date,
                amount=amount,
                payee=payee,
                purpose=purpose,
                tx_hash=tx_hash,
            )
            db.add(new_tx)
            new_count += 1

        # Update balance from last statement
        if transactions:
            last_balance = transactions[-1].data.get("final_balance")
            if last_balance is not None:
                account.balance = Decimal(str(last_balance.amount))

        from datetime import datetime
        account.last_sync = datetime.utcnow()
        db.commit()

        # Run auto-categorization on new transactions
        from app.services.categorizer import categorize_uncategorized
        categorize_uncategorized(db, account_id=account.id)

        # Detect recurring payments
        from app.services.recurring import detect_recurring
        detect_recurring(db, account_id=account.id)

        return {"new": new_count, "error": None}

    except Exception as e:
        db.rollback()
        log.exception("FinTS sync failed for account %s", account.iban)
        return {"new": 0, "error": str(e)}
