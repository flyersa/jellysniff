from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer

from ..config import Settings, get_settings
from ..services.auth import (
    SessionUser,
    get_serializer,
    issue_session_cookie,
    jellyfin_authenticate,
    login_attempt_allowed,
    optional_user,
    require_user,
)

router = APIRouter()


def _client_ip(request: Request, settings: Settings) -> str:
    """Trust X-Forwarded-For only when the immediate peer is a configured proxy.
    Otherwise an attacker could spoof XFF to defeat the login throttle."""
    peer = request.client.host if request.client else ""
    if peer and peer in settings.trusted_proxy_set:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer or "unknown"


@router.post("/api/auth/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    settings: Settings = Depends(get_settings),
    serializer: URLSafeTimedSerializer = Depends(get_serializer),
):
    ip = _client_ip(request, settings)
    if not login_attempt_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts; cool off for a minute.")

    data = await jellyfin_authenticate(username, password)
    if not data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user = data.get("User") or {}
    policy = user.get("Policy") or {}
    user_id = user.get("Id") or ""
    if not user_id:
        raise HTTPException(status_code=502, detail="Jellyfin returned no user id")
    payload = {
        "user_id": user_id,
        "username": user.get("Name") or username,
        "is_admin": bool(policy.get("IsAdministrator")),
        "jf_token": data.get("AccessToken") or "",
    }
    token = issue_session_cookie(serializer, payload)

    accepts_html = "text/html" in (request.headers.get("accept") or "")
    if accepts_html:
        resp: Response = RedirectResponse(url="/", status_code=303)
    else:
        resp = Response(status_code=204)
    resp.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="lax",
        path="/",
    )
    return resp


@router.post("/api/auth/logout")
async def logout(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    accepts_html = "text/html" in (request.headers.get("accept") or "")
    resp: Response = (
        RedirectResponse(url="/login", status_code=303)
        if accepts_html
        else Response(status_code=204)
    )
    resp.delete_cookie(settings.session_cookie_name, path="/")
    return resp


@router.get("/api/me")
async def me(user: SessionUser = Depends(require_user)):
    return {
        "user_id": user.user_id,
        "username": user.username,
        "is_admin": user.is_admin,
    }
