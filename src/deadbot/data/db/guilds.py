from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from deadbot.data.db.models import GuildConfig


async def get_config(s: AsyncSession, guild_id: int) -> GuildConfig:
    """Stored config, or an unsaved default."""
    return await s.get(GuildConfig, guild_id) or GuildConfig(
        guild_id=guild_id, require_verified=True
    )


async def update_config(s: AsyncSession, guild_id: int, **fields: Any) -> GuildConfig:
    """Set the given fields (None is a real value: it clears the setting)."""
    stmt = insert(GuildConfig).values(guild_id=guild_id, **fields)
    if fields:
        stmt = stmt.on_conflict_do_update(index_elements=["guild_id"], set_=fields)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["guild_id"])
    await s.execute(stmt)
    s.expire_all()
    return await get_config(s, guild_id)
