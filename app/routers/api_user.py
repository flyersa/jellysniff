from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ..services.auth import SessionUser, require_user
from ..services import queries as q
from ..services import recommender as rec
from ..services import sessions as live

router = APIRouter(prefix="/api/me")


def _anonymize_picks(picks: list[dict[str, Any]], is_admin: bool) -> list[dict[str, Any]]:
    """For non-admin users, collapse per-peer reasons into an anonymous
    "N other users watched" count so account names aren't leaked across users.
    Admin requests are passed through untouched."""
    if is_admin:
        return picks
    for p in picks:
        reasons = p.get("Reasons", []) or []
        peer_count = sum(1 for r in reasons if r.get("kind") == "peer")
        non_peer = [r for r in reasons if r.get("kind") != "peer"]
        if peer_count > 0:
            label = (
                f"{peer_count} other user watched"
                if peer_count == 1
                else f"{peer_count} other users watched"
            )
            non_peer.append({"kind": "peer-count", "label": label})
        p["Reasons"] = non_peer
    return picks


@router.get("/summary")
async def summary(
    days: int = Query(30, ge=1, le=3650),
    user: SessionUser = Depends(require_user),
):
    history = await q.user_history(user.user_id, limit=30, since_days=days)
    devices = await q.device_breakdown(since_days=days, user_id=user.user_id)
    top_i = await q.top_items(limit=12, since_days=days, user_id=user.user_id)
    daily = await q.daily_activity(since_days=days, user_id=user.user_id)
    monthly = await q.monthly_activity(user_id=user.user_id, months=18)
    heatmap = await q.hourly_heatmap(since_days=days, user_id=user.user_id)
    genres = await q.user_genre_profile(user.user_id, top=10)
    favorites = await q.user_favorites(user.user_id, limit=24)
    completion = await q.series_completion(user.user_id, limit=10)
    popular = await q.popular_now(user_id=user.user_id, days=21, limit=12)
    suggestions = await rec.recommend_for_user(user.user_id, limit=18)
    suggestions = _anonymize_picks(suggestions, user.is_admin)
    totals = await q.user_play_totals(user.user_id, since_days=days)
    total_seconds = totals["TotalSeconds"]
    return {
        "history": history,
        "devices": devices,
        "top_items": top_i,
        "daily": daily,
        "monthly": monthly,
        "heatmap": heatmap,
        "genres": genres,
        "favorites": favorites,
        "completion": completion,
        "popular_now": popular,
        "suggestions": suggestions,
        "total_seconds": total_seconds,
        "days": days,
    }


@router.get("/history")
async def history(
    days: int = Query(180, ge=1, le=3650),
    limit: int = Query(50, ge=1, le=500),
    page: int = Query(1, ge=1, le=10000),
    user: SessionUser = Depends(require_user),
):
    offset = (page - 1) * limit
    rows = await q.user_history(user.user_id, limit=limit, since_days=days, offset=offset)
    total = await q.user_history_total(user.user_id, since_days=days)
    pages = max(1, (total + limit - 1) // limit)
    return {
        "history": rows,
        "days": days,
        "page": page,
        "page_size": limit,
        "total": total,
        "pages": pages,
    }


@router.get("/suggestions")
async def suggestions(user: SessionUser = Depends(require_user)):
    picks = await rec.recommend_for_user(user.user_id, limit=30)
    picks = _anonymize_picks(picks, user.is_admin)
    # Non-admins never see the peers list (it would name other accounts).
    if not user.is_admin:
        return {"picks": picks, "peers": []}
    peers_raw = await rec.top_peers(user.user_id, limit=30)
    user_map = {u["Id"].replace("-", "").lower(): u["Username"] for u in await q.list_users()}
    peers = [{"UserId": p["UserId"], "Username": user_map[p["UserId"]],
              "Similarity": p["Similarity"]}
             for p in peers_raw if p["UserId"] in user_map][:8]
    return {"picks": picks, "peers": peers}


@router.get("/now-playing")
async def my_now_playing(user: SessionUser = Depends(require_user)):
    return await live.get_now_playing(filter_user_id=user.user_id.replace("-", "").lower())
