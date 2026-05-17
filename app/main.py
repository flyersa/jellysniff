from __future__ import annotations

import logging
import secrets
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .routers import api_admin, api_items, api_user, auth, images, pages


def create_app() -> FastAPI:
    s = get_settings()
    s.cache_dir.mkdir(parents=True, exist_ok=True)
    (s.cache_dir / "img").mkdir(parents=True, exist_ok=True)

    app = FastAPI(title=s.brand_name, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    app.include_router(auth.router)
    app.include_router(pages.router)
    app.include_router(images.router)
    app.include_router(api_admin.router)
    app.include_router(api_user.router)
    app.include_router(api_items.router)

    templates = Jinja2Templates(directory="app/templates")
    templates.env.autoescape = True
    app.state.templates = templates

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if s.enable_hsts and s.secure_cookie:
            resp.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains",
            )
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data: blob:; "
            f"script-src 'self' 'nonce-{nonce}' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        return resp

    @app.exception_handler(StarletteHTTPException)
    async def on_http_exception(request: Request, exc: StarletteHTTPException):
        wants_html = "text/html" in (request.headers.get("accept") or "")
        if exc.status_code == 401 and wants_html:
            return RedirectResponse(url="/login", status_code=303)
        if exc.status_code == 403 and wants_html:
            return RedirectResponse(url="/", status_code=303)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return app


app = create_app()
