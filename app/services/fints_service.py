"""
FinTS/HBCI service for fetching transactions from easybank.

easybank (BAWAG Group) FinTS endpoint:
  https://hbci.easybank.at/Account.asmx
Bank code (Bankleitzahl): 14200 (easybank)

Uses the `fints` package (v5+), formerly known as `python-fints`.
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

# product_id is required since fints v4.
# Use your own registered ID or this generic test ID for self-hosted use.
FINTS_PRODUCT_ID = "9FA6681DEC0CF3046BFC2F8A6"


def _make_client(cred: FintsCredential):
    from fints.client import FinTS3PinTanClient
    pin = decrypt_pin(cred.pin_encrypted)
    return FinTS3PinTanClient(
        bank_identifier=cred.bank_code,
        user_id=cred.login,
        pin=pin,
        server=cred.fints_url,
        product_id=FINTS_PRODUCT_ID,
    )


def _parse_tx_fields(tx_data: dict) -> tuple[date | None, Decimal | None, str, str]:
    """
    Extract (date, amount, payee, purpose) from a fints transaction data dict.
    Handles both MT940 and camt.052/XML fallback formats returned by fints v5.
    """
    # Date
    tx_date = tx_data.get("date") or tx_data.get("entry_date") or tx_data.get("booking_date")
    if not isinstance(tx_date, date):
        tx_date = None

    # Amount — MT940 returns an Amount namedtuple with .amount (Decimal)
    raw_amount = tx_data.get("amount")
    if raw_amount is None:
        return tx_date, None, "", ""
    if hasattr(raw_amount, "amount"):
        amount = Decimal(str(raw_amount.amount))
    else:
        amount = Decimal(str(raw_amount))

    # Payee
    payee = (
        tx_data.get("applicant_name")
        or tx_data.get("creditor_name")
        or tx_data.get("debtor_name")
        or ""
    )

    # Purpose / remittance info
    purpose = (
        tx_data.get("purpose")
        or tx_data.get("remittance_information")
        or tx_data.get("additional_data")
        or ""
    )

    return tx_date, amount, str(payee).strip(), str(purpose).strip()


def test_connection(cred: FintsCredential) -> dict:
    """
    Test FinTS connection and return available accounts.
    Returns {"ok": True, "accounts": [...]} or {"ok": False, "error": "..."}.
    """
    try:
        client = _make_client(cred)
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

    from_date = (
        account.last_sync.date() if account.last_sync
        else date.today() - timedelta(days=90)
    )
    to_date = date.today()

    try:
        client = _make_client(cred)

        with client:
            sepa_accounts = client.get_sepa_accounts()
            target = next(
                (a for a in sepa_accounts if a.iban == account.iban), None
            )
            if target is None:
                return {"new": 0, "error": f"IBAN {account.iban} not found in FinTS response"}

            transactions = client.get_transactions(target, start_date=from_date, end_date=to_date)

        new_count = 0
        last_balance = None

        for tx in transactions:
            data = tx.data if hasattr(tx, "data") else tx

            tx_date, amount, payee, purpose = _parse_tx_fields(data)

            if amount is None:
                continue
            if tx_date is None:
                tx_date = to_date

            tx_hash = Transaction.make_hash(account.iban, tx_date, amount, purpose, payee)

            if db.query(Transaction).filter(Transaction.tx_hash == tx_hash).first():
                continue

            db.add(Transaction(
                account_id=account.id,
                date=tx_date,
                amount=amount,
                payee=payee,
                purpose=purpose,
                tx_hash=tx_hash,
            ))
            new_count += 1

            # Track final balance from last statement
            raw_balance = data.get("final_balance") or data.get("closing_balance")
            if raw_balance is not None:
                last_balance = raw_balance

        if last_balance is not None:
            bal = last_balance.amount if hasattr(last_balance, "amount") else last_balance
            account.balance = Decimal(str(bal))

        from datetime import datetime
        account.last_sync = datetime.utcnow()
        db.commit()

        from app.services.categorizer import categorize_uncategorized
        from app.services.recurring import detect_recurring
        categorize_uncategorized(db, account_id=account.id)
        detect_recurring(db, account_id=account.id)

        return {"new": new_count, "error": None}

    except Exception as e:
        db.rollback()
        log.exception("FinTS sync failed for account %s", account.iban)
        return {"new": 0, "error": str(e)}
