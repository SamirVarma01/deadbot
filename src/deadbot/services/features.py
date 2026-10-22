"""Builds matching inputs (PlayerFeatures, pair lookups) from stored data."""

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deadbot.config import Settings
from deadbot.data.db import repo
from deadbot.data.db.models import User
from deadbot.services.catalog import Catalog
from deadbot.services.matching import PairInfo, PairLookup, PlayerFeatures
from deadbot.services.playstyle import role_mix

_MIN_OBSERVED_GAMES = 10


def normalize(counts: Sequence[int]) -> tuple[float, ...] | None:
    total = sum(counts)
    return tuple(c / total for c in counts) if total else None


def manual_hours(availability: Mapping[str, Any] | None) -> tuple[float, ...] | None:
    """{"start": 19, "end": 23, "utc_offset": -5} (local hours, end exclusive) -> UTC shares."""
    if not availability or "start" not in availability or "end" not in availability:
        return None
    start, end = int(availability["start"]) % 24, int(availability["end"]) % 24
    offset = int(availability.get("utc_offset", 0))
    span = (end - start) % 24 or 24
    counts = [0] * 24
    for i in range(span):
        counts[(start + i - offset) % 24] = 1
    return normalize(counts)


def blend_hours(
    observed: Sequence[int] | None, manual: tuple[float, ...] | None
) -> tuple[float, ...] | None:
    """Stated availability wins for players with little history; otherwise split 50/50."""
    obs = normalize(observed) if observed and sum(observed) >= _MIN_OBSERVED_GAMES else None
    if obs and manual:
        return tuple((a + b) / 2 for a, b in zip(obs, manual, strict=True))
    return manual or obs or (normalize(observed) if observed else None)


class FeatureService:
    def __init__(
        self, settings: Settings, sessions: async_sessionmaker[AsyncSession], catalog: Catalog
    ):
        self._s = settings
        self._sessions = sessions
        self._catalog = catalog

    async def build(
        self, users: Sequence[User], online: Mapping[int, bool | None] | None = None
    ) -> list[PlayerFeatures]:
        """Features for linked users, in the given order. `online` is keyed by discord id."""
        online = online or {}
        ids = [u.account_id for u in users]
        since = datetime.now(UTC) - timedelta(days=self._s.recent_window_days)
        async with self._sessions() as s:
            players = await repo.players_by_id(s, ids)
            hours = await repo.hour_counts(s, ids, since)
            heroes = await repo.hero_counts(s, ids, since)
            # Fall back to all-time for anyone who barely played recently.
            stale = [a for a in ids if sum(heroes.get(a, {}).values()) < _MIN_OBSERVED_GAMES]
            if stale:
                epoch = datetime(2020, 1, 1, tzinfo=UTC)
                hours |= await repo.hour_counts(s, stale, epoch)
                heroes |= await repo.hero_counts(s, stale, epoch)
        types = await self._catalog.hero_types()

        out = []
        for u in users:
            p = players.get(u.account_id)
            hero_games = Counter(heroes.get(u.account_id, {}))
            out.append(
                PlayerFeatures(
                    account_id=u.account_id,
                    discord_id=u.discord_id,
                    name=(p.persona_name if p else None) or f"[U:1:{u.account_id}]",
                    rank_badge=p.rank_badge if p else None,
                    hours=blend_hours(hours.get(u.account_id), manual_hours(u.availability)),
                    roles=dict(role_mix(hero_games, types)),
                    games=sum(hero_games.values()),
                    online=online.get(u.discord_id),
                )
            )
        return out

    async def pairs(self, account_ids: Sequence[int]) -> PairLookup:
        async with self._sessions() as s:
            raw = await repo.pairs_among(s, account_ids)
        return {k: PairInfo(syn, games) for k, (syn, games) in raw.items()}
