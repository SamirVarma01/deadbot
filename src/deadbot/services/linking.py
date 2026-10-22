import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deadbot.config import Settings
from deadbot.data.db import repo
from deadbot.data.deadlock_api import DeadlockAPI, DeadlockAPIError, ProtectedAccountError
from deadbot.data.steam import (
    SteamClient,
    SteamResolveError,
    account_id_to_steam3,
    account_id_to_steam64,
)
from deadbot.services.errors import NotLinkedError, UserFacingError
from deadbot.services.sync import counted

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L


def new_code() -> str:
    return "DL-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


@dataclass(frozen=True)
class LinkResult:
    account_id: int
    persona_name: str | None
    matches: int
    linked: bool  # False: someone else holds the account; verifying will take it over
    verified: bool
    code: str | None  # None when already verified


@dataclass(frozen=True)
class VerifyResult:
    account_id: int
    evicted_discord_id: int | None
    account_changed: bool


class LinkService:
    def __init__(
        self,
        settings: Settings,
        steam: SteamClient,
        api: DeadlockAPI,
        sessions: async_sessionmaker[AsyncSession],
    ):
        self._s = settings
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
        name = profiles[0].personaname if profiles else None
        async with self._sessions.begin() as s:
            try:
                user, _ = await repo.link_user(s, discord_id, account_id)
                linked, verified = True, user.verified
            except repo.AccountAlreadyLinkedError:
                linked, verified = False, False
            code = None
            if not verified:
                code = new_code()
                expires = datetime.now(UTC) + timedelta(minutes=self._s.verify_code_ttl_minutes)
                await repo.set_verification(s, discord_id, account_id, code, expires)
        return LinkResult(account_id, name, len(matches), linked, verified, code)

    async def verify(self, discord_id: int) -> VerifyResult:
        async with self._sessions() as s:
            pending = await repo.get_verification(s, discord_id)
            user = await repo.get_user(s, discord_id)
        if pending is None:
            if user is not None and user.verified:
                raise UserFacingError("Your Steam account is already verified. ✅")
            raise UserFacingError("Start with `/link` to get a verification code.")
        if pending.expires_at < datetime.now(UTC):
            raise UserFacingError("That code expired. Run `/link` again for a new one.")

        try:
            texts = await self._steam.profile_texts(account_id_to_steam64(pending.account_id))
        except httpx.HTTPError as e:
            raise UserFacingError("Steam didn't respond. Try again in a minute.") from e
        if not texts.contains(pending.code):
            raise UserFacingError(
                f"Couldn't find `{pending.code}` in your Steam profile name or About me yet. "
                "Save the change on Steam, wait a few seconds, then try again."
            )

        async with self._sessions.begin() as s:
            _, evicted = await repo.link_user(s, discord_id, pending.account_id, verified=True)
            await s.delete(await repo.get_verification(s, discord_id))
        return VerifyResult(
            pending.account_id,
            evicted,
            account_changed=user is None or user.account_id != pending.account_id,
        )

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
