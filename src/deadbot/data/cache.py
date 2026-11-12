"""Redis-backed JSON cache. Redis is an optimization: if it is down, calls fall through."""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)


class Cache:
    def __init__(self, redis: Redis | None, prefix: str = "deadbot:"):
        self._redis = redis
        self._prefix = prefix

    async def get_json(self, key: str) -> Any | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._prefix + key)
        except RedisError as e:
            log.warning("cache get failed for %s: %s", key, e)
            return None
        return None if raw is None else json.loads(raw)

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        if self._redis is None or ttl <= 0:
            return
        try:
            await self._redis.set(self._prefix + key, json.dumps(value, default=str), ex=ttl)
        except RedisError as e:
            log.warning("cache set failed for %s: %s", key, e)

    async def delete(self, *keys: str) -> None:
        if self._redis is None or not keys:
            return
        try:
            await self._redis.delete(*(self._prefix + k for k in keys))
        except RedisError as e:
            log.warning("cache delete failed: %s", e)

    async def get_or_set(self, key: str, ttl: int, factory: Callable[[], Awaitable[Any]]) -> Any:
        cached = await self.get_json(key)
        if cached is not None:
            return cached
        value = await factory()
        await self.set_json(key, value, ttl)
        return value

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
