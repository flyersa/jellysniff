from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from ..config import get_settings


def _ro_uri(path: Path) -> str:
    return f"file:{path.as_posix()}?mode=ro&cache=shared"


@asynccontextmanager
async def jellyfin_db() -> AsyncIterator[aiosqlite.Connection]:
    s = get_settings()
    async with aiosqlite.connect(_ro_uri(s.jellyfin_db), uri=True) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA query_only=1")
        yield db


@asynccontextmanager
async def playback_db() -> AsyncIterator[aiosqlite.Connection]:
    s = get_settings()
    async with aiosqlite.connect(_ro_uri(s.playback_db), uri=True) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA query_only=1")
        yield db


async def fetch_all(db: aiosqlite.Connection, sql: str, params: tuple | dict = ()) -> list[aiosqlite.Row]:
    async with db.execute(sql, params) as cur:
        return await cur.fetchall()


async def fetch_one(db: aiosqlite.Connection, sql: str, params: tuple | dict = ()) -> aiosqlite.Row | None:
    async with db.execute(sql, params) as cur:
        return await cur.fetchone()
