from redis.exceptions import ConnectionError as RedisConnectionError

from deadbot.config import Settings
from deadbot.data.cache import Cache


def test_database_url_gets_async_driver():
    for url in ("postgres://u:p@h:5432/db", "postgresql://u:p@h:5432/db"):
        s = Settings(_env_file=None, database_url=url)
        assert s.database_url == "postgresql+asyncpg://u:p@h:5432/db"


def test_empty_env_values_are_ignored(monkeypatch):
    monkeypatch.setenv("DEV_GUILD_ID", "")
    monkeypatch.setenv("DEADLOCK_API_KEY", "")
    s = Settings(_env_file=None)
    assert s.dev_guild_id is None
    assert s.deadlock_api_key is None


async def test_get_or_set_calls_factory_once(cache):
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return {"v": 1}

    assert await cache.get_or_set("k", 60, factory) == {"v": 1}
    assert await cache.get_or_set("k", 60, factory) == {"v": 1}
    assert calls == 1


class _DeadRedis:
    async def get(self, *a, **k):
        raise RedisConnectionError("down")

    async def set(self, *a, **k):
        raise RedisConnectionError("down")


async def test_cache_degrades_when_redis_is_down():
    c = Cache(_DeadRedis())  # type: ignore[arg-type]
    assert await c.get_json("k") is None
    await c.set_json("k", {"v": 1}, 60)  # must not raise
