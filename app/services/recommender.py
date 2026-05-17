"""
Hybrid recommender for JellySniff.

Two signals combined:

  1. **Content vectors** — every recommendable item is represented as a sparse
     vector over (genre, studio, tag, keyword) features pulled from ItemValues.
     Each feature is IDF-weighted so common ones (e.g. "Drama") don't dominate.
     A user's "taste vector" is the weighted sum of vectors of items they
     watched/favorited. Score = cosine similarity to taste vector.

  2. **Implicit-feedback collaborative filtering** — build a sparse user×item
     matrix from PlaybackActivity (one entry per user-item pair, weighted by
     log(seconds_watched + 1)). Truncated SVD gives latent factors for users
     and items; score = (user_factor · item_factor).

The final ranking is α·content + β·collab, then we drop items the user has
already played and return the top N. Both signals are computed lazily and
cached per process for a short TTL — at ~5k movies × 35 users this is dirt
cheap, but caching keeps page reloads snappy.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .db import fetch_all, jellyfin_db, playback_db
from .intl import normalize_genre


_TTL_SEC = 600.0
_LOCK = asyncio.Lock()


def _norm_id(value: str) -> str:
    return (value or "").replace("-", "").lower()


def _user_canonical(user_id: str) -> str:
    clean = _norm_id(user_id)
    if len(clean) != 32:
        return user_id
    return f"{clean[0:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:32]}".upper()


@dataclass
class ItemMeta:
    id: str  # nodash lower
    name: str
    type: str
    series_name: str | None
    year: int | None
    rating: float | None
    overview: str | None


@dataclass
class Model:
    built_at: float
    item_ids: list[str]
    item_index: dict[str, int]
    item_meta: dict[str, ItemMeta]
    content_matrix: np.ndarray   # shape (n_items, n_features) — L2-normalised rows
    feature_keys: list[tuple[int, str]]   # column index → (feature_type, value)
    user_ids: list[str]          # nodash lower
    user_index: dict[str, int]
    user_factors: np.ndarray     # shape (n_users, k)
    item_factors: np.ndarray     # shape (n_items, k)
    played_by_user: dict[str, set[str]]   # nodash userid → set of nodash itemids
    user_id_to_name: dict[str, str]       # nodash userid → username


_model: Model | None = None


async def _load_items() -> list[ItemMeta]:
    """Load Movie + Series items that are candidates for recommendation."""
    async with jellyfin_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT lower(replace(Id, '-', '')) AS Id,
                   Name, Type, SeriesName, ProductionYear, CommunityRating, Overview
            FROM BaseItems
            WHERE IsVirtualItem = 0
              AND (Type LIKE '%Movie' OR Type LIKE '%TV.Series')
              AND Name IS NOT NULL
            """,
        )
    return [
        ItemMeta(
            id=r["Id"],
            name=r["Name"],
            type=r["Type"].rsplit(".", 1)[-1],
            series_name=r["SeriesName"],
            year=r["ProductionYear"],
            rating=r["CommunityRating"],
            overview=r["Overview"],
        )
        for r in rows
    ]


async def _load_item_features(item_ids: set[str]) -> dict[str, list[tuple[int, str]]]:
    """For each item, return list of (feature_type, feature_value) tuples.

    Feature types kept: 2=Genres, 3=Studios, 4=Tags, 6=Keywords.
    """
    if not item_ids:
        return {}
    async with jellyfin_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT lower(replace(m.ItemId,'-','')) AS Id, iv.Type, iv.Value
            FROM ItemValuesMap m
            JOIN ItemValues iv ON iv.ItemValueId = m.ItemValueId
            WHERE iv.Type IN (2,3,4,6)
            """,
        )
    out: dict[str, list[tuple[int, str]]] = {}
    for r in rows:
        if r["Id"] not in item_ids:
            continue
        ft = r["Type"]
        val = r["Value"]
        # Normalise German genre labels into their English equivalents so the
        # content vector doesn't split "Abenteuer"/"Adventure" into two features.
        if ft == 2:
            val = normalize_genre(val) or val
        out.setdefault(r["Id"], []).append((ft, val))
    return out


async def _load_playback_matrix(item_ids: set[str]) -> tuple[dict[str, dict[str, float]], dict[str, set[str]]]:
    """Build implicit feedback: for each user, map item_id → log(seconds+1).

    Episodes in PlaybackActivity roll up to their Series via BaseItems.SeriesId
    so the SVD operates on the same item set as the content matrix.
    """
    # Map episode-id → series-id (nodash, lower) so episode plays count for the series
    async with jellyfin_db() as db:
        ep_rows = await fetch_all(
            db,
            """
            SELECT lower(replace(Id,'-','')) AS EpisodeId,
                   lower(replace(SeriesId,'-','')) AS SeriesId
            FROM BaseItems
            WHERE Type LIKE '%Episode' AND SeriesId IS NOT NULL
            """,
        )
        ep_to_series = {r["EpisodeId"]: r["SeriesId"] for r in ep_rows}

    async with playback_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT UserId,
                   ItemId,
                   SUM(PlayDuration) AS Sec
            FROM PlaybackActivity
            WHERE PlayDuration > 30
              AND PlayDuration <= 21600
              AND DateCreated >= datetime('now','-720 days')
            GROUP BY UserId, ItemId
            """,
        )

    user_to_items: dict[str, dict[str, float]] = {}
    played: dict[str, set[str]] = {}
    for r in rows:
        uid = (r["UserId"] or "").lower()
        iid = (r["ItemId"] or "").lower()
        # Roll episodes up to series
        iid = ep_to_series.get(iid, iid)
        if iid not in item_ids:
            continue
        sec = float(r["Sec"] or 0.0)
        if sec <= 0:
            continue
        user_to_items.setdefault(uid, {})
        user_to_items[uid][iid] = user_to_items[uid].get(iid, 0.0) + sec
        played.setdefault(uid, set()).add(iid)
    # log-transform
    for uid, items in user_to_items.items():
        for iid, sec in items.items():
            items[iid] = math.log1p(sec)
    return user_to_items, played


async def _load_favorites(item_ids: set[str]) -> dict[str, dict[str, float]]:
    """Add a constant favorite boost to the implicit-feedback matrix."""
    async with jellyfin_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT lower(replace(UserId,'-','')) AS Uid,
                   lower(replace(ItemId,'-','')) AS Iid
            FROM UserData
            WHERE IsFavorite = 1
            """,
        )
    fav: dict[str, dict[str, float]] = {}
    for r in rows:
        iid = r["Iid"]
        if iid not in item_ids:
            continue
        fav.setdefault(r["Uid"], {})[iid] = 5.0
    return fav


def _build_content_matrix(
    items: list[ItemMeta], feats: dict[str, list[tuple[int, str]]]
) -> tuple[np.ndarray, list[tuple[int, str]]]:
    """One-hot/IDF matrix of items × features. Returns (matrix, feature_keys)
    where feature_keys[col] is the (feature_type, value) tuple represented by
    that column — used downstream to explain why an item got recommended."""
    feature_index: dict[tuple[int, str], int] = {}
    for it in items:
        for ft, v in feats.get(it.id, []):
            key = (ft, v)
            if key not in feature_index:
                feature_index[key] = len(feature_index)
    n_items = len(items)
    n_feats = len(feature_index)
    feature_keys: list[tuple[int, str]] = [(0, "")] * n_feats
    for k, i in feature_index.items():
        feature_keys[i] = k
    if n_items == 0 or n_feats == 0:
        return np.zeros((n_items, max(1, n_feats)), dtype=np.float32), feature_keys

    mat = np.zeros((n_items, n_feats), dtype=np.float32)
    for i, it in enumerate(items):
        for ft, v in feats.get(it.id, []):
            j = feature_index[(ft, v)]
            # Weight different feature types differently — genres+studios stronger
            w = 1.5 if ft == 2 else 1.0 if ft == 3 else 0.6
            mat[i, j] = w

    # IDF weighting: rare features get higher weight
    df = (mat > 0).sum(axis=0).astype(np.float32)
    idf = np.log((n_items + 1.0) / (df + 1.0)) + 1.0
    mat = mat * idf

    # L2-normalize rows for cosine similarity
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    return mat, feature_keys


def _build_svd(
    item_ids: list[str], item_index: dict[str, int],
    plays: dict[str, dict[str, float]], favs: dict[str, dict[str, float]],
    k: int = 24,
) -> tuple[list[str], dict[str, int], np.ndarray, np.ndarray]:
    """Truncated SVD on the user × item implicit-feedback matrix.

    Returns (user_ids, user_index, U·Σ, V^T·Σ — i.e. user_factors, item_factors).
    Score for (u, i) is user_factors[u] · item_factors[i].
    """
    n_items = len(item_ids)
    user_ids = sorted(set(plays.keys()) | set(favs.keys()))
    user_index = {u: i for i, u in enumerate(user_ids)}
    n_users = len(user_ids)
    if n_users == 0 or n_items == 0:
        return user_ids, user_index, np.zeros((n_users, k), np.float32), np.zeros((n_items, k), np.float32)

    M = np.zeros((n_users, n_items), dtype=np.float32)
    for u, items in plays.items():
        ui = user_index[u]
        for iid, val in items.items():
            j = item_index.get(iid)
            if j is not None:
                M[ui, j] += val
    for u, items in favs.items():
        ui = user_index.get(u)
        if ui is None:
            continue
        for iid, val in items.items():
            j = item_index.get(iid)
            if j is not None:
                M[ui, j] += val

    k_eff = min(k, n_users, n_items)
    if k_eff < 2:
        return user_ids, user_index, np.zeros((n_users, k), np.float32), np.zeros((n_items, k), np.float32)

    # full_matrices=False yields the thin SVD; we keep top-k_eff components
    U, s, Vt = np.linalg.svd(M, full_matrices=False)
    U_k = U[:, :k_eff]
    s_k = s[:k_eff]
    V_k = Vt[:k_eff].T   # (n_items, k_eff)

    user_factors = (U_k * np.sqrt(s_k)).astype(np.float32)
    item_factors = (V_k * np.sqrt(s_k)).astype(np.float32)

    # Pad to k columns if needed so consumers always see shape (·, k)
    if k_eff < k:
        pad_u = np.zeros((n_users, k - k_eff), np.float32)
        pad_i = np.zeros((n_items, k - k_eff), np.float32)
        user_factors = np.concatenate([user_factors, pad_u], axis=1)
        item_factors = np.concatenate([item_factors, pad_i], axis=1)
    return user_ids, user_index, user_factors, item_factors


async def _rebuild() -> Model:
    items = await _load_items()
    item_ids = [it.id for it in items]
    item_index = {iid: i for i, iid in enumerate(item_ids)}
    item_meta = {it.id: it for it in items}
    item_id_set = set(item_ids)

    feats = await _load_item_features(item_id_set)
    plays, played = await _load_playback_matrix(item_id_set)
    favs = await _load_favorites(item_id_set)

    # Augment `played` from favorites too, so we don't re-recommend favorited items
    for u, fitems in favs.items():
        played.setdefault(u, set()).update(fitems.keys())

    content, feature_keys = _build_content_matrix(items, feats)
    user_ids, user_index, U, V = _build_svd(item_ids, item_index, plays, favs)

    # Resolve playback UserIds → usernames so explanation strings can name peers.
    from . import queries as _queries  # local import to dodge circulars at import time
    users = await _queries.list_users()
    user_id_to_name = {
        u["Id"].replace("-", "").lower(): u["Username"] for u in users
    }

    return Model(
        built_at=time.time(),
        item_ids=item_ids, item_index=item_index, item_meta=item_meta,
        content_matrix=content, feature_keys=feature_keys,
        user_ids=user_ids, user_index=user_index,
        user_factors=U, item_factors=V,
        played_by_user=played,
        user_id_to_name=user_id_to_name,
    )


async def get_model(force: bool = False) -> Model:
    global _model
    async with _LOCK:
        if (
            _model is None
            or force
            or (time.time() - _model.built_at) > _TTL_SEC
        ):
            _model = await _rebuild()
        return _model


_FEATURE_KIND = {2: "genre", 3: "studio", 4: "tag", 6: "keyword"}


async def recommend_for_user(user_id: str, limit: int = 24,
                             alpha: float = 0.6, beta: float = 0.4) -> list[dict[str, Any]]:
    """Hybrid content + collaborative recommendation with explanations.

    For each pick we return:
      - MatchPct: 50–99, scaled per-batch so the best pick is the strongest.
      - Reasons: up to 5 entries of {kind, label} where kind ∈
        {genre, studio, tag, keyword, peer}. Built from the dominant content
        features the user and the item share, plus latent-factor-similar peers
        who have actually played this item.
    """
    m = await get_model()
    uid = _norm_id(user_id)
    played = m.played_by_user.get(uid, set())

    # Content score: cosine between user taste vector and each item
    taste = np.zeros(m.content_matrix.shape[1], dtype=np.float32)
    if played:
        idxs = [m.item_index[i] for i in played if i in m.item_index]
        if idxs:
            taste = m.content_matrix[idxs].sum(axis=0)
            n = np.linalg.norm(taste)
            if n > 0:
                taste = taste / n

    content_score = m.content_matrix @ taste

    # Collaborative score
    if uid in m.user_index:
        uf = m.user_factors[m.user_index[uid]]
        collab_score = m.item_factors @ uf
    else:
        collab_score = np.zeros(len(m.item_ids), dtype=np.float32)
    if collab_score.size:
        lo, hi = float(collab_score.min()), float(collab_score.max())
        if hi > lo:
            collab_score = (collab_score - lo) / (hi - lo)
        else:
            collab_score = np.zeros_like(collab_score)

    score = alpha * content_score + beta * collab_score

    if played:
        for iid in played:
            j = m.item_index.get(iid)
            if j is not None:
                score[j] = -1e9

    # Peer overlap is the second leg of explanations: who plays similar things?
    peer_ids: list[str] = []
    if uid in m.user_index and m.user_factors.shape[0] > 1:
        me_idx = m.user_index[uid]
        me_v = m.user_factors[me_idx]
        nv = np.linalg.norm(me_v) or 1.0
        norms = np.linalg.norm(m.user_factors, axis=1)
        norms[norms == 0] = 1.0
        sims = (m.user_factors @ (me_v / nv)) / norms
        sims[me_idx] = -1.0
        top_peer_idxs = np.argsort(-sims)[:6]
        peer_ids = [
            m.user_ids[int(i)]
            for i in top_peer_idxs
            if sims[int(i)] > 0.05 and m.user_ids[int(i)] in m.user_id_to_name
        ]

    cold = (content_score.max() == 0.0) and (collab_score.max() == 0.0)
    if cold:
        order = sorted(
            range(len(m.item_ids)),
            key=lambda i: (m.item_meta[m.item_ids[i]].rating or 0.0),
            reverse=True,
        )
    else:
        order = np.argsort(-score)

    out: list[dict[str, Any]] = []
    for i in order:
        i = int(i)
        iid = m.item_ids[i]
        if iid in played:
            continue
        meta = m.item_meta[iid]

        reasons: list[dict[str, str]] = []
        # Top content features shared between user taste and this item
        if not cold and taste.any():
            contrib = m.content_matrix[i] * taste
            top_feats = np.argsort(-contrib)[:8]
            for fidx in top_feats:
                fidx = int(fidx)
                if contrib[fidx] <= 1e-6:
                    break
                ft, val = m.feature_keys[fidx]
                kind = _FEATURE_KIND.get(ft, "feature")
                if kind in {"tag", "keyword"}:
                    continue  # too noisy to surface
                if not any(r["label"] == val for r in reasons):
                    reasons.append({"kind": kind, "label": val})
                if len(reasons) >= 3:
                    break

        # Peers who have actually played this item
        if not cold and peer_ids:
            for pid in peer_ids:
                if iid in m.played_by_user.get(pid, set()):
                    name = m.user_id_to_name.get(pid)
                    if name and not any(r["kind"] == "peer" and r["label"] == name for r in reasons):
                        reasons.append({"kind": "peer", "label": name})
                    if len(reasons) >= 5:
                        break

        if not reasons and cold:
            reasons.append({"kind": "highly-rated", "label": "Top community rating"})

        out.append({
            "Id": iid,
            "Name": meta.name,
            "Type": meta.type,
            "SeriesName": meta.series_name,
            "ProductionYear": meta.year,
            "CommunityRating": meta.rating,
            "Overview": (meta.overview or "")[:280],
            "Score": float(score[i]),
            "ContentScore": float(content_score[i]),
            "CollabScore": float(collab_score[i]),
            "Reasons": reasons,
        })
        if len(out) >= limit:
            break

    # Match% — scale top of batch to 99, bottom to 55 so even the last pick
    # in the list still feels relevant ("55% match" beats "0% match").
    if out:
        scores = [p["Score"] for p in out]
        s_max = max(scores)
        s_min = min(scores)
        for p in out:
            if s_max > s_min:
                pct = 55 + 44 * (p["Score"] - s_min) / (s_max - s_min)
            else:
                pct = 75
            p["MatchPct"] = int(max(50, min(99, round(pct))))
    return out


async def top_peers(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Other users whose latent-factor vector is closest in cosine distance."""
    m = await get_model()
    uid = _norm_id(user_id)
    if uid not in m.user_index or m.user_factors.shape[0] < 2:
        return []
    me_idx = m.user_index[uid]
    me = m.user_factors[me_idx]
    me_n = me / (np.linalg.norm(me) or 1.0)
    F = m.user_factors
    norms = np.linalg.norm(F, axis=1)
    norms[norms == 0] = 1.0
    sims = (F @ me_n) / norms
    sims[me_idx] = -1.0
    order = np.argsort(-sims)[:limit]
    return [
        {"UserId": m.user_ids[int(i)], "Similarity": float(sims[int(i)])}
        for i in order
        if sims[int(i)] > 0.05
    ]
