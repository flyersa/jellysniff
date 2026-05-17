from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from ..services.auth import SessionUser, require_user
from ..services.images import fetch_image

router = APIRouter()


@router.get("/img/{item_id}")
async def get_image(
    item_id: str,
    type: str = "Primary",
    w: int = 400,
    _user: SessionUser = Depends(require_user),
):
    result = await fetch_image(item_id, type, w)
    if not result:
        raise HTTPException(status_code=404, detail="Image not available")
    body, ctype = result
    return Response(
        content=body,
        media_type=ctype,
        headers={"Cache-Control": "private, max-age=86400"},
    )
