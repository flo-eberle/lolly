"""
CSV import service for easybank (and other Austrian banks).

easybank exports CSVs from the online banking portal.
The format uses semicolons and German decimal notation (comma as decimal separator).

Expected columns (easybank):
  Buchungsdatum;Valutadatum;Buchungstext;Betrag;Währung;Auftraggeberkonto;Gegenkonto;...

Other common Austrian bank formats are also handled via auto-detection.
"""

import csv
import io
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import Account, Transaction

log = logging.getLogger(__name__)


def _parse_amount(raw: str) -> Decimal | None:
    """Parse German-formatted number: '−1.234,56' or '-1234.56'."""
    cleaned = (
        raw.strip()
        .replace("\xa0", "")   # non-breaking space
        .replace(" ", "")
        .replace("−", "-")     # minus sign (Unicode) → hyphen-minus
    )
    # German format: 1.234,56 → 1234.56
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_date(raw: str) -> date | None:
    """Parse DD.MM.YYYY or YYYY-MM-DD."""
    raw = raw.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _detect_columns(header: list[str]) -> dict:
    """Map semantic field names to column indices from the header row."""
    h = [c.strip().lower() for c in header]

    def find(*candidates):
        for c in candidates:
            for i, col in enumerate(h):
                if c in col:
                    return i
        return None

    return {
        "date":    find("buchungsdatum", "buchungs", "date", "datum"),
        "amount":  find("betrag", "amount", "umsatz"),
        "payee":   find("buchungstext", "gegenkonto name", "empfänger", "payee", "name"),
        "purpose": find("verwendungszweck", "purpose", "remittance", "text", "buchungstext"),
        "iban":    find("auftraggeberkonto", "iban", "konto"),
    }


def import_csv(content: bytes, account: Account, db: Session) -> dict:
    """
    Parse CSV bytes and import transactions for the given account.
    Returns {"imported": N, "skipped": N, "errors": [...]}.
    """
    # Detect encoding
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return {"imported": 0, "skipped": 0, "errors": ["Datei konnte nicht dekodiert werden."]}

    # Detect delimiter
    sample = text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)

    if not rows:
        return {"imported": 0, "skipped": 0, "errors": ["Leere Datei."]}

    # Skip non-header lines at the top (some banks prepend metadata rows)
    header_idx = 0
    cols = {}
    for i, row in enumerate(rows):
        cols = _detect_columns(row)
        if cols["date"] is not None and cols["amount"] is not None:
            header_idx = i
            break
    else:
        return {
            "imported": 0,
            "skipped": 0,
            "errors": ["Konnte keine Spalten erkennen. Erwartet: Buchungsdatum, Betrag."],
        }

    data_rows = rows[header_idx + 1:]
    imported = skipped = 0
    errors = []

    for row_num, row in enumerate(data_rows, start=header_idx + 2):
        if not any(cell.strip() for cell in row):
            continue  # blank line

        try:
            tx_date = _parse_date(row[cols["date"]]) if cols["date"] is not None else None
            if tx_date is None:
                skipped += 1
                continue

            amount = _parse_amount(row[cols["amount"]]) if cols["amount"] is not None else None
            if amount is None:
                skipped += 1
                continue

            payee = ""
            if cols["payee"] is not None and cols["payee"] < len(row):
                payee = row[cols["payee"]].strip()

            purpose = ""
            if cols["purpose"] is not None and cols["purpose"] < len(row) and cols["purpose"] != cols["payee"]:
                purpose = row[cols["purpose"]].strip()
            elif not purpose and cols["payee"] is not None:
                purpose = payee  # fallback

            tx_hash = Transaction.make_hash(account.iban, tx_date, amount, purpose, payee)
            if db.query(Transaction).filter(Transaction.tx_hash == tx_hash).first():
                skipped += 1
                continue

            db.add(Transaction(
                account_id=account.id,
                date=tx_date,
                amount=amount,
                payee=payee,
                purpose=purpose,
                tx_hash=tx_hash,
            ))
            imported += 1

        except Exception as e:
            errors.append(f"Zeile {row_num}: {e}")
            log.warning("CSV parse error row %d: %s", row_num, e)

    db.commit()

    from app.services.categorizer import categorize_uncategorized
    from app.services.recurring import detect_recurring
    categorize_uncategorized(db, account_id=account.id)
    detect_recurring(db, account_id=account.id)

    return {"imported": imported, "skipped": skipped, "errors": errors[:10]}
