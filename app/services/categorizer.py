"""
Auto-categorization service.
Applies regex rules (CategoryRule) to transaction payee + purpose fields.
"""

import re
import logging
from sqlalchemy.orm import Session

from app.models import Category, CategoryRule, Transaction

log = logging.getLogger(__name__)

DEFAULT_CATEGORIES = [
    {
        "name": "Lebensmittel",
        "color": "#22c55e",
        "icon": "🛒",
        "patterns": [
            r"BILLA", r"SPAR", r"REWE", r"HOFER", r"LIDL", r"ALDI",
            r"MERKUR", r"PENNY", r"INTERSPAR", r"DENN'?S", r"BIO MARKT",
        ],
    },
    {
        "name": "Restaurant & Café",
        "color": "#f97316",
        "icon": "🍽️",
        "patterns": [
            r"RESTAURANT", r"GASTHAUS", r"CAFE", r"KAFFEE", r"PIZZA",
            r"BURGER", r"KEBAB", r"MCDONALD", r"STARBUCKS", r"SUBWAY",
            r"VAPIANO", r"LIEFERANDO", r"MJAM", r"FOODORA",
        ],
    },
    {
        "name": "Transport",
        "color": "#3b82f6",
        "icon": "🚗",
        "patterns": [
            r"WIENER LINIEN", r"ÖBB", r"OEBB", r"WESTBAHN", r"FLIXBUS",
            r"UBER", r"BOLT", r"TANKSTELLE", r"SHELL", r"OMV", r"ARAL",
            r"BP ", r"AUTOPARK", r"PARKHAUS",
        ],
    },
    {
        "name": "Wohnen",
        "color": "#8b5cf6",
        "icon": "🏠",
        "patterns": [
            r"MIETE", r"BETRIEBSKOSTEN", r"STROM", r"GAS", r"WASSER",
            r"INTERNET", r"A1 TELEKOM", r"MAGENTA", r"T-MOBILE",
            r"DREI ", r"HAUSVERSICHERUNG",
        ],
    },
    {
        "name": "Gesundheit",
        "color": "#ec4899",
        "icon": "💊",
        "patterns": [
            r"APOTHEKE", r"ARZT", r"KRANKENHAUS", r"ORDINATION",
            r"ZAHNARZT", r"OPTIKER", r"FIELMANN", r"GYMPASS", r"FITNESSCENTER",
        ],
    },
    {
        "name": "Shopping",
        "color": "#f59e0b",
        "icon": "🛍️",
        "patterns": [
            r"AMAZON", r"ZALANDO", r"H&M", r"ZARA", r"IKEA",
            r"MEDIAMARKT", r"SATURN", r"THALIA", r"DOUGLAS",
        ],
    },
    {
        "name": "Unterhaltung",
        "color": "#06b6d4",
        "icon": "🎬",
        "patterns": [
            r"NETFLIX", r"SPOTIFY", r"AMAZON PRIME", r"DISNEY",
            r"APPLE\.COM", r"GOOGLE PLAY", r"STEAM", r"KINO",
            r"THEATER", r"MUSEUM",
        ],
    },
    {
        "name": "Einkommen",
        "color": "#10b981",
        "icon": "💰",
        "patterns": [
            r"GEHALT", r"LOHN", r"HONORAR", r"GUTSCHRIFT AUS",
        ],
    },
]


def seed_default_categories(db: Session) -> None:
    """Insert default categories + rules if not yet present."""
    for cat_def in DEFAULT_CATEGORIES:
        existing = db.query(Category).filter(Category.name == cat_def["name"]).first()
        if existing:
            continue
        cat = Category(name=cat_def["name"], color=cat_def["color"], icon=cat_def["icon"])
        db.add(cat)
        db.flush()
        for i, pattern in enumerate(cat_def["patterns"]):
            rule = CategoryRule(category_id=cat.id, pattern=pattern, priority=i)
            db.add(rule)
    db.commit()


def categorize_uncategorized(db: Session, account_id: int | None = None) -> int:
    """
    Apply category rules to all uncategorized transactions.
    Returns number of newly categorized transactions.
    """
    rules: list[CategoryRule] = (
        db.query(CategoryRule)
        .order_by(CategoryRule.priority)
        .all()
    )

    query = db.query(Transaction).filter(Transaction.category_id.is_(None))
    if account_id is not None:
        query = query.filter(Transaction.account_id == account_id)

    transactions = query.all()
    count = 0

    for tx in transactions:
        search_text = f"{tx.payee} {tx.purpose}".upper()
        for rule in rules:
            try:
                if re.search(rule.pattern, search_text, re.IGNORECASE):
                    tx.category_id = rule.category_id
                    count += 1
                    break
            except re.error:
                log.warning("Invalid regex pattern: %s", rule.pattern)

    db.commit()
    return count
