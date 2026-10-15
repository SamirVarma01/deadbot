"""Premade (party) inference. Upstream `party` is always 0, so we combine two signals:

- FRIENDS: the API's same_party mate stats (same team + Steam friends at match start).
- SESSION: two players were teammates in back-to-back matches of one play session.
  Overall same-team ratio is *not* used: at high rank, friends often face each other.
"""

import itertools
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from deadbot.data.db.models import PartySource


@dataclass(frozen=True)
class MatchRef:
    match_id: int
    start_time: int  # unix seconds
    duration_s: int
    team: int


def ordered(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def session_premades(
    own_matches: Iterable[MatchRef],
    teammates_by_match: Mapping[int, set[int]],
    gap_s: int,
) -> dict[int, set[int]]:
    """match_id -> mates who were also teammates in an adjacent match of the same session."""
    ms = sorted(own_matches, key=lambda m: m.start_time)
    out: dict[int, set[int]] = defaultdict(set)
    for prev, cur in itertools.pairwise(ms):
        if cur.start_time - (prev.start_time + prev.duration_s) > gap_s:
            continue
        both = teammates_by_match.get(prev.match_id, set()) & teammates_by_match.get(
            cur.match_id, set()
        )
        if both:
            out[prev.match_id] |= both
            out[cur.match_id] |= both
    return dict(out)


def party_links(
    account_id: int,
    friends_matches: Mapping[int, Iterable[int]],
    session: Mapping[int, set[int]],
) -> dict[tuple[int, int, int], int]:
    """(match_id, account_a, account_b) -> PartySource bitmask."""
    links: dict[tuple[int, int, int], int] = defaultdict(int)
    for mate, matches in friends_matches.items():
        a, b = ordered(account_id, mate)
        for mid in matches:
            links[(mid, a, b)] |= PartySource.FRIENDS
    for mid, mates in session.items():
        for mate in mates:
            a, b = ordered(account_id, mate)
            links[(mid, a, b)] |= PartySource.SESSION
    return dict(links)
