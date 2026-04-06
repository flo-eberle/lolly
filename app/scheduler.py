"""
Background scheduler: auto-sync all GoCardless-connected accounts every N hours.
"""

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from app.database import SessionLocal
from app.models import Account
from app.services.gocardless_service import sync_account

log = logging.getLogger(__name__)

SYNC_INTERVAL_HOURS = int(os.environ.get("SYNC_INTERVAL_HOURS", "6"))

_scheduler: BackgroundScheduler | None = None


def _run_sync():
    log.info("Scheduled sync starting...")
    db = SessionLocal()
    try:
        accounts = db.query(Account).filter(Account.gc_account_id.isnot(None)).all()
        for account in accounts:
            result = sync_account(account, db)
            log.info("Synced %s: %s new transactions", account.iban, result.get("new"))
    except Exception:
        log.exception("Scheduled sync failed")
    finally:
        db.close()


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _run_sync,
        trigger="interval",
        hours=SYNC_INTERVAL_HOURS,
        id="auto_sync",
        replace_existing=True,
    )
    _scheduler.start()
    log.info("Scheduler started (interval: %dh)", SYNC_INTERVAL_HOURS)


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
