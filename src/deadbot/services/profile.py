from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deadbot.config import Settings
from deadbot.data.cache import Cache
from deadbot.data.db import repo
from deadbot.services.catalog import Catalog
from deadbot.services.errors import NotSyncedError, TooFewMatchesError
from deadbot.services.playstyle import busiest_days, hero_lines, peak_window, role_mix


class HeroLine(BaseModel):
    hero_id: int
    name: str
    games: int
    wins: int
    kills: int
    deaths: int
    assists: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.games

    @property
    def kda(self) -> float:
        return (self.kills + self.assists) / max(1, self.deaths)


class ProfileReport(BaseModel):
    account_id: int
    persona_name: str | None
    avatar_url: str | None
    rank_label: str | None
    total_games: int
    total_wins: int
    window_days: int
    window_games: int
    window_wins: int
    top_heroes: list[HeroLine]
    roles: list[tuple[str, float]]
    peak_start_hour_utc: int | None
    peak_share: float | None
    busiest_days: list[str]
    last_played: datetime | None
    last_synced_at: datetime


class ProfileService:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        cache: Cache,
        catalog: Catalog,
    ):
        self._s = settings
        self._sessions = sessions
        self._cache = cache
        self._catalog = catalog

    async def build(self, account_id: int) -> ProfileReport:
        async with self._sessions() as s:
            player = await repo.get_player(s, account_id)
            if player is None or player.last_synced_at is None:
                raise NotSyncedError(
                    "Match history is still syncing. Try again in a minute or two."
                )
            key = f"report:profile:{account_id}:{int(player.last_synced_at.timestamp())}"
            if cached := await self._cache.get_json(key):
                return ProfileReport.model_validate(cached)
            rows = await repo.recent_match_players(s, account_id)

        if len(rows) < self._s.profile_min_games:
            raise TooFewMatchesError(
                f"Only {len(rows)} ranked/unranked match(es) on record. A profile needs at "
                f"least {self._s.profile_min_games}. Play a few more and check back!"
            )

        since = datetime.now(UTC) - timedelta(days=self._s.recent_window_days)
        recent = [r for r in rows if r.start_time >= since]
        # Describe current habits; fall back to all-time for players who took a break.
        basis = recent if len(recent) >= self._s.profile_min_games else rows

        names = await self._catalog.hero_names()
        lines = hero_lines(basis)
        peak = peak_window([r.start_time for r in basis])
        report = ProfileReport(
            account_id=account_id,
            persona_name=player.persona_name,
            avatar_url=player.avatar_url,
            rank_label=await self._catalog.rank_label(player.rank_badge),
            total_games=len(rows),
            total_wins=sum(r.won for r in rows),
            window_days=self._s.recent_window_days if basis is recent else 0,
            window_games=len(basis),
            window_wins=sum(r.won for r in basis),
            top_heroes=[
                HeroLine(name=names.get(h["hero_id"], f"Hero {h['hero_id']}"), **h)
                for h in lines[:5]
            ],
            roles=role_mix(
                {h["hero_id"]: h["games"] for h in lines}, await self._catalog.hero_types()
            ),
            peak_start_hour_utc=peak[0] if peak else None,
            peak_share=peak[1] if peak else None,
            busiest_days=busiest_days([r.start_time for r in basis]),
            last_played=rows[0].start_time if rows else None,
            last_synced_at=player.last_synced_at,
        )
        await self._cache.set_json(key, report.model_dump(mode="json"), self._s.ttl_reports)
        return report
