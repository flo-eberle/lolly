import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.database import engine, Base
from app.routers import dashboard, transactions, accounts, categories, settings
from app.routers.auth_router import router as auth_router
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger(__name__)

# Paths that don't require authentication
_PUBLIC_PATHS = {"/login", "/setup"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        from app.database import SessionLocal
        from app.auth import is_password_set, is_logged_in

        db = SessionLocal()
        try:
            if not is_password_set(db):
                return RedirectResponse("/setup")
        finally:
            db.close()

        if not is_logged_in(request):
            return RedirectResponse(f"/login?next={request.url.path}")

        return await call_next(request)


def _run_migrations():
    import sqlalchemy
    migrations = [
        "ALTER TABLE accounts ADD COLUMN gc_account_id VARCHAR",
        "ALTER TABLE accounts ADD COLUMN gc_requisition_id VARCHAR",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(sqlalchemy.text(sql))
                conn.commit()
                log.info("Migration applied: %s", sql)
            except Exception:
                pass  # Column already exists


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

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "change-me-in-production-please"),
    session_cookie="lolly_session",
    max_age=60 * 60 * 24 * 30,  # 30 days
    https_only=False,
)
app.add_middleware(AuthMiddleware)

app.include_router(auth_router)
app.include_router(dashboard.router)
app.include_router(transactions.router)
app.include_router(accounts.router)
app.include_router(categories.router)
app.include_router(settings.router)
