import hashlib
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import (
    Column, Integer, String, Numeric, Date, DateTime,
    ForeignKey, Boolean, Text
)
from sqlalchemy.orm import relationship
from app.database import Base


class GoCardlessSettings(Base):
    """Single-row table for GoCardless API credentials and tokens."""
    __tablename__ = "gocardless_settings"

    id = Column(Integer, primary_key=True, default=1)
    secret_id = Column(String, nullable=False)
    secret_key_encrypted = Column(Text, nullable=False)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    access_token_expires_at = Column(DateTime, nullable=True)
    refresh_token_expires_at = Column(DateTime, nullable=True)


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    iban = Column(String, unique=True, nullable=False)
    balance = Column(Numeric(12, 2), default=0)
    last_sync = Column(DateTime, nullable=True)
    color = Column(String, default="#6366f1")

    # GoCardless account ID (set after user completes bank authorization)
    gc_account_id = Column(String, nullable=True)
    gc_requisition_id = Column(String, nullable=True)

    transactions = relationship("Transaction", back_populates="account", cascade="all, delete-orphan")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    color = Column(String, default="#6366f1")
    icon = Column(String, default="💳")

    rules = relationship("CategoryRule", back_populates="category", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="category")


class CategoryRule(Base):
    __tablename__ = "category_rules"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    pattern = Column(String, nullable=False)
    priority = Column(Integer, default=0)

    category = relationship("Category", back_populates="rules")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)

    date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    payee = Column(String, default="")
    purpose = Column(Text, default="")
    tx_hash = Column(String, unique=True, nullable=False, index=True)
    is_recurring = Column(Boolean, default=False)
    note = Column(Text, default="")

    account = relationship("Account", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")

    @staticmethod
    def make_hash(iban: str, tx_date: date, amount: Decimal, purpose: str, payee: str) -> str:
        raw = f"{iban}|{tx_date}|{amount}|{purpose[:80]}|{payee[:60]}"
        return hashlib.sha256(raw.encode()).hexdigest()
