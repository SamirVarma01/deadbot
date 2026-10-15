import fakeredis
import pytest

from deadbot.config import Settings
from deadbot.data.cache import Cache


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        discord_token="x",
        deadlock_api_key="test-key",
        deadlock_api_base_url="https://api.test",
        http_max_retries=3,
    )


@pytest.fixture
async def cache():
    redis = fakeredis.FakeAsyncRedis()
    c = Cache(redis)
    yield c
    await c.close()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def fake_sleep(sleeps):
    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return _sleep
