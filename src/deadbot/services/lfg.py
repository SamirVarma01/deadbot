from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deadbot.data.db import lfg as lfg_repo
from deadbot.data.db.models import LfgMember, LfgPost, LfgStatus
from deadbot.services.errors import UserFacingError

MAX_PARTY = 6


@dataclass(frozen=True)
class LfgState:
    post: LfgPost
    members: list[int]  # discord ids, owner first

    @property
    def open_slots(self) -> int:
        return max(0, self.post.capacity - len(self.members))

    @property
    def active(self) -> bool:
        return self.post.status in (LfgStatus.OPEN, LfgStatus.FULL)


class LfgService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self._sessions = sessions

    async def _state(self, s: AsyncSession, post: LfgPost) -> LfgState:
        return LfgState(post, await lfg_repo.members(s, post.id))

    async def create(
        self,
        *,
        guild_id: int,
        channel_id: int,
        owner_id: int,
        slots: int,
        mode: str,
        note: str | None,
        expires_minutes: int,
    ) -> LfgState:
        if not 1 <= slots < MAX_PARTY:
            raise UserFacingError(f"You can look for 1-{MAX_PARTY - 1} players.")
        try:
            async with self._sessions.begin() as s:
                post = LfgPost(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    owner_id=owner_id,
                    capacity=slots + 1,
                    mode=mode,
                    note=note,
                    status=LfgStatus.OPEN,
                    expires_at=datetime.now(UTC) + timedelta(minutes=expires_minutes),
                )
                s.add(post)
                await s.flush()
                s.add(LfgMember(post_id=post.id, discord_id=owner_id))
                await s.flush()
                return await self._state(s, post)
        except IntegrityError as e:
            raise UserFacingError(
                "You already have an open LFG post here. Close it before starting another."
            ) from e

    async def attach_message(self, post_id: int, message_id: int) -> None:
        async with self._sessions.begin() as s:
            post = await lfg_repo.get_post(s, post_id, lock=True)
            if post:
                post.message_id = message_id

    async def get(self, post_id: int) -> LfgState | None:
        async with self._sessions() as s:
            post = await lfg_repo.get_post(s, post_id)
            return await self._state(s, post) if post else None

    async def active_for(self, guild_id: int, discord_id: int) -> LfgState | None:
        async with self._sessions() as s:
            post = await lfg_repo.active_post_with_member(s, guild_id, discord_id)
            return await self._state(s, post) if post else None

    async def join(self, post_id: int, discord_id: int) -> tuple[LfgState, bool]:
        """Returns (state, became_full)."""
        async with self._sessions.begin() as s:
            post = await self._locked(s, post_id)
            members = await lfg_repo.members(s, post_id)
            if discord_id in members:
                raise UserFacingError("You're already in this party.")
            if post.status != LfgStatus.OPEN or post.expires_at <= datetime.now(UTC):
                raise UserFacingError("This LFG post isn't open anymore.")
            if len(members) >= post.capacity:
                raise UserFacingError("This party is already full.")
            s.add(LfgMember(post_id=post_id, discord_id=discord_id))
            became_full = len(members) + 1 >= post.capacity
            if became_full:
                post.status = LfgStatus.FULL
            await s.flush()
            return await self._state(s, post), became_full

    async def leave(self, post_id: int, discord_id: int) -> LfgState:
        async with self._sessions.begin() as s:
            post = await self._locked(s, post_id)
            if discord_id == post.owner_id:
                raise UserFacingError("You own this post. Use **Close** to end it.")
            member = await s.get(LfgMember, (post_id, discord_id))
            if member is None:
                raise UserFacingError("You're not in this party.")
            await s.delete(member)
            if post.status == LfgStatus.FULL:
                # Query before mutating: autoflush would otherwise make this post match itself.
                newer = await lfg_repo.open_post_for_owner(s, post.guild_id, post.owner_id)
                if post.expires_at <= datetime.now(UTC):
                    post.status = LfgStatus.EXPIRED
                elif newer is not None:
                    post.status = LfgStatus.CLOSED  # owner already started a newer post
                else:
                    post.status = LfgStatus.OPEN
            await s.flush()
            return await self._state(s, post)

    async def close(self, post_id: int, discord_id: int, *, force: bool = False) -> LfgState:
        async with self._sessions.begin() as s:
            post = await self._locked(s, post_id)
            if discord_id != post.owner_id and not force:
                raise UserFacingError("Only the post's owner can close it.")
            if post.status in (LfgStatus.OPEN, LfgStatus.FULL):
                post.status = LfgStatus.CLOSED
            await s.flush()
            return await self._state(s, post)

    async def expire_due(self) -> list[LfgState]:
        async with self._sessions.begin() as s:
            posts = await lfg_repo.due_for_expiry(s, datetime.now(UTC))
            for p in posts:
                p.status = LfgStatus.EXPIRED
            await s.flush()
            return [await self._state(s, p) for p in posts]

    @staticmethod
    async def _locked(s: AsyncSession, post_id: int) -> LfgPost:
        post = await lfg_repo.get_post(s, post_id, lock=True)
        if post is None:
            raise UserFacingError("That LFG post no longer exists.")
        return post
