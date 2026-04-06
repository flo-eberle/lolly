import hashlib
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import (
    Column, Integer, String, Numeric, Date, DateTime,
    ForeignKey, Boolean, Text
)
from sqlalchemy.orm import relationship
from app.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    iban = Column(String, unique=True, nullable=False)
    balance = Column(Numeric(12, 2), default=0)
    last_sync = Column(DateTime, nullable=True)
    color = Column(String, default="#6366f1")  # for UI display

    credentials = relationship("FintsCredential", back_populates="account", uselist=False, cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="account", cascade="all, delete-orphan")


class FintsCredential(Base):
    __tablename__ = "fints_credentials"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), unique=True, nullable=False)
    fints_url = Column(String, nullable=False)
    bank_code = Column(String, nullable=False)
    login = Column(String, nullable=False)
    pin_encrypted = Column(Text, nullable=False)  # Fernet-encrypted

    account = relationship("Account", back_populates="credentials")


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
    pattern = Column(String, nullable=False)  # regex applied to payee + purpose
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
