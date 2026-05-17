from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path

import httpx

from ..config import get_settings

_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,40}$")
_TYPE_RE = re.compile(r"^[A-Za-z]{1,32}$")
_CACHE_TTL_SEC = 24 * 3600
_LOCK = asyncio.Lock()


def _safe_id(value: str) -> str | None:
    if not _ID_RE.match(value):
        return None
    return value.replace("-", "").lower()


def _safe_type(value: str) -> str:
    return value if _TYPE_RE.match(value) else "Primary"


def _cache_path(item_id: str, kind: str, max_width: int) -> Path:
    s = get_settings()
    key = hashlib.sha256(f"{item_id}|{kind}|{max_width}".encode()).hexdigest()
    p = s.cache_dir / "img" / key[:2] / f"{key}.bin"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


async def fetch_image(item_id: str, kind: str = "Primary", max_width: int = 400) -> tuple[bytes, str] | None:
    norm = _safe_id(item_id)
    if not norm:
        return None
    kind = _safe_type(kind)
    max_width = max(64, min(int(max_width), 1200))

    cp = _cache_path(norm, kind, max_width)
    meta = cp.with_suffix(".meta")
    if cp.exists() and meta.exists():
        try:
            age = time.time() - cp.stat().st_mtime
            if age < _CACHE_TTL_SEC:
                return cp.read_bytes(), meta.read_text().strip() or "image/jpeg"
        except OSError:
            pass

    s = get_settings()
    base = s.jellyfin_url.rstrip("/")
    url = f"{base}/Items/{norm}/Images/{kind}?maxWidth={max_width}&quality=80"
    headers: dict[str, str] = {}
    # Jellyfin serves item images publicly on the local network; the API key
    # is only needed when the server is configured to require auth on images
    # or when reaching private endpoints. Send it when set, omit when not.
    if s.jellyfin_api_key:
        headers["X-Emby-Token"] = s.jellyfin_api_key
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=s.jellyfin_verify_ssl) as client:
            r = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    body = r.content
    ctype = r.headers.get("content-type", "image/jpeg").split(";")[0].strip() or "image/jpeg"
    async with _LOCK:
        try:
            cp.write_bytes(body)
            meta.write_text(ctype)
        except OSError:
            pass
    return body, ctype
