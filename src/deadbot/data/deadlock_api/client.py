"""Thin async client for https://api.deadlock-api.com.

Paths and parameters come from the published OpenAPI spec. The generated Python client in
deadlock-api/openapi-clients is synchronous (urllib3), so we wrap httpx ourselves.
"""

import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import TypeAdapter

from deadbot.config import Settings
from deadbot.data.cache import Cache
from deadbot.data.deadlock_api.models import (
    HeroAsset,
    HeroStats,
    MatchHistoryEntry,
    MatchMetadata,
    MateStats,
    RankAsset,
    RankResponse,
    SteamProfile,
)

log = logging.getLogger(__name__)

_RETRY_STATUSES = {429, 502, 503, 504}


class DeadlockAPIError(Exception):
    def __init__(self, status: int | None, path: str, detail: str = ""):
        self.status = status
        self.path = path
        super().__init__(f"{path} -> {status}: {detail}"[:300])


class ProtectedAccountError(DeadlockAPIError):
    """The player opted out of public stats on deadlock-api.com (HTTP 403)."""


class SlidingWindowLimiter:
    """At most `limit` acquisitions per `window` seconds."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic):
        self._limit = limit
        self._window = window
        self._clock = clock
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= self._window:
                    self._stamps.popleft()
                if len(self._stamps) < self._limit:
                    self._stamps.append(now)
                    return
                await asyncio.sleep(self._window - (now - self._stamps[0]))


def _chunks[T](items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _encode_params(params: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, list | tuple | set):
            out[k] = ",".join(str(x) for x in v)  # the API takes comma-separated lists
        else:
            out[k] = str(v)
    return out


def _retry_after_seconds(r: httpx.Response) -> float | None:
    value = r.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class DeadlockAPI:
    def __init__(
        self,
        settings: Settings,
        cache: Cache,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._s = settings
        self._cache = cache
        headers = {"User-Agent": "deadbot (discord teammate finder)"}
        if settings.deadlock_api_key:
            headers["X-API-KEY"] = settings.deadlock_api_key.get_secret_value()
        self._http = http or httpx.AsyncClient(timeout=settings.http_timeout_s)
        self._headers = headers
        self._sleep = sleep
        self._metadata_limiter = SlidingWindowLimiter(settings.bulk_metadata_per_minute, 60.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- core request path -------------------------------------------------------------

    def _backoff(self, attempt: int) -> float:
        return min(self._s.http_max_backoff_s, 2**attempt) + random.uniform(0, 0.5)

    async def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        limiter: SlidingWindowLimiter | None = None,
        accept_429_body: bool = False,
        not_found_ok: bool = False,
    ) -> Any:
        url = self._s.deadlock_api_base_url.rstrip("/") + path
        query = _encode_params(params or {})
        last_status: int | None = None
        for attempt in range(self._s.http_max_retries + 1):
            if limiter:
                await limiter.acquire()
            try:
                r = await self._http.get(url, params=query, headers=self._headers)
            except httpx.TransportError as e:
                log.warning("GET %s failed (%s), attempt %d", path, e, attempt + 1)
                last_status, delay = None, self._backoff(attempt)
            else:
                if r.is_success:
                    return r.json()
                last_status = r.status_code
                if r.status_code == 429 and accept_429_body and r.content:
                    # match-history answers 429 with the stored history when the live Steam
                    # refetch is rate limited; that is still a usable result.
                    return r.json()
                if r.status_code == 404 and not_found_ok:
                    return None
                if r.status_code == 403:
                    raise ProtectedAccountError(403, path, r.text[:200])
                if r.status_code not in _RETRY_STATUSES:
                    raise DeadlockAPIError(r.status_code, path, r.text[:200])
                delay = _retry_after_seconds(r) or self._backoff(attempt)
                log.info("GET %s -> %d, retrying in %.1fs", path, r.status_code, delay)
            if attempt < self._s.http_max_retries:
                await self._sleep(min(delay, self._s.http_max_backoff_s))
        raise DeadlockAPIError(last_status, path, "retries exhausted")

    async def _get(self, path: str, params: dict[str, Any] | None, ttl: int, **kw: Any) -> Any:
        key = f"api:{path}?{urlencode(sorted(_encode_params(params or {}).items()))}"
        cached = await self._cache.get_json(key)
        if cached is not None:
            return cached
        data = await self._request(path, params, **kw)
        if data is not None:
            await self._cache.set_json(key, data, ttl)
        return data

    # ---- endpoints ---------------------------------------------------------------------

    async def match_history(self, account_id: int) -> list[MatchHistoryEntry]:
        data = await self._get(
            f"/v1/players/{account_id}/match-history",
            None,
            self._s.ttl_match_history,
            accept_429_body=True,
        )
        return TypeAdapter(list[MatchHistoryEntry]).validate_python(data or [])

    async def mate_stats(
        self, account_id: int, *, min_matches_played: int | None = None, same_party: bool = False
    ) -> list[MateStats]:
        data = await self._get(
            f"/v1/players/{account_id}/mate-stats",
            {"min_matches_played": min_matches_played, "same_party": same_party},
            self._s.ttl_mate_stats,
        )
        return TypeAdapter(list[MateStats]).validate_python(data or [])

    async def rank(self, account_id: int) -> RankResponse:
        data = await self._get(f"/v1/players/{account_id}/rank", None, self._s.ttl_rank)
        return RankResponse.model_validate(data)

    async def hero_stats(
        self, account_ids: Sequence[int], *, min_unix_timestamp: int | None = None
    ) -> list[HeroStats]:
        out: list[HeroStats] = []
        for chunk in _chunks(sorted(set(account_ids)), 200):
            data = await self._get(
                "/v1/players/hero-stats",
                {"account_ids": list(chunk), "min_unix_timestamp": min_unix_timestamp},
                self._s.ttl_hero_stats,
            )
            out.extend(TypeAdapter(list[HeroStats]).validate_python(data or []))
        return out

    async def steam_profiles(self, account_ids: Sequence[int]) -> list[SteamProfile]:
        out: list[SteamProfile] = []
        for chunk in _chunks(sorted(set(account_ids)), 200):
            data = await self._get(
                "/v1/players/steam",
                {"account_ids": list(chunk)},
                self._s.ttl_steam_profiles,
                not_found_ok=True,
            )
            out.extend(TypeAdapter(list[SteamProfile]).validate_python(data or []))
        return out

    async def bulk_metadata(
        self, match_ids: Sequence[int], account_ids: Sequence[int]
    ) -> list[MatchMetadata]:
        """Per-match hero/team/KDA for just `account_ids` in the given matches."""
        out: list[MatchMetadata] = []
        for chunk in _chunks(sorted(set(match_ids)), self._s.bulk_metadata_chunk):
            data = await self._get(
                "/v1/matches/metadata",
                {
                    "match_ids": list(chunk),
                    "account_ids": sorted(set(account_ids)),
                    "only_filtered_players": True,
                    "include_info": True,
                    "include_player_kda": True,
                    "limit": len(chunk),
                },
                self._s.ttl_assets,  # finished matches never change
                limiter=self._metadata_limiter,
                not_found_ok=True,  # 404 = none of these matches have metadata upstream
            )
            out.extend(TypeAdapter(list[MatchMetadata]).validate_python(data or []))
        return out

    async def heroes(self) -> list[HeroAsset]:
        data = await self._get("/v1/assets/heroes", {"only_active": True}, self._s.ttl_assets)
        return TypeAdapter(list[HeroAsset]).validate_python(data or [])

    async def ranks(self) -> list[RankAsset]:
        data = await self._get("/v1/assets/ranks", None, self._s.ttl_assets)
        return TypeAdapter(list[RankAsset]).validate_python(data or [])
