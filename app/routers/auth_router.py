from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import (
    is_password_set, set_password, verify_password,
    login_session, logout_session, get_app_settings,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db)):
    if is_password_set(db):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("setup.html", {"request": request, "error": None})


@router.post("/setup", response_class=HTMLResponse)
def setup_submit(
    request: Request,
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(get_db),
):
    if is_password_set(db):
        return RedirectResponse("/login", status_code=303)
    if len(password) < 8:
        return templates.TemplateResponse("setup.html", {
            "request": request, "error": "Passwort muss mindestens 8 Zeichen haben."
        })
    if password != password2:
        return templates.TemplateResponse("setup.html", {
            "request": request, "error": "Passwörter stimmen nicht überein."
        })
    set_password(db, password)
    login_session(request)
    return RedirectResponse("/", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if not is_password_set(db):
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    settings = get_app_settings(db)
    if settings and verify_password(password, settings.password_hash):
        login_session(request)
        # Only allow relative redirects
        if not next.startswith("/"):
            next = "/"
        return RedirectResponse(next, status_code=303)
    return templates.TemplateResponse("login.html", {
        "request": request, "next": next, "error": "Falsches Passwort."
    })


@router.get("/logout")
def logout(request: Request):
    logout_session(request)
    return RedirectResponse("/login", status_code=303)


@router.post("/settings/change-password", response_class=HTMLResponse)
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password2: str = Form(...),
    db: Session = Depends(get_db),
):
    settings = get_app_settings(db)
    error = None
    if not settings or not verify_password(current_password, settings.password_hash):
        error = "Aktuelles Passwort falsch."
    elif len(new_password) < 8:
        error = "Neues Passwort muss mindestens 8 Zeichen haben."
    elif new_password != new_password2:
        error = "Neue Passwörter stimmen nicht überein."
    else:
        set_password(db, new_password)

    from app.services.gocardless_service import get_settings as get_gc_settings
    gc_settings = get_gc_settings(db)
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "gc_settings": gc_settings,
        "pw_error": error,
        "pw_saved": error is None,
    })
