"""Wires settings, clients, and services together. One instance per process."""

from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deadbot.config import Settings
from deadbot.data.cache import Cache
from deadbot.data.db.engine import make_engine, make_sessionmaker
from deadbot.data.deadlock_api import DeadlockAPI
from deadbot.data.steam import SteamClient
from deadbot.services.catalog import Catalog
from deadbot.services.linking import LinkService
from deadbot.services.profile import ProfileService
from deadbot.services.sync import SyncService
from deadbot.services.teammates import TeammatesService


@dataclass
class AppContext:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    cache: Cache
    http: httpx.AsyncClient
    api: DeadlockAPI
    catalog: Catalog
    sync: SyncService
    linking: LinkService
    profile: ProfileService
    teammates: TeammatesService

    @classmethod
    def create(cls, settings: Settings) -> "AppContext":
        engine = make_engine(settings.database_url)
        sessions = make_sessionmaker(engine)
        cache = Cache(Redis.from_url(settings.redis_url))
        http = httpx.AsyncClient(timeout=settings.http_timeout_s)
        api = DeadlockAPI(settings, cache)
        steam_key = settings.steam_api_key.get_secret_value() if settings.steam_api_key else None
        steam = SteamClient(http, steam_key)
        catalog = Catalog(api)
        sync = SyncService(settings, api, sessions)
        return cls(
            settings=settings,
            engine=engine,
            sessions=sessions,
            cache=cache,
            http=http,
            api=api,
            catalog=catalog,
            sync=sync,
            linking=LinkService(steam, api, sessions),
            profile=ProfileService(settings, sessions, cache, catalog),
            teammates=TeammatesService(settings, sessions, cache, catalog),
        )

    async def aclose(self) -> None:
        await self.api.aclose()
        await self.http.aclose()
        await self.cache.close()
        await self.engine.dispose()
