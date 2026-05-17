"""Live session + bandwidth data via Jellyfin's /Sessions REST API."""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import get_settings


_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_TTL_SEC = 2.0


async def _fetch_sessions() -> list[dict[str, Any]] | None:
    s = get_settings()
    if not s.jellyfin_api_key:
        return None
    url = s.jellyfin_url.rstrip("/") + "/Sessions"
    try:
        async with httpx.AsyncClient(timeout=4.0, verify=s.jellyfin_verify_ssl) as client:
            r = await client.get(
                url,
                headers={"X-Emby-Token": s.jellyfin_api_key},
                params={"activeWithinSeconds": 60},
            )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _ticks_to_s(ticks: int | None) -> float:
    return (ticks or 0) / 10_000_000.0


def _bitrate_for(session: dict[str, Any]) -> int:
    """Pick the most representative bitrate for a session, in bits/sec.

    Preference order:
      1. TranscodingInfo.Bitrate (output bitrate when transcoding)
      2. NowPlayingItem.MediaSources[0].Bitrate
      3. PlayState.Bitrate (rare)
      4. Sum of MediaStream bitrates from the playing source
    """
    ti = session.get("TranscodingInfo") or {}
    if ti.get("Bitrate"):
        return int(ti["Bitrate"])

    item = session.get("NowPlayingItem") or {}
    src = (item.get("MediaSources") or [{}])[0]
    if src.get("Bitrate"):
        return int(src["Bitrate"])

    ps = session.get("PlayState") or {}
    if ps.get("Bitrate"):
        return int(ps["Bitrate"])

    streams = src.get("MediaStreams") or item.get("MediaStreams") or []
    total = 0
    for st in streams:
        if st.get("BitRate"):
            total += int(st["BitRate"])
    return total


def _format_play_method(session: dict[str, Any]) -> str:
    ps = session.get("PlayState") or {}
    method = ps.get("PlayMethod") or "Direct"
    ti = session.get("TranscodingInfo") or {}
    if ti:
        reasons = ti.get("TranscodeReasons") or []
        if reasons:
            return f"Transcode ({', '.join(reasons[:2])})"
        return "Transcode"
    return method


async def get_now_playing(filter_user_id: str | None = None) -> dict[str, Any]:
    """Return active playing sessions + aggregate bandwidth.

    If filter_user_id is given (nodash lower), filter to that user only.
    """
    now = time.monotonic()
    if _CACHE["data"] is not None and (now - _CACHE["at"]) < _TTL_SEC:
        raw = _CACHE["data"]
    else:
        raw = await _fetch_sessions()
        _CACHE["at"] = now
        _CACHE["data"] = raw

    if raw is None:
        return {"available": False, "sessions": [], "total_bandwidth_bps": 0, "active_count": 0}

    sessions: list[dict[str, Any]] = []
    total_bps = 0
    active_count = 0

    for s in raw:
        item = s.get("NowPlayingItem")
        if not item:
            continue
        suid = (s.get("UserId") or "").replace("-", "").lower()
        if filter_user_id and suid != filter_user_id:
            continue

        play_state = s.get("PlayState") or {}
        # Only surface ACTIVELY playing sessions — paused ones are noise.
        if bool(play_state.get("IsPaused")):
            continue
        bps = _bitrate_for(s)
        total_bps += bps
        active_count += 1

        pos_s = _ticks_to_s(play_state.get("PositionTicks"))
        total_s = _ticks_to_s(item.get("RunTimeTicks"))
        progress = (pos_s / total_s) if total_s > 0 else 0.0

        # Pick a stable nodash id and a nice display title
        item_id = (item.get("Id") or "").replace("-", "").lower()
        series_id = (item.get("SeriesId") or "").replace("-", "").lower()
        link_id = series_id or item_id

        if item.get("Type") == "Episode":
            display_title = item.get("SeriesName") or item.get("Name") or ""
            sub_title = f"S{item.get('ParentIndexNumber','?')}E{item.get('IndexNumber','?')} · {item.get('Name','')}"
        else:
            display_title = item.get("Name") or ""
            sub_title = item.get("ProductionYear") and str(item.get("ProductionYear")) or ""

        sessions.append({
            "SessionId": s.get("Id"),
            "UserId": suid,
            "UserName": s.get("UserName") or "",
            "ItemId": item_id,
            "LinkItemId": link_id,
            "ItemType": item.get("Type") or "",
            "Title": display_title,
            "Subtitle": sub_title,
            "Client": s.get("Client") or "",
            "DeviceName": s.get("DeviceName") or "",
            "RemoteEndPoint": s.get("RemoteEndPoint") or "",
            "PlayMethod": _format_play_method(s),
            "PositionSeconds": pos_s,
            "DurationSeconds": total_s,
            "Progress": progress,
            "BitrateBps": bps,
        })

    sessions.sort(key=lambda x: x["BitrateBps"], reverse=True)
    return {
        "available": True,
        "sessions": sessions,
        "total_bandwidth_bps": total_bps,
        "active_count": active_count,
    }
