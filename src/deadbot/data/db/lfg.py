from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from deadbot.data.db.models import LfgMember, LfgPost, LfgStatus


async def get_post(s: AsyncSession, post_id: int, *, lock: bool = False) -> LfgPost | None:
    q = select(LfgPost).where(LfgPost.id == post_id)
    if lock:
        q = q.with_for_update()  # serializes join/leave/close on the same post
    return await s.scalar(q)


async def members(s: AsyncSession, post_id: int) -> list[int]:
    rows = await s.scalars(
        select(LfgMember.discord_id)
        .where(LfgMember.post_id == post_id)
        .order_by(LfgMember.joined_at, LfgMember.discord_id)
    )
    return list(rows.all())


async def open_post_for_owner(s: AsyncSession, guild_id: int, owner_id: int) -> LfgPost | None:
    return await s.scalar(
        select(LfgPost).where(
            LfgPost.guild_id == guild_id,
            LfgPost.owner_id == owner_id,
            LfgPost.status == LfgStatus.OPEN,
        )
    )


async def active_post_with_member(
    s: AsyncSession, guild_id: int, discord_id: int
) -> LfgPost | None:
    return await s.scalar(
        select(LfgPost)
        .join(LfgMember, LfgMember.post_id == LfgPost.id)
        .where(
            LfgPost.guild_id == guild_id,
            LfgMember.discord_id == discord_id,
            LfgPost.status.in_([LfgStatus.OPEN, LfgStatus.FULL]),
        )
        .order_by(LfgPost.created_at.desc())
        .limit(1)
    )


async def due_for_expiry(s: AsyncSession, now: datetime) -> list[LfgPost]:
    rows = await s.scalars(
        select(LfgPost)
        .where(LfgPost.status == LfgStatus.OPEN, LfgPost.expires_at <= now)
        .with_for_update(skip_locked=True)
    )
    return list(rows.all())


async def member_count(s: AsyncSession, post_id: int) -> int:
    return (
        await s.scalar(
            select(func.count()).select_from(LfgMember).where(LfgMember.post_id == post_id)
        )
        or 0
    )
