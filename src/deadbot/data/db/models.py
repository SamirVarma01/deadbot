from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from deadbot.data.steam import account_id_to_steam64


class Base(DeclarativeBase):
    pass


class PartySource:
    """Bitmask stored in match_party_links.sources."""

    FRIENDS = 1  # API same_party: same team + Steam friends at match start
    SESSION = 2  # same team in back-to-back matches within the session gap


class Player(Base):
    """Any Deadlock account we know about: linked users and their mates."""

    __tablename__ = "players"

    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    persona_name: Mapped[str | None] = mapped_column(String(64))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    rank_badge: Mapped[int | None] = mapped_column(SmallInteger)
    total_games: Mapped[int | None] = mapped_column(Integer)
    total_wins: Mapped[int | None] = mapped_column(Integer)
    last_match_id: Mapped[int | None] = mapped_column(BigInteger)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def win_rate(self) -> float | None:
        if not self.total_games:
            return None
        return (self.total_wins or 0) / self.total_games


class User(Base):
    """A Discord user linked to a Deadlock account."""

    __tablename__ = "users"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.account_id"), unique=True, index=True
    )
    # Set once the user proves control of the Steam account (code in profile name/summary).
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opt_in: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    allow_pings: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_pinged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    region: Mapped[str | None] = mapped_column(String(16))
    preferred_mode: Mapped[str | None] = mapped_column(String(16))
    availability: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    prefs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")

    @property
    def steam_id64(self) -> int:
        return account_id_to_steam64(self.account_id)


class Match(Base):
    __tablename__ = "matches"

    match_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_s: Mapped[int] = mapped_column(Integer)
    game_mode: Mapped[int] = mapped_column(SmallInteger)
    match_mode: Mapped[int] = mapped_column(SmallInteger)
    winning_team: Mapped[int | None] = mapped_column(SmallInteger)
    average_badge: Mapped[int | None] = mapped_column(SmallInteger)


class MatchPlayer(Base):
    __tablename__ = "match_players"

    match_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("matches.match_id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team: Mapped[int] = mapped_column(SmallInteger)
    hero_id: Mapped[int] = mapped_column(SmallInteger)
    kills: Mapped[int] = mapped_column(SmallInteger)
    deaths: Mapped[int] = mapped_column(SmallInteger)
    assists: Mapped[int] = mapped_column(SmallInteger)
    net_worth: Mapped[int | None] = mapped_column(Integer)
    last_hits: Mapped[int | None] = mapped_column(SmallInteger)
    won: Mapped[bool] = mapped_column(Boolean)
    # Copied from matches so per-player time-window queries skip the join.
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_match_players_account_time", "account_id", "start_time"),)


class MatchPartyLink(Base):
    """Evidence that two players queued together in a match (no party IDs exist upstream)."""

    __tablename__ = "match_party_links"

    match_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("matches.match_id", ondelete="CASCADE"), primary_key=True
    )
    account_a: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_b: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sources: Mapped[int] = mapped_column(SmallInteger)  # PartySource bitmask

    __table_args__ = (
        CheckConstraint("account_a < account_b", name="ck_party_link_order"),
        Index("ix_party_links_pair", "account_a", "account_b"),
    )


class PairStat(Base):
    """Same-team stats for a pair, stored once with account_a < account_b."""

    __tablename__ = "pair_stats"

    account_a: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_b: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    games_together: Mapped[int] = mapped_column(Integer)
    wins_together: Mapped[int] = mapped_column(Integer)
    premade_games: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    steam_friends: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    expected_wr: Mapped[float | None] = mapped_column(Float)
    synergy: Mapped[float | None] = mapped_column(Float)
    last_played_together: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("account_a < account_b", name="ck_pair_order"),
        Index("ix_pair_stats_b", "account_b"),
    )


class LinkVerification(Base):
    """A pending ownership challenge: put `code` in the Steam profile name or summary."""

    __tablename__ = "link_verifications"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger)
    code: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GuildConfig(Base):
    __tablename__ = "guild_config"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lfg_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    ping_role_id: Mapped[int | None] = mapped_column(BigInteger)
    # Only suggest members whose Steam link is verified.
    require_verified: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LfgStatus:
    OPEN = "open"
    FULL = "full"
    CLOSED = "closed"
    EXPIRED = "expired"


class LfgPost(Base):
    __tablename__ = "lfg_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    capacity: Mapped[int] = mapped_column(SmallInteger)  # total party size incl. owner
    mode: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default=LfgStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_lfg_posts_status_expires", "status", "expires_at"),
        # One open post per owner per server.
        Index(
            "uq_lfg_open_owner",
            "guild_id",
            "owner_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )


class LfgMember(Base):
    __tablename__ = "lfg_members"

    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("lfg_posts.id", ondelete="CASCADE"), primary_key=True
    )
    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
