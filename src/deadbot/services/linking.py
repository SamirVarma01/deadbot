from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deadbot.data.db import repo
from deadbot.data.deadlock_api import DeadlockAPI, DeadlockAPIError, ProtectedAccountError
from deadbot.data.steam import SteamClient, SteamResolveError, account_id_to_steam3
from deadbot.services.errors import NotLinkedError, UserFacingError
from deadbot.services.sync import counted


@dataclass(frozen=True)
class LinkResult:
    account_id: int
    persona_name: str | None
    matches: int


class LinkService:
    def __init__(
        self, steam: SteamClient, api: DeadlockAPI, sessions: async_sessionmaker[AsyncSession]
    ):
        self._steam = steam
        self._api = api
        self._sessions = sessions

    async def link(self, discord_id: int, text: str) -> LinkResult:
        try:
            account_id = await self._steam.resolve_account_id(text)
        except SteamResolveError as e:
            raise UserFacingError(str(e)) from e

        try:
            matches = counted(await self._api.match_history(account_id))
        except ProtectedAccountError as e:
            raise UserFacingError(
                "That account has hidden its stats on deadlock-api.com, so it can't be linked."
            ) from e
        except DeadlockAPIError as e:
            raise UserFacingError(
                "The Deadlock stats API isn't responding right now. Please try again in a few "
                "minutes."
            ) from e
        if not matches:
            raise UserFacingError(
                f"Found Steam account `{account_id_to_steam3(account_id)}`, but it has no ranked "
                "or unranked Deadlock matches on record. Check you linked the right account. "
                "New matches can take a while to appear on deadlock-api.com."
            )

        profiles = await self._api.steam_profiles([account_id])
        async with self._sessions.begin() as s:
            try:
                await repo.link_user(s, discord_id, account_id)
            except repo.AccountAlreadyLinkedError as e:
                raise UserFacingError(
                    "That Steam account is already linked to another Discord user. If it's "
                    "yours, ask them to /unlink or contact a server admin."
                ) from e
        return LinkResult(account_id, profiles[0].personaname if profiles else None, len(matches))

    async def unlink(self, discord_id: int) -> bool:
        async with self._sessions.begin() as s:
            return await repo.unlink_user(s, discord_id)

    async def account_for(self, discord_id: int, *, self_lookup: bool) -> int:
        async with self._sessions() as s:
            user = await repo.get_user(s, discord_id)
        if user is None:
            raise NotLinkedError(
                "You haven't linked a Steam account yet. Use `/link` first."
                if self_lookup
                else "That user hasn't linked a Steam account."
            )
        return user.account_id
