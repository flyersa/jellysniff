from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Cookie, Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import Settings, get_settings


@dataclass
class SessionUser:
    user_id: str
    username: str
    is_admin: bool
    jf_token: str


def _serializer(s: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(s.session_secret, salt="jellysniff-session-v1")


def _device_id() -> str:
    return secrets.token_hex(16)


async def jellyfin_authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Forward credentials to Jellyfin /Users/AuthenticateByName.
    Returns Jellyfin's response on success, None on auth failure."""
    s = get_settings()
    auth_header = (
        f'MediaBrowser Client="{s.brand_name}", '
        f'Device="{s.brand_name} Web", '
        f'DeviceId="{_device_id()}", '
        f'Version="1.0.0"'
    )
    url = s.jellyfin_url.rstrip("/") + "/Users/AuthenticateByName"
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            verify=s.jellyfin_verify_ssl,
        ) as client:
            resp = await client.post(
                url,
                json={"Username": username, "Pw": password},
                headers={
                    "X-Emby-Authorization": auth_header,
                    "Content-Type": "application/json",
                },
            )
    except httpx.HTTPError:
        return None
    if resp.status_code == 401:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def issue_session_cookie(serializer: URLSafeTimedSerializer, payload: dict[str, Any]) -> str:
    return serializer.dumps(payload)


def read_session_cookie(serializer: URLSafeTimedSerializer, raw: str, max_age: int) -> dict[str, Any] | None:
    try:
        return serializer.loads(raw, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Login throttle (per IP, in-memory, bounded)
# ─────────────────────────────────────────────────────────────────────────────

_BUCKETS: dict[str, list[float]] = {}
_MAX_ATTEMPTS = 8
_WINDOW_SEC = 60.0


def login_attempt_allowed(ip: str) -> bool:
    now = time.monotonic()
    bucket = [t for t in _BUCKETS.get(ip, []) if now - t < _WINDOW_SEC]
    if len(bucket) >= _MAX_ATTEMPTS:
        _BUCKETS[ip] = bucket
        return False
    bucket.append(now)
    _BUCKETS[ip] = bucket
    if len(_BUCKETS) > 4096:  # don't let it grow unbounded
        for k in list(_BUCKETS)[:1024]:
            _BUCKETS.pop(k, None)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Dependencies
# ─────────────────────────────────────────────────────────────────────────────

def _current_user_or_none(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> SessionUser | None:
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    payload = read_session_cookie(_serializer(settings), raw, settings.session_max_age)
    if not payload:
        return None
    try:
        return SessionUser(
            user_id=payload["user_id"],
            username=payload["username"],
            is_admin=bool(payload.get("is_admin")),
            jf_token=payload.get("jf_token", ""),
        )
    except KeyError:
        return None


def optional_user(
    user: SessionUser | None = Depends(_current_user_or_none),
) -> SessionUser | None:
    return user


def require_user(
    user: SessionUser | None = Depends(_current_user_or_none),
) -> SessionUser:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_admin(
    user: SessionUser = Depends(require_user),
) -> SessionUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


def get_serializer(settings: Settings = Depends(get_settings)) -> URLSafeTimedSerializer:
    return _serializer(settings)
