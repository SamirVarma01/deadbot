"""initial schema: players, users, matches, match_players, party links, pair stats

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # pgvector is used from Phase 3 (player embeddings); enable it up front.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "players",
        sa.Column("account_id", sa.BigInteger(), primary_key=True),
        sa.Column("persona_name", sa.String(64)),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("rank_badge", sa.SmallInteger()),
        sa.Column("total_games", sa.Integer()),
        sa.Column("total_wins", sa.Integer()),
        sa.Column("last_match_id", sa.BigInteger()),
        sa.Column("last_synced_at", TS),
        sa.Column("sync_error", sa.Text()),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("discord_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "account_id", sa.BigInteger(), sa.ForeignKey("players.account_id"), nullable=False
        ),
        sa.Column("verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("linked_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("opt_in", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("region", sa.String(16)),
        sa.Column("preferred_mode", sa.String(16)),
        sa.Column("availability", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("prefs", postgresql.JSONB(), server_default="{}", nullable=False),
    )
    op.create_index("ix_users_account_id", "users", ["account_id"], unique=True)

    op.create_table(
        "matches",
        sa.Column("match_id", sa.BigInteger(), primary_key=True),
        sa.Column("start_time", TS, nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("game_mode", sa.SmallInteger(), nullable=False),
        sa.Column("match_mode", sa.SmallInteger(), nullable=False),
        sa.Column("winning_team", sa.SmallInteger()),
        sa.Column("average_badge", sa.SmallInteger()),
    )
    op.create_index("ix_matches_start_time", "matches", ["start_time"])

    op.create_table(
        "match_players",
        sa.Column(
            "match_id",
            sa.BigInteger(),
            sa.ForeignKey("matches.match_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("account_id", sa.BigInteger(), primary_key=True),
        sa.Column("team", sa.SmallInteger(), nullable=False),
        sa.Column("hero_id", sa.SmallInteger(), nullable=False),
        sa.Column("kills", sa.SmallInteger(), nullable=False),
        sa.Column("deaths", sa.SmallInteger(), nullable=False),
        sa.Column("assists", sa.SmallInteger(), nullable=False),
        sa.Column("net_worth", sa.Integer()),
        sa.Column("last_hits", sa.SmallInteger()),
        sa.Column("won", sa.Boolean(), nullable=False),
        sa.Column("start_time", TS, nullable=False),
    )
    op.create_index("ix_match_players_account_time", "match_players", ["account_id", "start_time"])

    op.create_table(
        "match_party_links",
        sa.Column(
            "match_id",
            sa.BigInteger(),
            sa.ForeignKey("matches.match_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("account_a", sa.BigInteger(), primary_key=True),
        sa.Column("account_b", sa.BigInteger(), primary_key=True),
        sa.Column("sources", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("account_a < account_b", name="ck_party_link_order"),
    )
    op.create_index("ix_party_links_pair", "match_party_links", ["account_a", "account_b"])

    op.create_table(
        "pair_stats",
        sa.Column("account_a", sa.BigInteger(), primary_key=True),
        sa.Column("account_b", sa.BigInteger(), primary_key=True),
        sa.Column("games_together", sa.Integer(), nullable=False),
        sa.Column("wins_together", sa.Integer(), nullable=False),
        sa.Column("premade_games", sa.Integer(), server_default="0", nullable=False),
        sa.Column("steam_friends", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("expected_wr", sa.Float()),
        sa.Column("synergy", sa.Float()),
        sa.Column("last_played_together", TS),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("account_a < account_b", name="ck_pair_order"),
    )
    op.create_index("ix_pair_stats_b", "pair_stats", ["account_b"])


def downgrade() -> None:
    op.drop_table("pair_stats")
    op.drop_table("match_party_links")
    op.drop_table("match_players")
    op.drop_table("matches")
    op.drop_table("users")
    op.drop_table("players")
