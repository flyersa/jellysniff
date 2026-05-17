from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..services.auth import SessionUser, require_admin
from ..services import queries as q
from ..services import recommender as rec
from ..services import sessions as live

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@router.get("/overview")
async def overview(days: int = Query(30, ge=1, le=3650)):
    library = await q.library_overview()
    top_i = await q.top_items(limit=12, since_days=days)
    # Pull more than 10 and drop orphans (deleted users whose playback rows
    # still linger in playback_reporting.db) so the leaderboard stays current.
    top_u_raw = await q.top_users(limit=50, since_days=days)
    users = await q.list_users()
    known = {u["Id"].replace("-", "").lower() for u in users}
    top_u = [r for r in top_u_raw if r["UserId"].lower() in known][:10]
    devices = await q.device_breakdown(since_days=days)
    daily = await q.daily_activity(since_days=days)
    heatmap = await q.hourly_heatmap(since_days=days)
    return {
        "library": library,
        "top_items": top_i,
        "top_users": top_u,
        "devices": devices,
        "daily": daily,
        "heatmap": heatmap,
        "user_count": len(users),
    }


@router.get("/users")
async def users(days: int = Query(30, ge=1, le=3650)):
    users = await q.list_users()
    top = {u["UserId"]: u for u in await q.top_users(limit=500, since_days=days)}
    out = []
    for u in users:
        nodash = u["Id"].replace("-", "").lower()
        stats = top.get(nodash, {})
        out.append({
            **u,
            "Plays": stats.get("Plays", 0),
            "TotalSeconds": stats.get("TotalSeconds", 0),
            "DistinctItems": stats.get("DistinctItems", 0),
        })
    out.sort(key=lambda r: (-(r.get("TotalSeconds") or 0), (r["Username"] or "").lower()))
    return {"users": out, "days": days}


@router.get("/users/{target_id}")
async def user_detail(target_id: str, days: int = Query(90, ge=1, le=3650)):
    profile = await q.get_user(target_id)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found")
    totals = await q.user_play_totals(target_id, since_days=days)
    favorites = await q.user_favorites(target_id, limit=60)
    devices = await q.device_breakdown(since_days=days, user_id=target_id)
    daily = await q.daily_activity(since_days=days, user_id=target_id)
    heatmap = await q.hourly_heatmap(since_days=days, user_id=target_id)
    monthly = await q.monthly_activity(user_id=target_id, months=18)
    top_i = await q.top_items(limit=20, since_days=days, user_id=target_id)
    genres = await q.user_genre_profile(target_id, top=15)
    completion = await q.series_completion(target_id, limit=10)

    suggestions = await rec.recommend_for_user(target_id, limit=24)
    peers_raw = await rec.top_peers(target_id, limit=30)
    user_map = {u["Id"].replace("-", "").lower(): u["Username"] for u in await q.list_users()}
    peers = [{"UserId": p["UserId"], "Username": user_map[p["UserId"]],
              "Similarity": p["Similarity"]}
             for p in peers_raw if p["UserId"] in user_map][:10]

    return {
        "profile": profile,
        "totals": totals,
        "favorites": favorites,
        "devices": devices,
        "daily": daily,
        "heatmap": heatmap,
        "monthly": monthly,
        "top_items": top_i,
        "genres": genres,
        "completion": completion,
        "suggestions": suggestions,
        "peers": peers,
        "days": days,
    }


@router.get("/users/{target_id}/history")
async def user_history(
    target_id: str,
    days: int = Query(3650, ge=1, le=3650),
    limit: int = Query(50, ge=1, le=500),
    page: int = Query(1, ge=1, le=10000),
):
    offset = (page - 1) * limit
    rows = await q.user_history(target_id, limit=limit, since_days=days, offset=offset)
    total = await q.user_history_total(target_id, since_days=days)
    pages = max(1, (total + limit - 1) // limit)
    return {
        "history": rows,
        "page": page,
        "page_size": limit,
        "total": total,
        "pages": pages,
        "days": days,
    }


@router.get("/now-playing")
async def now_playing():
    return await live.get_now_playing()


@router.get("/recent")
async def recent_watched(
    days: int | None = Query(None, ge=1, le=3650),
    limit: int = Query(15, ge=1, le=200),
    page: int = Query(1, ge=1, le=10000),
):
    """Paginated server-wide recent playback feed for the admin overview."""
    offset = (page - 1) * limit
    rows = await q.global_history(limit=limit, since_days=days, offset=offset)
    total = await q.global_history_total(since_days=days)
    pages = max(1, (total + limit - 1) // limit)
    users = await q.list_users()
    user_map = {u["Id"].replace("-", "").lower(): u for u in users}
    for r in rows:
        uid = (r.get("UserId") or "").lower()
        u = user_map.get(uid)
        r["Username"] = u["Username"] if u else None
        r["UserExists"] = u is not None
    return {
        "history": rows,
        "page": page,
        "page_size": limit,
        "total": total,
        "pages": pages,
        "days": days,
    }
