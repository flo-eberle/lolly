import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from app.database import engine, Base
from app.routers import dashboard, transactions, accounts, categories, settings
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger(__name__)


def _run_migrations():
    """Add columns introduced in later versions to existing tables."""
    migrations = [
        "ALTER TABLE accounts ADD COLUMN gc_account_id VARCHAR",
        "ALTER TABLE accounts ADD COLUMN gc_requisition_id VARCHAR",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(__import__("sqlalchemy").text(sql))
                conn.commit()
                log.info("Migration applied: %s", sql)
            except Exception:
                pass  # Column already exists — safe to ignore


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _run_migrations()
    log.info("Database tables created / verified")

    from app.database import SessionLocal
    from app.services.categorizer import seed_default_categories
    db = SessionLocal()
    try:
        seed_default_categories(db)
    finally:
        db.close()

    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Lolly", lifespan=lifespan)

app.include_router(dashboard.router)
app.include_router(transactions.router)
app.include_router(accounts.router)
app.include_router(categories.router)
app.include_router(settings.router)
