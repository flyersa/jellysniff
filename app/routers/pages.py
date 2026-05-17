from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..services.auth import SessionUser, optional_user, require_admin, require_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.autoescape = True


def _ctx(request: Request, user: SessionUser | None, **extra) -> dict:
    ctx = {"request": request, "user": user}
    ctx.update(extra)
    return ctx


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user: SessionUser | None = Depends(optional_user)):
    if user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", _ctx(request, None))


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, user: SessionUser | None = Depends(optional_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/admin/overview" if user.is_admin else "/me", status_code=303)


@router.get("/me", response_class=HTMLResponse)
async def my_dashboard(request: Request, user: SessionUser = Depends(require_user)):
    return templates.TemplateResponse(request, "user_dashboard.html", _ctx(request, user))


@router.get("/me/suggestions", response_class=HTMLResponse)
async def my_suggestions(request: Request, user: SessionUser = Depends(require_user)):
    return templates.TemplateResponse(request, "user_suggestions.html", _ctx(request, user))


@router.get("/me/history", response_class=HTMLResponse)
async def my_history(request: Request, user: SessionUser = Depends(require_user)):
    return templates.TemplateResponse(request, "user_history.html", _ctx(request, user))


# Admin
@router.get("/admin/overview", response_class=HTMLResponse)
async def admin_overview(request: Request, user: SessionUser = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin_overview.html", _ctx(request, user))


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, user: SessionUser = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin_users.html", _ctx(request, user))


@router.get("/admin/users/{target_id}", response_class=HTMLResponse)
async def admin_user_detail(target_id: str, request: Request, user: SessionUser = Depends(require_admin)):
    return templates.TemplateResponse(
        request, "admin_user_detail.html", _ctx(request, user, target_id=target_id)
    )


@router.get("/item/{item_id}", response_class=HTMLResponse)
async def item_detail_page(item_id: str, request: Request, user: SessionUser = Depends(require_user)):
    return templates.TemplateResponse(
        request, "item_detail.html", _ctx(request, user, item_id=item_id)
    )
