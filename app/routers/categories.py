from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, CategoryRule, Transaction
from app.services.categorizer import categorize_uncategorized

router = APIRouter(prefix="/categories")
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def list_categories(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).all()
    # Attach transaction counts
    cat_counts = {}
    for cat in categories:
        cat_counts[cat.id] = len(cat.transactions)

    return templates.TemplateResponse("categories.html", {
        "request": request,
        "categories": categories,
        "cat_counts": cat_counts,
    })


@router.post("/add")
def add_category(
    name: str = Form(...),
    color: str = Form("#6366f1"),
    icon: str = Form("💳"),
    db: Session = Depends(get_db),
):
    existing = db.query(Category).filter(Category.name == name).first()
    if not existing:
        cat = Category(name=name, color=color, icon=icon)
        db.add(cat)
        db.commit()
    return RedirectResponse("/categories", status_code=303)


@router.post("/{cat_id}/delete")
def delete_category(cat_id: int, db: Session = Depends(get_db)):
    cat = db.query(Category).filter(Category.id == cat_id).first()
    if cat:
        # Unset category on transactions
        db.query(Transaction).filter(Transaction.category_id == cat_id).update(
            {"category_id": None}
        )
        db.delete(cat)
        db.commit()
    return RedirectResponse("/categories", status_code=303)


@router.post("/{cat_id}/rules/add")
def add_rule(
    cat_id: int,
    pattern: str = Form(...),
    db: Session = Depends(get_db),
):
    cat = db.query(Category).filter(Category.id == cat_id).first()
    if not cat:
        raise HTTPException(status_code=404)
    rule = CategoryRule(category_id=cat_id, pattern=pattern, priority=len(cat.rules))
    db.add(rule)
    db.commit()

    # Apply the new rule immediately
    categorize_uncategorized(db)

    return RedirectResponse("/categories", status_code=303)


@router.post("/{cat_id}/rules/{rule_id}/delete")
def delete_rule(cat_id: int, rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(CategoryRule).filter(CategoryRule.id == rule_id).first()
    if rule:
        db.delete(rule)
        db.commit()
    return RedirectResponse("/categories", status_code=303)


@router.post("/re-categorize", response_class=HTMLResponse)
def re_categorize_all(request: Request, db: Session = Depends(get_db)):
    count = categorize_uncategorized(db)
    categories = db.query(Category).all()
    cat_counts = {cat.id: len(cat.transactions) for cat in categories}
    return templates.TemplateResponse("categories.html", {
        "request": request,
        "categories": categories,
        "cat_counts": cat_counts,
        "recategorized": count,
    })
