import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from app.database import engine, Base
from app.routers import dashboard, transactions, accounts, categories, settings
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
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
