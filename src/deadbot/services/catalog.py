"""Hero and rank lookups from the API's asset endpoints (cached by the client)."""

from deadbot.data.deadlock_api import DeadlockAPI


class Catalog:
    def __init__(self, api: DeadlockAPI):
        self._api = api

    async def hero_names(self) -> dict[int, str]:
        return {h.id: h.name for h in await self._api.heroes()}

    async def hero_types(self) -> dict[int, str]:
        return {h.id: h.hero_type for h in await self._api.heroes() if h.hero_type}

    async def rank_label(self, badge: int | None) -> str | None:
        """Badge is tier*10 + subrank; 0/None means unranked or in placements."""
        if not badge:
            return None
        tier, sub = divmod(badge, 10)
        names = {r.tier: r.name for r in await self._api.ranks()}
        name = names.get(tier, f"Tier {tier}")
        return f"{name} {sub}" if sub else name
