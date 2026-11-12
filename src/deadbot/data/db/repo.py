"""Query/upsert helpers. All take an AsyncSession; callers own the transaction."""

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, and_, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from deadbot.data.db.models import (
    LinkVerification,
    Match,
    MatchPartyLink,
    MatchPlayer,
    PairStat,
    PartySource,
    Player,
    User,
)

_CHUNK = 2000  # asyncpg caps a statement at 32767 bind params


class AccountAlreadyLinkedError(Exception):
    def __init__(self, discord_id: int):
        self.discord_id = discord_id
        super().__init__(f"account already linked to {discord_id}")


def _chunks(rows: Sequence[dict[str, Any]]) -> Iterable[Sequence[dict[str, Any]]]:
    for i in range(0, len(rows), _CHUNK):
        yield rows[i : i + _CHUNK]


# ---- users / players -----------------------------------------------------------------


async def get_user(s: AsyncSession, discord_id: int) -> User | None:
    return await s.get(User, discord_id)


async def get_player(s: AsyncSession, account_id: int) -> Player | None:
    return await s.get(Player, account_id)


async def upsert_players(s: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    """Insert players; on conflict overwrite only the columns provided (non-None)."""
    by_cols: dict[frozenset[str], list[dict[str, Any]]] = {}
    for r in rows:
        r = {k: v for k, v in r.items() if v is not None}
        by_cols.setdefault(frozenset(r), []).append(r)
    for cols, group in by_cols.items():
        for chunk in _chunks(group):
            stmt = insert(Player).values(list(chunk))
            updates = {c: stmt.excluded[c] for c in cols if c != "account_id"}
            stmt = (
                stmt.on_conflict_do_update(index_elements=["account_id"], set_=updates)
                if updates
                else stmt.on_conflict_do_nothing(index_elements=["account_id"])
            )
            await s.execute(stmt)


async def get_user_by_account(s: AsyncSession, account_id: int) -> User | None:
    return await s.scalar(select(User).where(User.account_id == account_id))


async def link_user(
    s: AsyncSession, discord_id: int, account_id: int, *, verified: bool = False
) -> tuple[User, int | None]:
    """Link discord_id -> account_id. A verified link evicts any other holder of the account;
    an unverified one refuses. Returns (user, evicted discord_id or None)."""
    evicted = None
    owner = await get_user_by_account(s, account_id)
    if owner is not None and owner.discord_id != discord_id:
        if not verified:
            raise AccountAlreadyLinkedError(owner.discord_id)
        evicted = owner.discord_id
        await s.delete(owner)
        await s.flush()
    await upsert_players(s, [{"account_id": account_id}])
    user = await s.get(User, discord_id)
    if user is None:
        user = User(discord_id=discord_id, account_id=account_id, verified=False)
        s.add(user)
    elif user.account_id != account_id:
        user.account_id = account_id
        user.linked_at = func.now()
        user.verified = False
        user.verified_at = None
    if verified and not user.verified:
        user.verified = True
        user.verified_at = func.now()
    await s.flush()
    return user, evicted


async def set_verification(
    s: AsyncSession, discord_id: int, account_id: int, code: str, expires_at: datetime
) -> None:
    stmt = insert(LinkVerification).values(
        discord_id=discord_id, account_id=account_id, code=code, expires_at=expires_at
    )
    await s.execute(
        stmt.on_conflict_do_update(
            index_elements=["discord_id"],
            set_={c: stmt.excluded[c] for c in ("account_id", "code", "expires_at")},
        )
    )


async def get_verification(s: AsyncSession, discord_id: int) -> LinkVerification | None:
    return await s.get(LinkVerification, discord_id)


async def unlink_user(s: AsyncSession, discord_id: int) -> bool:
    user = await s.get(User, discord_id)
    if user is None:
        return False
    await s.delete(user)
    return True


async def discord_ids_for_accounts(s: AsyncSession, account_ids: Iterable[int]) -> dict[int, int]:
    ids = list(set(account_ids))
    if not ids:
        return {}
    rows = await s.execute(select(User.account_id, User.discord_id).where(User.account_id.in_(ids)))
    return {a: d for a, d in rows.all()}


async def linked_accounts_due(s: AsyncSession, older_than: datetime, limit: int) -> list[int]:
    rows = await s.scalars(
        select(Player.account_id)
        .join(User, User.account_id == Player.account_id)
        .where(or_(Player.last_synced_at.is_(None), Player.last_synced_at < older_than))
        .order_by(Player.last_synced_at.asc().nulls_first())
        .limit(limit)
    )
    return list(rows)


async def mark_synced(
    s: AsyncSession, account_id: int, at: datetime, error: str | None = None
) -> None:
    values: dict[str, Any] = {"sync_error": error}
    if error is None:
        values["last_synced_at"] = at
    await s.execute(update(Player).where(Player.account_id == account_id).values(**values))


# ---- matches -------------------------------------------------------------------------


async def upsert_matches(s: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    for chunk in _chunks(rows):
        stmt = insert(Match).values(list(chunk))
        stmt = stmt.on_conflict_do_update(
            index_elements=["match_id"],
            set_={"average_badge": func.coalesce(stmt.excluded.average_badge, Match.average_badge)},
        )
        await s.execute(stmt)


async def upsert_match_players(s: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    for chunk in _chunks(rows):
        stmt = insert(MatchPlayer).values(list(chunk))
        cols = ("team", "hero_id", "kills", "deaths", "assists", "won")
        stmt = stmt.on_conflict_do_update(
            index_elements=["match_id", "account_id"],
            set_={c: stmt.excluded[c] for c in cols}
            | {
                # Bulk metadata (kda only) lacks these; keep values from match history.
                "net_worth": func.coalesce(stmt.excluded.net_worth, MatchPlayer.net_worth),
                "last_hits": func.coalesce(stmt.excluded.last_hits, MatchPlayer.last_hits),
            },
        )
        await s.execute(stmt)


async def upsert_party_links(s: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    for chunk in _chunks(rows):
        stmt = insert(MatchPartyLink).values(list(chunk))
        stmt = stmt.on_conflict_do_update(
            index_elements=["match_id", "account_a", "account_b"],
            set_={"sources": MatchPartyLink.sources.op("|")(stmt.excluded.sources)},
        )
        await s.execute(stmt)


async def upsert_pair_stats(s: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    for chunk in _chunks(rows):
        stmt = insert(PairStat).values(list(chunk))
        cols = (
            "games_together",
            "wins_together",
            "premade_games",
            "steam_friends",
            "expected_wr",
            "synergy",
            "last_played_together",
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["account_a", "account_b"],
            set_={c: stmt.excluded[c] for c in cols} | {"updated_at": func.now()},
        )
        await s.execute(stmt)


async def player_match_ids(s: AsyncSession, account_id: int) -> set[int]:
    rows = await s.execute(select(MatchPlayer.match_id).where(MatchPlayer.account_id == account_id))
    return set(rows.scalars())


async def existing_player_matches(
    s: AsyncSession, account_ids: Iterable[int], match_ids: Iterable[int]
) -> set[tuple[int, int]]:
    """(match_id, account_id) pairs already stored."""
    aids, mids = list(set(account_ids)), list(set(match_ids))
    if not aids or not mids:
        return set()
    rows = await s.execute(
        select(MatchPlayer.match_id, MatchPlayer.account_id).where(
            MatchPlayer.account_id.in_(aids), MatchPlayer.match_id.in_(mids)
        )
    )
    return {(m, a) for m, a in rows.all()}


async def teammates_by_match(
    s: AsyncSession, account_id: int, mates: Iterable[int]
) -> dict[int, set[int]]:
    """match_id -> stored mates who were on account_id's team."""
    mate_ids = list(set(mates))
    if not mate_ids:
        return {}
    me, other = aliased(MatchPlayer), aliased(MatchPlayer)
    rows = await s.execute(
        select(me.match_id, other.account_id)
        .join(other, and_(other.match_id == me.match_id, other.team == me.team))
        .where(me.account_id == account_id, other.account_id.in_(mate_ids))
    )
    out: dict[int, set[int]] = {}
    for mid, aid in rows.all():
        out.setdefault(mid, set()).add(aid)
    return out


async def party_link_counts(s: AsyncSession, account_id: int) -> dict[int, tuple[int, int]]:
    """mate -> (matches flagged FRIENDS, matches flagged SESSION) with account_id."""
    mate = MatchPartyLink.account_a + MatchPartyLink.account_b - account_id
    rows = await s.execute(
        select(
            mate,
            func.count().filter(MatchPartyLink.sources.op("&")(PartySource.FRIENDS) != 0),
            func.count().filter(MatchPartyLink.sources.op("&")(PartySource.SESSION) != 0),
        )
        .where(or_(MatchPartyLink.account_a == account_id, MatchPartyLink.account_b == account_id))
        .group_by(mate)
    )
    return {int(m): (int(f), int(sess)) for m, f, sess in rows.all()}


# ---- reports -------------------------------------------------------------------------


async def recent_match_players(
    s: AsyncSession, account_id: int, since: datetime | None = None
) -> list[MatchPlayer]:
    q = select(MatchPlayer).where(MatchPlayer.account_id == account_id)
    if since is not None:
        q = q.where(MatchPlayer.start_time >= since)
    return list(await s.scalars(q.order_by(MatchPlayer.start_time.desc())))


async def pair_stats_for(
    s: AsyncSession, account_id: int, min_games: int
) -> list[tuple[int, PairStat]]:
    """(mate_id, stats) for every stored pair involving account_id."""
    rows = await s.scalars(
        select(PairStat).where(
            or_(PairStat.account_a == account_id, PairStat.account_b == account_id),
            PairStat.games_together >= min_games,
        )
    )
    return [(p.account_b if p.account_a == account_id else p.account_a, p) for p in rows.all()]


async def hero_pairings(
    s: AsyncSession, account_id: int, mate_ids: Sequence[int]
) -> list[tuple[int, int, int, int, int]]:
    """(mate_id, my_hero, mate_hero, games, wins) for shared same-team stored matches."""
    if not mate_ids:
        return []
    me, other = aliased(MatchPlayer), aliased(MatchPlayer)
    won = func.sum(cast(me.won, Integer))
    rows = await s.execute(
        select(other.account_id, me.hero_id, other.hero_id, func.count(), won)
        .join(other, and_(other.match_id == me.match_id, other.team == me.team))
        .where(me.account_id == account_id, other.account_id.in_(list(mate_ids)))
        .group_by(other.account_id, me.hero_id, other.hero_id)
    )
    return [(int(a), int(h1), int(h2), int(g), int(w)) for a, h1, h2, g, w in rows.all()]


async def players_by_id(s: AsyncSession, account_ids: Iterable[int]) -> dict[int, Player]:
    ids = list(set(account_ids))
    if not ids:
        return {}
    rows = await s.scalars(select(Player).where(Player.account_id.in_(ids)))
    return {p.account_id: p for p in rows.all()}


# ---- matching features ---------------------------------------------------------------


async def hour_counts(
    s: AsyncSession, account_ids: Iterable[int], since: datetime
) -> dict[int, list[int]]:
    """account -> games started in each UTC hour since `since`."""
    ids = list(set(account_ids))
    if not ids:
        return {}
    hour = cast(func.extract("hour", func.timezone("UTC", MatchPlayer.start_time)), Integer)
    rows = await s.execute(
        select(MatchPlayer.account_id, hour, func.count())
        .where(MatchPlayer.account_id.in_(ids), MatchPlayer.start_time >= since)
        .group_by(MatchPlayer.account_id, hour)
    )
    out: dict[int, list[int]] = {}
    for aid, h, n in rows.all():
        out.setdefault(aid, [0] * 24)[int(h)] = int(n)
    return out


async def hero_counts(
    s: AsyncSession, account_ids: Iterable[int], since: datetime
) -> dict[int, dict[int, int]]:
    ids = list(set(account_ids))
    if not ids:
        return {}
    rows = await s.execute(
        select(MatchPlayer.account_id, MatchPlayer.hero_id, func.count())
        .where(MatchPlayer.account_id.in_(ids), MatchPlayer.start_time >= since)
        .group_by(MatchPlayer.account_id, MatchPlayer.hero_id)
    )
    out: dict[int, dict[int, int]] = {}
    for aid, hero, n in rows.all():
        out.setdefault(aid, {})[int(hero)] = int(n)
    return out


async def pairs_among(
    s: AsyncSession, account_ids: Iterable[int]
) -> dict[tuple[int, int], tuple[float, int]]:
    """(a, b) -> (synergy, games) for every stored pair inside the given set."""
    ids = list(set(account_ids))
    if len(ids) < 2:
        return {}
    rows = await s.execute(
        select(
            PairStat.account_a, PairStat.account_b, PairStat.synergy, PairStat.games_together
        ).where(PairStat.account_a.in_(ids), PairStat.account_b.in_(ids))
    )
    return {(a, b): (syn or 0.0, g) for a, b, syn, g in rows.all()}


async def users_by_discord(s: AsyncSession, discord_ids: Iterable[int]) -> dict[int, User]:
    ids = list(set(discord_ids))
    out: dict[int, User] = {}
    for i in range(0, len(ids), 5000):  # big servers: keep IN lists bounded
        rows = await s.scalars(select(User).where(User.discord_id.in_(ids[i : i + 5000])))
        out |= {u.discord_id: u for u in rows.all()}
    return out


async def mark_pinged(s: AsyncSession, discord_ids: Iterable[int], at: datetime) -> None:
    ids = list(set(discord_ids))
    if ids:
        await s.execute(update(User).where(User.discord_id.in_(ids)).values(last_pinged_at=at))


async def update_user(s: AsyncSession, discord_id: int, **fields: Any) -> User | None:
    user = await s.get(User, discord_id)
    if user is None:
        return None
    for k, v in fields.items():
        setattr(user, k, v)
    await s.flush()
    return user
