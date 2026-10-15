from deadbot.data.db.models import PartySource
from deadbot.services.premade import MatchRef, ordered, party_links, session_premades

GAP = 90 * 60


def ref(mid, start, dur=1800, team=0):
    return MatchRef(mid, start, dur, team)


def test_back_to_back_teammates_are_premade():
    ms = [ref(1, 0), ref(2, 2000), ref(3, 4000)]
    together = {1: {7, 8}, 2: {7}, 3: {9}}
    out = session_premades(ms, together, GAP)
    assert out == {1: {7}, 2: {7}}


def test_long_gap_breaks_the_session():
    ms = [ref(1, 0), ref(2, 1800 + GAP + 1)]
    assert session_premades(ms, {1: {7}, 2: {7}}, GAP) == {}


def test_input_order_does_not_matter():
    ms = [ref(2, 2000), ref(1, 0)]
    assert session_premades(ms, {1: {7}, 2: {7}}, GAP) == {1: {7}, 2: {7}}


def test_one_off_teammate_is_not_premade():
    ms = [ref(1, 0), ref(2, 2000)]
    assert session_premades(ms, {1: {7}}, GAP) == {}


def test_party_links_order_pairs_and_merge_sources():
    links = party_links(50, {7: [1, 2], 99: [3]}, {2: {7}, 4: {7}})
    assert links[(1, 7, 50)] == PartySource.FRIENDS
    assert links[(2, 7, 50)] == PartySource.FRIENDS | PartySource.SESSION
    assert links[(4, 7, 50)] == PartySource.SESSION
    assert links[(3, 50, 99)] == PartySource.FRIENDS
    assert all(a < b for (_, a, b) in links)


def test_ordered():
    assert ordered(5, 3) == (3, 5) == ordered(3, 5)
