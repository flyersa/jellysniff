"""All SQL queries. Parameterized only. No string interpolation of user input."""
from __future__ import annotations

import math
from typing import Any

from .db import fetch_all, fetch_one, jellyfin_db, playback_db
from .intl import normalize_genre


TICKS_PER_SECOND = 10_000_000

# Jellyfin's Playback Reporting plugin sometimes records aborted sessions with
# PlayDuration = -2147483648 (signed-int-32 underflow) or with durations that
# never got closed out and run into days. Both poison aggregate stats, so every
# query against PlaybackActivity filters on this sanity range.
_PD_SANE = "PlayDuration > 0 AND PlayDuration <= 21600"
# Same as above but with the 60-second "trivial play" threshold for popularity
# rankings — barely-started playbacks shouldn't bump an item up the leaderboard.
_PD_REAL = "PlayDuration > 60 AND PlayDuration <= 21600"


def _norm_id(value: str) -> str:
    """Jellyfin stores BaseItem IDs as 32-char lowercase hex without dashes,
    but Users.Id is the canonical hyphenated UPPER GUID. Normalise to no-dash lower
    when matching across tables."""
    return value.replace("-", "").lower()


def _user_id_variants(user_id: str) -> tuple[str, str]:
    """Return (canonical, nodash) forms. Users.Id is hyphenated UPPER in jellyfin.db.
    playback_reporting.db uses nodash lower."""
    clean = user_id.replace("-", "").lower()
    if len(clean) != 32:
        return user_id, clean
    canonical = f"{clean[0:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:32]}".upper()
    return canonical, clean


# ─────────────────────────────────────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────────────────────────────────────

async def list_users() -> list[dict[str, Any]]:
    async with jellyfin_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT u.Id, u.Username, u.LastLoginDate, u.LastActivityDate,
                   COALESCE(p.Value, 0) AS IsAdmin
            FROM Users u
            LEFT JOIN Permissions p ON p.UserId = u.Id AND p.Kind = 0
            ORDER BY u.Username COLLATE NOCASE
            """,
        )
    return [dict(r) for r in rows]


async def get_user(user_id: str) -> dict[str, Any] | None:
    canonical, _ = _user_id_variants(user_id)
    async with jellyfin_db() as db:
        row = await fetch_one(
            db,
            """
            SELECT u.Id, u.Username, u.LastLoginDate, u.LastActivityDate,
                   COALESCE(p.Value, 0) AS IsAdmin
            FROM Users u
            LEFT JOIN Permissions p ON p.UserId = u.Id AND p.Kind = 0
            WHERE u.Id = ? COLLATE NOCASE
            """,
            (canonical,),
        )
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# History (playback_reporting.db)
# ─────────────────────────────────────────────────────────────────────────────

async def user_history(
    user_id: str,
    limit: int = 200,
    since_days: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    _, nodash = _user_id_variants(user_id)
    clauses = ["UserId = ?", _PD_SANE]
    params: list[Any] = [nodash]
    if since_days is not None:
        clauses.append("DateCreated >= datetime('now', ?)")
        params.append(f"-{int(since_days)} days")
    where = " AND ".join(clauses)
    sql = f"""
        SELECT DateCreated, ItemId, ItemType, ItemName, PlaybackMethod,
               ClientName, DeviceName, PlayDuration
        FROM PlaybackActivity
        WHERE {where}
        ORDER BY DateCreated DESC
        LIMIT ? OFFSET ?
    """
    params.extend([int(limit), int(offset)])
    async with playback_db() as db:
        rows = await fetch_all(db, sql, tuple(params))
    return [dict(r) for r in rows]


async def user_play_totals(user_id: str, since_days: int | None = None) -> dict[str, int]:
    """Aggregate plays + watched seconds for a user in the window. Cheap; pulls
    just two scalars so summary pages don't have to slurp the whole history."""
    _, nodash = _user_id_variants(user_id)
    clauses = ["UserId = ?", _PD_SANE]
    params: list[Any] = [nodash]
    if since_days is not None:
        clauses.append("DateCreated >= datetime('now', ?)")
        params.append(f"-{int(since_days)} days")
    where = " AND ".join(clauses)
    async with playback_db() as db:
        row = await fetch_one(
            db,
            f"SELECT COUNT(*) AS plays, COALESCE(SUM(PlayDuration), 0) AS sec FROM PlaybackActivity WHERE {where}",
            tuple(params),
        )
    return {"Plays": int(row["plays"] or 0), "TotalSeconds": int(row["sec"] or 0)}


async def user_history_total(user_id: str, since_days: int | None = None) -> int:
    """Total row count for paginated history."""
    _, nodash = _user_id_variants(user_id)
    clauses = ["UserId = ?", _PD_SANE]
    params: list[Any] = [nodash]
    if since_days is not None:
        clauses.append("DateCreated >= datetime('now', ?)")
        params.append(f"-{int(since_days)} days")
    where = " AND ".join(clauses)
    async with playback_db() as db:
        row = await fetch_one(
            db,
            f"SELECT COUNT(*) AS n FROM PlaybackActivity WHERE {where}",
            tuple(params),
        )
    return int(row["n"]) if row else 0


async def global_history(
    limit: int = 500,
    since_days: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = [_PD_SANE]
    params: list[Any] = []
    if since_days is not None:
        clauses.append("DateCreated >= datetime('now', ?)")
        params.append(f"-{int(since_days)} days")
    where = "WHERE " + " AND ".join(clauses)
    sql = f"""
        SELECT DateCreated, UserId, ItemId, ItemType, ItemName, PlaybackMethod,
               ClientName, DeviceName, PlayDuration
        FROM PlaybackActivity
        {where}
        ORDER BY DateCreated DESC
        LIMIT ? OFFSET ?
    """
    params.extend([int(limit), int(offset)])
    async with playback_db() as db:
        rows = await fetch_all(db, sql, tuple(params))
    return [dict(r) for r in rows]


async def global_history_total(since_days: int | None = None) -> int:
    clauses = [_PD_SANE]
    params: list[Any] = []
    if since_days is not None:
        clauses.append("DateCreated >= datetime('now', ?)")
        params.append(f"-{int(since_days)} days")
    where = "WHERE " + " AND ".join(clauses)
    async with playback_db() as db:
        row = await fetch_one(
            db,
            f"SELECT COUNT(*) AS n FROM PlaybackActivity {where}",
            tuple(params),
        )
    return int(row["n"]) if row else 0


async def top_items(limit: int = 20, since_days: int = 30, user_id: str | None = None) -> list[dict[str, Any]]:
    clauses = ["DateCreated >= datetime('now', ?)", _PD_REAL]
    params: list[Any] = [f"-{int(since_days)} days"]
    if user_id:
        _, nodash = _user_id_variants(user_id)
        clauses.append("UserId = ?")
        params.append(nodash)
    where = " AND ".join(clauses)
    sql = f"""
        SELECT ItemId, ItemName, ItemType,
               COUNT(*) AS Plays,
               SUM(PlayDuration) AS TotalSeconds,
               COUNT(DISTINCT UserId) AS UniqueUsers
        FROM PlaybackActivity
        WHERE {where}
        GROUP BY ItemId, ItemName, ItemType
        ORDER BY TotalSeconds DESC
        LIMIT ?
    """
    params.append(int(limit))
    async with playback_db() as db:
        rows = await fetch_all(db, sql, tuple(params))
    return [dict(r) for r in rows]


async def top_users(limit: int = 20, since_days: int = 30) -> list[dict[str, Any]]:
    async with playback_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT UserId,
                   COUNT(*) AS Plays,
                   SUM(PlayDuration) AS TotalSeconds,
                   COUNT(DISTINCT ItemId) AS DistinctItems
            FROM PlaybackActivity
            WHERE DateCreated >= datetime('now', ?)
              AND PlayDuration > 60 AND PlayDuration <= 21600
            GROUP BY UserId
            ORDER BY TotalSeconds DESC
            LIMIT ?
            """,
            (f"-{int(since_days)} days", int(limit)),
        )
    pbr = [dict(r) for r in rows]
    if not pbr:
        return []
    # Enrich with username
    user_map = {u["Id"].replace("-", "").lower(): u["Username"] for u in await list_users()}
    for r in pbr:
        r["Username"] = user_map.get(r["UserId"], r["UserId"][:8])
    return pbr


async def device_breakdown(since_days: int = 30, user_id: str | None = None) -> list[dict[str, Any]]:
    clauses = ["DateCreated >= datetime('now', ?)", _PD_SANE]
    params: list[Any] = [f"-{int(since_days)} days"]
    if user_id:
        _, nodash = _user_id_variants(user_id)
        clauses.append("UserId = ?")
        params.append(nodash)
    where = " AND ".join(clauses)
    sql = f"""
        SELECT ClientName, DeviceName,
               COUNT(*) AS Plays,
               SUM(PlayDuration) AS TotalSeconds
        FROM PlaybackActivity
        WHERE {where}
        GROUP BY ClientName, DeviceName
        ORDER BY TotalSeconds DESC
    """
    async with playback_db() as db:
        rows = await fetch_all(db, sql, tuple(params))
    return [dict(r) for r in rows]


async def hourly_heatmap(since_days: int = 30, user_id: str | None = None) -> list[dict[str, Any]]:
    """Returns rows of (dow 0-6 Sun=0, hour 0-23, plays, seconds)."""
    clauses = ["DateCreated >= datetime('now', ?)", _PD_SANE]
    params: list[Any] = [f"-{int(since_days)} days"]
    if user_id:
        _, nodash = _user_id_variants(user_id)
        clauses.append("UserId = ?")
        params.append(nodash)
    where = " AND ".join(clauses)
    sql = f"""
        SELECT CAST(strftime('%w', DateCreated) AS INT) AS Dow,
               CAST(strftime('%H', DateCreated) AS INT) AS Hour,
               COUNT(*) AS Plays,
               SUM(PlayDuration) AS TotalSeconds
        FROM PlaybackActivity
        WHERE {where}
        GROUP BY Dow, Hour
    """
    async with playback_db() as db:
        rows = await fetch_all(db, sql, tuple(params))
    return [dict(r) for r in rows]


async def daily_activity(since_days: int = 30, user_id: str | None = None) -> list[dict[str, Any]]:
    clauses = ["DateCreated >= datetime('now', ?)", _PD_SANE]
    params: list[Any] = [f"-{int(since_days)} days"]
    if user_id:
        _, nodash = _user_id_variants(user_id)
        clauses.append("UserId = ?")
        params.append(nodash)
    where = " AND ".join(clauses)
    sql = f"""
        SELECT date(DateCreated) AS Day,
               COUNT(*) AS Plays,
               SUM(PlayDuration) AS TotalSeconds,
               COUNT(DISTINCT UserId) AS Users
        FROM PlaybackActivity
        WHERE {where}
        GROUP BY Day
        ORDER BY Day
    """
    async with playback_db() as db:
        rows = await fetch_all(db, sql, tuple(params))
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Library + UserData (jellyfin.db)
# ─────────────────────────────────────────────────────────────────────────────

async def library_overview() -> dict[str, int]:
    async with jellyfin_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT
              SUM(CASE WHEN Type LIKE '%Movie' THEN 1 ELSE 0 END) AS Movies,
              SUM(CASE WHEN Type LIKE '%TV.Series' THEN 1 ELSE 0 END) AS Series,
              SUM(CASE WHEN Type LIKE '%Episode' THEN 1 ELSE 0 END) AS Episodes,
              SUM(CASE WHEN Type LIKE '%AudioBook' THEN 1 ELSE 0 END) AS AudioBooks,
              SUM(CASE WHEN Type LIKE '%MusicArtist' THEN 1 ELSE 0 END) AS Artists
            FROM BaseItems
            WHERE IsVirtualItem = 0
            """,
        )
    r = rows[0] if rows else {}
    return {k: (r[k] or 0) for k in ("Movies", "Series", "Episodes", "AudioBooks", "Artists")}


async def item_lookup(item_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch BaseItem metadata for a set of IDs (nodash lower hex)."""
    if not item_ids:
        return {}
    cleaned = list({_norm_id(i) for i in item_ids if i})
    if not cleaned:
        return {}
    placeholders = ",".join("?" * len(cleaned))
    sql = f"""
        SELECT lower(replace(b.Id, '-', '')) AS NormId,
               b.Id, b.Name, b.Type, b.SeriesName, b.SeasonName,
               b.IndexNumber, b.ParentIndexNumber, b.ProductionYear,
               b.RunTimeTicks, b.CommunityRating, b.Overview, b.OfficialRating,
               b.PremiereDate
        FROM BaseItems b
        WHERE lower(replace(b.Id, '-', '')) IN ({placeholders})
    """
    async with jellyfin_db() as db:
        rows = await fetch_all(db, sql, tuple(cleaned))
    return {r["NormId"]: dict(r) for r in rows}


async def user_favorites(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Whitelist real media types only. BaseItems also contains placeholder
    rows (Type='PLACEHOLDER', CollectionFolder, UserView, Folder, Person,
    Studio, Genre) that shouldn't show as favorites even if IsFavorite is
    set on them."""
    canonical, _ = _user_id_variants(user_id)
    async with jellyfin_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT b.Id, b.Name, b.Type, b.SeriesName, b.ProductionYear,
                   b.CommunityRating,
                   MAX(ud.LastPlayedDate) AS LastPlayedDate,
                   MAX(ud.PlayCount) AS PlayCount
            FROM UserData ud
            JOIN BaseItems b ON b.Id = ud.ItemId
            WHERE ud.UserId = ? COLLATE NOCASE
              AND ud.IsFavorite = 1
              AND b.IsVirtualItem = 0
              AND b.Name IS NOT NULL AND b.Name <> ''
              AND (
                   b.Type LIKE '%.Movie'
                OR b.Type LIKE '%.Episode'
                OR b.Type LIKE '%.TV.Series'
                OR b.Type LIKE '%.TV.Season'
                OR b.Type LIKE '%.AudioBook'
                OR b.Type LIKE '%.Video'
                OR b.Type LIKE '%.Book'
                OR b.Type LIKE '%.Audio.MusicArtist'
                OR b.Type LIKE '%.Audio.MusicAlbum'
                OR b.Type LIKE '%.Audio'
                OR b.Type LIKE '%.Playlists.Playlist'
              )
            -- UserData PK is (ItemId, UserId, CustomDataKey) so the same item
            -- can land in 2-3 rows. GROUP BY b.Id collapses to one per item.
            GROUP BY b.Id, b.Name, b.Type, b.SeriesName, b.ProductionYear, b.CommunityRating
            ORDER BY MAX(ud.LastPlayedDate) DESC NULLS LAST
            LIMIT ?
            """,
            (canonical, int(limit)),
        )
    return [dict(r) for r in rows]


async def user_played_item_ids(user_id: str) -> set[str]:
    canonical, _ = _user_id_variants(user_id)
    async with jellyfin_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT lower(replace(ItemId, '-', '')) AS NormId
            FROM UserData
            WHERE UserId = ? COLLATE NOCASE AND Played = 1
            """,
            (canonical,),
        )
    return {r["NormId"] for r in rows}


async def user_genre_profile(user_id: str, top: int = 10) -> list[dict[str, Any]]:
    """Counts genre occurrences across user's played items.
    Genres are normalized (German labels folded into their English equivalents)
    so duplicates don't split the profile."""
    canonical, _ = _user_id_variants(user_id)
    async with jellyfin_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT iv.Value AS Genre, COUNT(*) AS Hits
            FROM UserData ud
            JOIN ItemValuesMap m ON m.ItemId = ud.ItemId
            JOIN ItemValues iv ON iv.ItemValueId = m.ItemValueId AND iv.Type = 2
            WHERE ud.UserId = ? COLLATE NOCASE
              AND (ud.Played = 1 OR ud.PlayCount > 0 OR ud.IsFavorite = 1)
            GROUP BY iv.Value
            """,
            (canonical,),
        )
    merged: dict[str, int] = {}
    for r in rows:
        name = normalize_genre(r["Genre"]) or r["Genre"]
        merged[name] = merged.get(name, 0) + (r["Hits"] or 0)
    out = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[: int(top)]
    return [{"Genre": g, "Hits": h} for g, h in out]


async def suggestions_for_user(user_id: str, limit: int = 24) -> list[dict[str, Any]]:
    """
    Hybrid recommender:
      1) Compute user's top genres (UserData × ItemValues type 2).
      2) Find unplayed Movies/Series whose genre overlap with top-N is highest.
      3) Tie-break by CommunityRating desc, then DateCreated desc.
    """
    canonical, _ = _user_id_variants(user_id)
    profile = await user_genre_profile(user_id, top=8)
    if not profile:
        # Cold start: just return top-rated movies user hasn't played
        async with jellyfin_db() as db:
            rows = await fetch_all(
                db,
                """
                SELECT b.Id, b.Name, b.Type, b.SeriesName, b.ProductionYear,
                       b.CommunityRating, b.Overview
                FROM BaseItems b
                LEFT JOIN UserData ud
                       ON ud.ItemId = b.Id AND ud.UserId = ? COLLATE NOCASE
                WHERE b.IsVirtualItem = 0
                  AND (b.Type LIKE '%Movie' OR b.Type LIKE '%TV.Series')
                  AND b.CommunityRating IS NOT NULL
                  AND (ud.Played IS NULL OR ud.Played = 0)
                ORDER BY b.CommunityRating DESC, b.DateCreated DESC
                LIMIT ?
                """,
                (canonical, int(limit)),
            )
        return [dict(r) for r in rows]

    genres = [g["Genre"] for g in profile]
    weights = {g["Genre"]: g["Hits"] for g in profile}
    placeholders = ",".join("?" * len(genres))
    sql = f"""
        WITH GenreHits AS (
          SELECT m.ItemId, iv.Value AS Genre
          FROM ItemValuesMap m
          JOIN ItemValues iv ON iv.ItemValueId = m.ItemValueId AND iv.Type = 2
          WHERE iv.Value IN ({placeholders})
        ),
        ItemScore AS (
          SELECT b.Id, COUNT(DISTINCT gh.Genre) AS GenreMatches
          FROM BaseItems b
          JOIN GenreHits gh ON gh.ItemId = b.Id
          WHERE b.IsVirtualItem = 0
            AND (b.Type LIKE '%Movie' OR b.Type LIKE '%TV.Series')
          GROUP BY b.Id
        )
        SELECT b.Id, b.Name, b.Type, b.SeriesName, b.ProductionYear,
               b.CommunityRating, b.Overview, s.GenreMatches
        FROM ItemScore s
        JOIN BaseItems b ON b.Id = s.Id
        LEFT JOIN UserData ud
               ON ud.ItemId = b.Id AND ud.UserId = ? COLLATE NOCASE
        WHERE (ud.Played IS NULL OR ud.Played = 0)
        ORDER BY s.GenreMatches DESC,
                 b.CommunityRating DESC NULLS LAST,
                 b.DateCreated DESC
        LIMIT ?
    """
    params = (*genres, canonical, int(limit))
    async with jellyfin_db() as db:
        rows = await fetch_all(db, sql, params)
    out = [dict(r) for r in rows]
    # Apply genre weights for nicer ordering when ties happen
    for r in out:
        r["_weight"] = sum(weights.get(g, 0) for g in genres) if r.get("GenreMatches") else 0
    return out


async def popular_now(
    user_id: str | None = None,
    days: int = 21,
    limit: int = 12,
    min_viewers: int = 2,
    decay_days: float = 10.0,
) -> list[dict[str, Any]]:
    """Items trending across a 3-week window with exponential recency decay.

    Each play contributes weight = exp(-age_days / decay_days) to the item's
    "hot score". With decay_days=10 a play today is weight 1.0, a week ago
    ~0.50, two weeks ago ~0.25, three weeks ago ~0.12. So recent activity
    dominates the ranking while not-quite-this-week hits still appear.

    Episodes roll up to their series so the panel reads "Stargate SG-1" rather
    than "S03E07". Items must have at least `min_viewers` distinct viewers in
    the window so a single binger doesn't pollute the leaderboard. If
    `user_id` is given, items the user has already played are excluded.

    A `Trending` flag is set when more than 60% of an item's hot score comes
    from plays within the last 7 days — useful for highlighting fresh hits
    in the UI.
    """
    # Episode → Series rollup map
    async with jellyfin_db() as db:
        ep_rows = await fetch_all(
            db,
            """
            SELECT lower(replace(Id,'-','')) AS Id,
                   lower(replace(SeriesId,'-','')) AS SeriesId
            FROM BaseItems
            WHERE Type LIKE '%Episode'
              AND SeriesId IS NOT NULL
              AND IsVirtualItem = 0
            """,
        )
    ep_to_series = {r["Id"]: r["SeriesId"] for r in ep_rows}

    excluded: set[str] = set()
    if user_id:
        canonical, _ = _user_id_variants(user_id)
        async with jellyfin_db() as db:
            ex_rows = await fetch_all(
                db,
                """
                SELECT DISTINCT lower(replace(COALESCE(b.SeriesId, b.Id),'-','')) AS Id
                FROM UserData ud
                JOIN BaseItems b ON b.Id = ud.ItemId
                WHERE ud.UserId = ? COLLATE NOCASE
                  AND (ud.Played = 1 OR ud.PlayCount > 0 OR ud.IsFavorite = 1)
                  AND b.IsVirtualItem = 0
                """,
                (canonical,),
            )
        excluded = {r["Id"] for r in ex_rows}

    # Raw playback in window — pull AgeDays so we can decay-weight in Python.
    async with playback_db() as db:
        rows = await fetch_all(
            db,
            f"""
            SELECT UserId, ItemId, PlayDuration,
                   (julianday('now') - julianday(DateCreated)) AS AgeDays
            FROM PlaybackActivity
            WHERE DateCreated >= datetime('now', ?)
              AND PlayDuration > 60 AND PlayDuration <= 21600
            """,
            (f"-{int(days)} days",),
        )

    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        iid = (r["ItemId"] or "").lower()
        rolled = ep_to_series.get(iid, iid)
        if rolled in excluded or not rolled:
            continue
        age = max(0.0, float(r["AgeDays"] or 0.0))
        weight = math.exp(-age / max(0.5, decay_days))
        a = agg.setdefault(rolled, {
            "plays": 0, "viewers": set(), "secs": 0,
            "hot": 0.0, "hot_7d": 0.0,
            "recent_viewers": set(),
        })
        a["plays"] += 1
        a["secs"] += r["PlayDuration"] or 0
        a["viewers"].add(r["UserId"])
        a["hot"] += weight
        if age <= 7.0:
            a["hot_7d"] += weight
            a["recent_viewers"].add(r["UserId"])

    candidates = [(k, v) for k, v in agg.items() if len(v["viewers"]) >= min_viewers]
    candidates.sort(
        key=lambda kv: (kv[1]["hot"], len(kv[1]["viewers"]), kv[1]["secs"]),
        reverse=True,
    )
    # Overfetch so we still hit the requested limit after dropping deleted items.
    candidates = candidates[: int(limit) * 3]
    if not candidates:
        return []

    ids = [k for k, _ in candidates]
    metadata = await item_lookup(ids)

    out: list[dict[str, Any]] = []
    for iid, stats in candidates:
        m = metadata.get(iid)
        if not m or not m.get("Name"):
            # Item was deleted from the library but playback rows remain — skip.
            continue
        if len(out) >= limit:
            break
        hot = stats["hot"]
        hot_7d = stats["hot_7d"]
        trending = hot > 0 and (hot_7d / hot) >= 0.6
        out.append({
            "Id": iid,
            "Name": m.get("Name"),
            "Type": (m.get("Type") or "").split(".")[-1],
            "SeriesName": m.get("SeriesName"),
            "ProductionYear": m.get("ProductionYear"),
            "CommunityRating": m.get("CommunityRating"),
            "Viewers": len(stats["viewers"]),
            "RecentViewers": len(stats["recent_viewers"]),
            "Plays": stats["plays"],
            "TotalSeconds": stats["secs"],
            "HotScore": round(hot, 3),
            "Trending": trending,
        })
    return out


async def monthly_activity(user_id: str | None = None, months: int = 12) -> list[dict[str, Any]]:
    clauses = ["DateCreated >= datetime('now', ?)", _PD_SANE]
    params: list[Any] = [f"-{int(months)*31} days"]
    if user_id:
        _, nodash = _user_id_variants(user_id)
        clauses.append("UserId = ?")
        params.append(nodash)
    where = " AND ".join(clauses)
    sql = f"""
        SELECT strftime('%Y-%m', DateCreated) AS Month,
               COUNT(*) AS Plays,
               SUM(PlayDuration) AS TotalSeconds
        FROM PlaybackActivity
        WHERE {where}
        GROUP BY Month
        ORDER BY Month
    """
    async with playback_db() as db:
        rows = await fetch_all(db, sql, tuple(params))
    return [dict(r) for r in rows]


async def series_completion(user_id: str, limit: int = 12) -> list[dict[str, Any]]:
    """For each series the user has watched at least one episode of,
    compute episodes watched vs episodes in library."""
    canonical, _ = _user_id_variants(user_id)
    async with jellyfin_db() as db:
        rows = await fetch_all(
            db,
            """
            WITH WatchedEpisodes AS (
              -- DISTINCT to collapse UserData rows that share an ItemId
              -- across multiple CustomDataKey values (which are part of the PK)
              SELECT DISTINCT b.Id AS EpId, b.SeriesId
              FROM UserData ud
              JOIN BaseItems b ON b.Id = ud.ItemId
              WHERE ud.UserId = ? COLLATE NOCASE
                AND ud.Played = 1
                AND b.Type LIKE '%Episode'
                AND b.SeriesId IS NOT NULL
                AND b.IsVirtualItem = 0
            ),
            SeriesStats AS (
              SELECT we.SeriesId,
                     COUNT(*) AS Watched
              FROM WatchedEpisodes we
              GROUP BY we.SeriesId
            ),
            SeriesTotals AS (
              SELECT b.SeriesId,
                     COUNT(DISTINCT b.Id) AS Total
              FROM BaseItems b
              WHERE b.Type LIKE '%Episode'
                AND b.IsVirtualItem = 0
                AND b.SeriesId IS NOT NULL
              GROUP BY b.SeriesId
            )
            SELECT s.Id AS SeriesId,
                   s.Name AS SeriesName,
                   s.ProductionYear,
                   ss.Watched,
                   st.Total
            FROM SeriesStats ss
            JOIN SeriesTotals st ON st.SeriesId = ss.SeriesId
            JOIN BaseItems s ON s.Id = ss.SeriesId
            WHERE s.Type LIKE '%TV.Series' AND s.IsVirtualItem = 0
            ORDER BY (CAST(ss.Watched AS REAL) / st.Total) DESC, ss.Watched DESC
            LIMIT ?
            """,
            (canonical, int(limit)),
        )
    return [dict(r) for r in rows]


async def item_detail_movie_or_series(item_id: str) -> dict[str, Any] | None:
    """Lookup an item; if it's an Episode, resolve up to its Series."""
    nodash = _norm_id(item_id)
    async with jellyfin_db() as db:
        row = await fetch_one(
            db,
            """
            SELECT lower(replace(Id,'-','')) AS NormId,
                   Id, Name, Type, SeriesId, SeasonId, SeriesName,
                   IndexNumber, ParentIndexNumber, ProductionYear,
                   RunTimeTicks, CommunityRating, Overview, OfficialRating,
                   PremiereDate, Genres, Studios, Tags
            FROM BaseItems
            WHERE lower(replace(Id,'-','')) = ?
            """,
            (nodash,),
        )
    return dict(row) if row else None


async def item_viewers(item_id: str, since_days: int = 3650, limit: int = 50) -> list[dict[str, Any]]:
    """Who has watched this item (or any episode of this series), plays + watched-time."""
    nodash = _norm_id(item_id)
    # First, figure out whether this is a series → expand to its episodes
    async with jellyfin_db() as db:
        kind = await fetch_one(
            db,
            "SELECT Type FROM BaseItems WHERE lower(replace(Id,'-','')) = ?",
            (nodash,),
        )
    item_ids: list[str] = [nodash]
    if kind and "Series" in (kind["Type"] or ""):
        async with jellyfin_db() as db:
            ep_rows = await fetch_all(
                db,
                """
                SELECT lower(replace(Id,'-','')) AS Id
                FROM BaseItems
                WHERE Type LIKE '%Episode'
                  AND lower(replace(SeriesId,'-','')) = ?
                """,
                (nodash,),
            )
        item_ids = [r["Id"] for r in ep_rows] or [nodash]

    placeholders = ",".join("?" * len(item_ids))
    sql = f"""
        SELECT UserId,
               COUNT(*) AS Plays,
               SUM(PlayDuration) AS TotalSeconds,
               MAX(DateCreated) AS LastPlayed
        FROM PlaybackActivity
        WHERE ItemId IN ({placeholders})
          AND DateCreated >= datetime('now', ?)
          AND PlayDuration > 0 AND PlayDuration <= 21600
        GROUP BY UserId
        ORDER BY TotalSeconds DESC
        LIMIT ?
    """
    params = (*item_ids, f"-{int(since_days)} days", int(limit))
    async with playback_db() as db:
        rows = await fetch_all(db, sql, params)
    pbr = [dict(r) for r in rows]
    if not pbr:
        return []
    user_map = {u["Id"].replace("-", "").lower(): u["Username"] for u in await list_users()}
    for r in pbr:
        r["Username"] = user_map.get((r["UserId"] or "").lower(), (r["UserId"] or "")[:8])
    return pbr


async def series_episode_plays(series_id: str, since_days: int = 3650) -> list[dict[str, Any]]:
    """For a series: per-episode aggregate plays + viewers."""
    nodash = _norm_id(series_id)
    async with jellyfin_db() as db:
        ep_rows = await fetch_all(
            db,
            """
            SELECT lower(replace(Id,'-','')) AS Id, Name, IndexNumber,
                   ParentIndexNumber AS Season
            FROM BaseItems
            WHERE Type LIKE '%Episode'
              AND lower(replace(SeriesId,'-','')) = ?
              AND IsVirtualItem = 0
            ORDER BY ParentIndexNumber, IndexNumber
            """,
            (nodash,),
        )
    if not ep_rows:
        return []
    ep_meta = {r["Id"]: dict(r) for r in ep_rows}
    ids = list(ep_meta.keys())
    placeholders = ",".join("?" * len(ids))
    async with playback_db() as db:
        rows = await fetch_all(
            db,
            f"""
            SELECT ItemId,
                   COUNT(*) AS Plays,
                   COUNT(DISTINCT UserId) AS Viewers,
                   SUM(PlayDuration) AS TotalSeconds,
                   MAX(DateCreated) AS LastPlayed
            FROM PlaybackActivity
            WHERE ItemId IN ({placeholders})
              AND DateCreated >= datetime('now', ?)
              AND PlayDuration > 0 AND PlayDuration <= 21600
            GROUP BY ItemId
            """,
            (*ids, f"-{int(since_days)} days"),
        )
    stats = {r["ItemId"]: dict(r) for r in rows}
    out: list[dict[str, Any]] = []
    for eid, meta in ep_meta.items():
        s = stats.get(eid, {})
        out.append({
            "Id": eid,
            "Name": meta["Name"],
            "Season": meta["Season"],
            "Episode": meta["IndexNumber"],
            "Plays": s.get("Plays", 0),
            "Viewers": s.get("Viewers", 0),
            "TotalSeconds": s.get("TotalSeconds", 0) or 0,
            "LastPlayed": s.get("LastPlayed"),
        })
    return out


async def co_watch_suggestions(user_id: str, limit: int = 24) -> list[dict[str, Any]]:
    """Item-item co-watch: pull series the user has watched, then series other people
    who watched the same things also watched. Cheap and effective on Jellyfin scale."""
    _, nodash = _user_id_variants(user_id)
    async with playback_db() as db:
        seen_rows = await fetch_all(
            db,
            """
            SELECT DISTINCT ItemId
            FROM PlaybackActivity
            WHERE UserId = ? AND PlayDuration > 120 AND PlayDuration <= 21600
            """,
            (nodash,),
        )
        seen_ids = [r["ItemId"] for r in seen_rows]
        if not seen_ids:
            return []
        # users who watched any of these items
        ph = ",".join("?" * len(seen_ids))
        peers = await fetch_all(
            db,
            f"SELECT DISTINCT UserId FROM PlaybackActivity WHERE ItemId IN ({ph}) AND UserId != ? AND PlayDuration > 0 AND PlayDuration <= 21600",
            (*seen_ids, nodash),
        )
        peer_ids = [p["UserId"] for p in peers]
        if not peer_ids:
            return []
        php = ",".join("?" * len(peer_ids))
        seen_set = set(seen_ids)
        rows = await fetch_all(
            db,
            f"""
            SELECT ItemId, ItemName, ItemType, COUNT(DISTINCT UserId) AS Peers,
                   SUM(PlayDuration) AS TotalSeconds
            FROM PlaybackActivity
            WHERE UserId IN ({php}) AND PlayDuration > 120 AND PlayDuration <= 21600
            GROUP BY ItemId, ItemName, ItemType
            ORDER BY Peers DESC, TotalSeconds DESC
            LIMIT ?
            """,
            (*peer_ids, int(limit) * 4),
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["ItemId"] in seen_set:
            continue
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out
