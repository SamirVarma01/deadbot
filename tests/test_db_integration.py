"""Runs against a real Postgres (docker compose up -d postgres). Skipped if unreachable.

Uses a separate `deadbot_test` database that is rebuilt for every test.
"""

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deadbot.data.db import guilds, repo
from deadbot.data.db.models import Base, LfgStatus, User
from deadbot.data.steam import ProfileTexts
from deadbot.services.errors import UserFacingError
from deadbot.services.features import FeatureService
from deadbot.services.lfg import LfgService
from deadbot.services.linking import LinkService
from deadbot.services.recommend import RecommendService
from tests.test_sync_helpers import entry

ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", "postgresql+asyncpg://deadbot:deadbot@localhost:5432/deadbot"
)
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + "/deadbot_test"


@pytest.fixture
async def sessions():
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as c:
            exists = await c.scalar(text("SELECT 1 FROM pg_database WHERE datname='deadbot_test'"))
            if not exists:
                await c.execute(text("CREATE DATABASE deadbot_test"))
    except (OSError, Exception) as e:
        pytest.skip(f"Postgres not available: {e}")
    finally:
        await admin.dispose()

    engine = create_async_engine(TEST_URL)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# ---- linking + verification -----------------------------------------------------------


@dataclass
class FakeSteam:
    texts: ProfileTexts = field(default_factory=lambda: ProfileTexts("someone", ""))

    async def resolve_account_id(self, text: str) -> int:
        return int(text)

    async def profile_texts(self, steam64: int) -> ProfileTexts:
        return self.texts


class FakeAPI:
    async def match_history(self, account_id):
        return [entry(1)]

    async def steam_profiles(self, account_ids):
        return []


@pytest.fixture
def linking(sessions, settings):
    steam = FakeSteam()
    return LinkService(settings, steam, FakeAPI(), sessions), steam  # type: ignore[arg-type]


async def test_link_then_verify_takes_over_squatted_account(linking, sessions):
    svc, steam = linking
    squatter = await svc.link(1, "111")
    assert squatter.linked and not squatter.verified and squatter.code

    owner = await svc.link(2, "111")
    assert not owner.linked and owner.code  # can't take it without proof

    with pytest.raises(UserFacingError, match="Couldn't find"):
        await svc.verify(2)

    steam.texts = ProfileTexts("real owner", f"verify {owner.code.lower()}")
    result = await svc.verify(2)
    assert result.evicted_discord_id == 1 and result.account_changed

    async with sessions() as s:
        assert await repo.get_user(s, 1) is None
        user = await repo.get_user(s, 2)
        assert user.account_id == 111 and user.verified and user.verified_at

    again = await svc.link(2, "111")
    assert again.verified and again.code is None
    # Someone else can no longer claim it without verifying, and verified owners stay put.
    third = await svc.link(3, "111")
    assert not third.linked


async def test_relinking_another_account_drops_verification(linking, sessions):
    svc, steam = linking
    first = await svc.link(1, "111")
    steam.texts = ProfileTexts(first.code, "")
    await svc.verify(1)
    await svc.link(1, "222")
    async with sessions() as s:
        user = await repo.get_user(s, 1)
    assert user.account_id == 222 and not user.verified


async def test_expired_code(linking, sessions):
    svc, steam = linking
    r = await svc.link(1, "111")
    async with sessions.begin() as s:
        await repo.set_verification(s, 1, 111, r.code, datetime.now(UTC) - timedelta(minutes=1))
    steam.texts = ProfileTexts(r.code, "")
    with pytest.raises(UserFacingError, match="expired"):
        await svc.verify(1)


# ---- LFG ----------------------------------------------------------------------------------


async def _post(svc: LfgService, owner=1, slots=2):
    return await svc.create(
        guild_id=9,
        channel_id=8,
        owner_id=owner,
        slots=slots,
        mode="ranked",
        note=None,
        expires_minutes=60,
    )


async def test_lfg_lifecycle(sessions):
    svc = LfgService(sessions)
    state = await _post(svc)
    assert state.members == [1] and state.post.capacity == 3 and state.open_slots == 2

    with pytest.raises(UserFacingError, match="already have an open"):
        await _post(svc)

    state, full = await svc.join(state.post.id, 2)
    assert not full and state.members == [1, 2]
    with pytest.raises(UserFacingError, match="already in"):
        await svc.join(state.post.id, 2)
    state, full = await svc.join(state.post.id, 3)
    assert full and state.post.status == LfgStatus.FULL
    with pytest.raises(UserFacingError):
        await svc.join(state.post.id, 4)

    state = await svc.leave(state.post.id, 3)
    assert state.post.status == LfgStatus.OPEN and state.members == [1, 2]
    with pytest.raises(UserFacingError, match="owner"):
        await svc.close(state.post.id, 2)
    with pytest.raises(UserFacingError, match="Close"):
        await svc.leave(state.post.id, 1)

    assert (await svc.active_for(9, 2)).post.id == state.post.id
    state = await svc.close(state.post.id, 1)
    assert state.post.status == LfgStatus.CLOSED
    assert await svc.active_for(9, 2) is None
    await _post(svc)  # closed posts don't block a new one


async def test_lfg_concurrent_joins_never_overfill(sessions):
    svc = LfgService(sessions)
    state = await _post(svc, slots=2)
    results = await asyncio.gather(
        *(svc.join(state.post.id, uid) for uid in range(100, 110)), return_exceptions=True
    )
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 2
    assert all(isinstance(r, UserFacingError) for r in results if isinstance(r, Exception))
    final = await svc.get(state.post.id)
    assert len(final.members) == 3 and final.post.status == LfgStatus.FULL


async def test_lfg_expiry(sessions):
    svc = LfgService(sessions)
    state = await _post(svc)
    async with sessions.begin() as s:
        await s.execute(
            text("UPDATE lfg_posts SET expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": state.post.id},
        )
    expired = await svc.expire_due()
    assert [e.post.id for e in expired] == [state.post.id]
    assert expired[0].post.status == LfgStatus.EXPIRED
    with pytest.raises(UserFacingError, match="isn't open"):
        await svc.join(state.post.id, 5)


# ---- guild config + recommendations ---------------------------------------------------------


async def test_guild_config_defaults_and_updates(sessions):
    async with sessions.begin() as s:
        cfg = await guilds.get_config(s, 9)
        assert cfg.require_verified and cfg.lfg_channel_id is None
        cfg = await guilds.update_config(s, 9, lfg_channel_id=5, ping_role_id=6)
        assert (cfg.lfg_channel_id, cfg.ping_role_id) == (5, 6)
        cfg = await guilds.update_config(s, 9, ping_role_id=None, require_verified=False)
        assert cfg.lfg_channel_id == 5 and cfg.ping_role_id is None and not cfg.require_verified


async def _seed(sessions):
    """Owner 1 (assassin, evenings) + candidates with different traits."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    evening = now.replace(hour=20) - timedelta(days=1)
    players = [
        # discord, account, verified, opt_in, mode, hero (types per catalog stub)
        (1, 101, True, True, None, 13),  # owner: Haze (assassin)
        (2, 102, True, True, None, 6),  # Abrams (brawler), synergy with owner
        (3, 103, False, True, None, 6),  # unverified
        (4, 104, True, False, None, 6),  # opted out
        (5, 105, True, True, "casual", 6),  # prefers casual
        (6, 106, True, True, None, 2),  # Seven (mystic)
    ]
    async with sessions.begin() as s:
        await repo.upsert_players(
            s,
            [{"account_id": a, "rank_badge": 115, "persona_name": f"p{a}"} for _, a, *_ in players],
        )
        for d, a, verified, opt_in, mode, _ in players:
            s.add(
                User(
                    discord_id=d,
                    account_id=a,
                    verified=verified,
                    opt_in=opt_in,
                    preferred_mode=mode,
                )
            )
        await s.flush()
        matches, rows = [], []
        for i in range(12):
            mid = 1000 + i
            matches.append(
                {
                    "match_id": mid,
                    "start_time": evening - timedelta(days=i),
                    "duration_s": 1800,
                    "game_mode": 1,
                    "match_mode": 4,
                    "winning_team": 0,
                    "average_badge": None,
                }
            )
            for _, a, *_, hero in players:
                rows.append(
                    {
                        "match_id": mid,
                        "account_id": a,
                        "team": 0,
                        "hero_id": hero,
                        "kills": 1,
                        "deaths": 1,
                        "assists": 1,
                        "net_worth": None,
                        "last_hits": None,
                        "won": True,
                        "start_time": evening - timedelta(days=i),
                    }
                )
        await repo.upsert_matches(s, matches)
        await repo.upsert_match_players(s, rows)
        await repo.upsert_pair_stats(
            s,
            [
                {
                    "account_a": 101,
                    "account_b": 102,
                    "games_together": 40,
                    "wins_together": 26,
                    "premade_games": 30,
                    "steam_friends": True,
                    "expected_wr": 0.5,
                    "synergy": 0.08,
                    "last_played_together": now,
                }
            ],
        )


class StubCatalog:
    async def hero_types(self):
        return {13: "assassin", 6: "brawler", 2: "mystic"}


async def test_recommend_filters_and_ranks(sessions, settings):
    await _seed(sessions)
    svc = RecommendService(settings, sessions, FeatureService(settings, sessions, StubCatalog()))
    party, picks = await svc.recommend(
        guild_id=9, member_ids=[1, 2, 3, 4, 5, 6, 999], party_ids=[1], slots=5, mode="ranked"
    )
    assert [m.account_id for m in party.members] == [101]
    ids = [r.player.discord_id for r in picks]
    # unverified (3), opted-out (4) and casual-only (5) are excluded; 999 isn't linked
    assert ids == [2, 6]
    assert "covers Brawler" in picks[0].fit.reason and "synergy" in picks[0].fit.reason
    assert all(r.fit.reason for r in picks)

    async with sessions.begin() as s:
        await guilds.update_config(s, 9, require_verified=False)
    _, picks = await svc.recommend(
        guild_id=9, member_ids=[1, 2, 3], party_ids=[1], slots=5, mode="ranked"
    )
    assert {r.player.discord_id for r in picks} == {2, 3}

    _, fit = await svc.evaluate([1, 2, 6])
    assert fit.synergy == pytest.approx(0.08 / 3)
    assert fit.hour_overlap == pytest.approx(1.0)


async def test_ping_cooldown(sessions, settings):
    await _seed(sessions)
    svc = RecommendService(settings, sessions, FeatureService(settings, sessions, StubCatalog()))
    assert await svc.claim_pings([2, 6, 3], limit=2) == [2, 6]
    assert await svc.claim_pings([2, 6, 3], limit=2) == [3]  # 2 and 6 on cooldown
    async with sessions.begin() as s:
        await repo.update_user(s, 3, allow_pings=False, last_pinged_at=None)
    assert await svc.claim_pings([3], limit=2) == []
