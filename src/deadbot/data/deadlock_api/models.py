"""Typed subsets of the deadlock-api.com response schemas (see openapi.json).

Only fields we use are declared; unknown fields are ignored so upstream additions don't break us.
"""

from enum import IntEnum

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GameMode(IntEnum):
    """Index into the spec's ActiveMatchGameMode enum (match-history returns ints)."""

    INVALID = 0
    NORMAL = 1
    ONE_V_ONE_TEST = 2
    SANDBOX = 3
    STREET_BRAWL = 4


class MatchMode(IntEnum):
    """Index into the spec's ActiveMatchMode enum."""

    INVALID = 0
    UNRANKED = 1
    PRIVATE_LOBBY = 2
    COOP_BOT = 3
    RANKED = 4
    SERVER_TEST = 5
    TUTORIAL = 6
    HERO_LABS = 7
    NEW_PLAYER_PLACEMENT = 8


def team_from_name(name: str | int | None) -> int | None:
    if isinstance(name, int):
        return name
    if name in ("Team0", "Team1"):
        return int(name[-1])
    return None


class MatchHistoryEntry(_Model):
    account_id: int
    match_id: int
    hero_id: int
    hero_level: int
    start_time: int
    game_mode: int
    match_mode: int
    player_team: int
    player_kills: int
    player_deaths: int
    player_assists: int
    denies: int
    net_worth: int
    last_hits: int
    match_duration_s: int
    match_result: int  # the winning team
    ranked_display_badge: int | None = None

    @property
    def won(self) -> bool:
        return self.match_result == self.player_team


class MateStats(_Model):
    mate_id: int
    wins: int
    matches_played: int
    matches: list[int]


class LastRankedMatch(_Model):
    match_id: int
    start_time: int


class RankResponse(_Model):
    badge: int
    rank: int
    subrank: int
    last_match: LastRankedMatch | None = None


class HeroStats(_Model):
    account_id: int
    hero_id: int
    matches_played: int
    wins: int
    kills: int
    deaths: int
    assists: int
    last_played: int
    time_played: int


class SteamProfile(_Model):
    account_id: int
    personaname: str
    profileurl: str
    avatar: str
    avatarmedium: str
    avatarfull: str
    countrycode: str | None = None


class MetadataPlayer(_Model):
    account_id: int
    hero_id: int
    team: str | int
    kills: int = 0
    deaths: int = 0
    assists: int = 0


class MatchMetadata(_Model):
    match_id: int
    start_time: str
    winning_team: str | int | None = None
    duration_s: int
    match_mode: str | int | None = None
    game_mode: str | int | None = None
    average_badge: int | None = None
    players: list[MetadataPlayer] = []


class HeroAsset(_Model):
    id: int
    name: str
    hero_type: str | None = None
    player_selectable: bool = True
    disabled: bool = False


class RankAsset(_Model):
    tier: int
    name: str
