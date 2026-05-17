from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..services.auth import SessionUser, require_user
from ..services import queries as q

router = APIRouter(prefix="/api/item")


@router.get("/{item_id}")
async def item_detail(item_id: str, user: SessionUser = Depends(require_user)):
    item = await q.item_detail_movie_or_series(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    item_type = (item.get("Type") or "")
    # If an episode is opened, resolve to its series for the panels
    series_id = (item.get("SeriesId") or "").replace("-", "").lower() or None
    landing_id = item["NormId"]
    if "Episode" in item_type and series_id:
        landing_id = series_id
        series_item = await q.item_detail_movie_or_series(series_id)
        if series_item:
            item = {**series_item, "_jumpedFromEpisode": True, "_episode": item}

    # Permissions: non-admin users only see their own viewership data, but can
    # see the aggregate counts. We always reveal the aggregate; the per-user
    # rows are filtered for non-admin.
    viewers = await q.item_viewers(landing_id, since_days=3650, limit=200)
    if not user.is_admin:
        my_nodash = user.user_id.replace("-", "").lower()
        viewers = [v for v in viewers if v["UserId"].lower() == my_nodash]

    episodes: list = []
    if "Series" in item_type or item.get("_jumpedFromEpisode"):
        episodes = await q.series_episode_plays(landing_id, since_days=3650)

    return {
        "item": item,
        "viewers": viewers,
        "episodes": episodes,
        "is_admin": user.is_admin,
    }
