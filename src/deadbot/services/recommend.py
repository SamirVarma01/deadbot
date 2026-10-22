"""Turns Discord context (server members, a party) into ranked recommendations."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deadbot.config import Settings
from deadbot.data.db import guilds, repo
from deadbot.data.db.models import User
from deadbot.services.features import FeatureService
from deadbot.services.matching import (
    PartyFit,
    PlayerFeatures,
    Recommendation,
    Scales,
    Weights,
    greedy_fill,
    party_fit,
)

MODES = ("ranked", "casual")


def mode_ok(preferred: str | None, mode: str | None) -> bool:
    return mode is None or preferred in (None, "any") or preferred == mode


def region_ok(region: str | None, party_regions: set[str]) -> bool:
    return not region or not party_regions or region in party_regions


@dataclass(frozen=True)
class Party:
    members: list[PlayerFeatures]  # linked members
    unlinked: list[int]  # discord ids in the party without a linked account
    regions: set[str]


class RecommendService:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        features: FeatureService,
    ):
        self._s = settings
        self._sessions = sessions
        self._features = features
        self.weights = Weights(
            synergy=settings.match_w_synergy,
            role=settings.match_w_role,
            availability=settings.match_w_availability,
            rank=settings.match_w_rank,
        )
        self.scales = Scales(synergy=settings.synergy_scale, rank_gap=settings.rank_gap_scale)

    async def party(
        self, discord_ids: Sequence[int], online: Mapping[int, bool | None] | None = None
    ) -> Party:
        async with self._sessions() as s:
            users = await repo.users_by_discord(s, discord_ids)
        linked = [users[d] for d in discord_ids if d in users]
        return Party(
            members=await self._features.build(linked, online),
            unlinked=[d for d in discord_ids if d not in users],
            regions={u.region for u in linked if u.region},
        )

    async def eligible(
        self,
        guild_id: int,
        member_ids: Iterable[int],
        exclude: set[int],
        mode: str | None,
        party_regions: set[str],
    ) -> list[User]:
        async with self._sessions() as s:
            cfg = await guilds.get_config(s, guild_id)
            users = await repo.users_by_discord(s, (m for m in member_ids if m not in exclude))
        return sorted(
            (
                u
                for u in users.values()
                if u.opt_in
                and (u.verified or not cfg.require_verified)
                and mode_ok(u.preferred_mode, mode)
                and region_ok(u.region, party_regions)
            ),
            key=lambda u: u.discord_id,
        )

    async def recommend(
        self,
        *,
        guild_id: int,
        member_ids: Iterable[int],
        party_ids: Sequence[int],
        slots: int,
        mode: str | None,
        online: Mapping[int, bool | None] | None = None,
    ) -> tuple[Party, list[Recommendation]]:
        party = await self.party(party_ids, online)
        users = await self.eligible(guild_id, member_ids, set(party_ids), mode, party.regions)
        candidates = await self._features.build(users, online)
        pairs = await self._features.pairs(
            [p.account_id for p in party.members] + [c.account_id for c in candidates]
        )
        picks = greedy_fill(
            candidates,
            party.members,
            slots,
            pairs,
            self.weights,
            self.scales,
            datetime.now(UTC).hour,
        )
        return party, picks

    async def evaluate(
        self, discord_ids: Sequence[int], online: Mapping[int, bool | None] | None = None
    ) -> tuple[Party, PartyFit]:
        party = await self.party(discord_ids, online)
        pairs = await self._features.pairs([p.account_id for p in party.members])
        return party, party_fit(party.members, pairs, self.weights, self.scales)

    async def claim_pings(self, discord_ids: Sequence[int], limit: int) -> list[int]:
        """Of the given (ranked) users, those who allow pings and are off cooldown; marks them."""
        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=self._s.lfg_ping_cooldown_minutes)
        async with self._sessions.begin() as s:
            users = await repo.users_by_discord(s, discord_ids)
            chosen = [
                d
                for d in discord_ids
                if (u := users.get(d))
                and u.allow_pings
                and (u.last_pinged_at is None or u.last_pinged_at < cutoff)
            ][:limit]
            await repo.mark_pinged(s, chosen, now)
        return chosen
