"""phase 2: link verification, guild config, LFG posts, ping preferences

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("users", sa.Column("verified_at", TS))
    op.add_column(
        "users", sa.Column("allow_pings", sa.Boolean(), server_default="true", nullable=False)
    )
    op.add_column("users", sa.Column("last_pinged_at", TS))

    op.create_table(
        "link_verifications",
        sa.Column("discord_id", sa.BigInteger(), primary_key=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
    )

    op.create_table(
        "guild_config",
        sa.Column("guild_id", sa.BigInteger(), primary_key=True),
        sa.Column("lfg_channel_id", sa.BigInteger()),
        sa.Column("ping_role_id", sa.BigInteger()),
        sa.Column("require_verified", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "lfg_posts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger()),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("capacity", sa.SmallInteger(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("note", sa.String(200)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
    )
    op.create_index("ix_lfg_posts_status_expires", "lfg_posts", ["status", "expires_at"])
    op.create_index(
        "uq_lfg_open_owner",
        "lfg_posts",
        ["guild_id", "owner_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "lfg_members",
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("lfg_posts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("discord_id", sa.BigInteger(), primary_key=True),
        sa.Column("joined_at", TS, server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("lfg_members")
    op.drop_table("lfg_posts")
    op.drop_table("guild_config")
    op.drop_table("link_verifications")
    op.drop_column("users", "last_pinged_at")
    op.drop_column("users", "allow_pings")
    op.drop_column("users", "verified_at")
