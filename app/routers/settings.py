from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import gocardless_service as gc

router = APIRouter(prefix="/settings")
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    gc_settings = gc.get_settings(db)
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "gc_settings": gc_settings,
    })


@router.post("/gocardless", response_class=HTMLResponse)
def save_gc_settings(
    request: Request,
    secret_id: str = Form(...),
    secret_key: str = Form(...),
    db: Session = Depends(get_db),
):
    gc.save_settings(db, secret_id, secret_key)
    return RedirectResponse("/settings/?saved=1", status_code=303)
