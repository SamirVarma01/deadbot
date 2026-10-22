import re

import httpx
import pytest
import respx

from deadbot.data.steam import ProfileTexts, SteamClient
from deadbot.services.features import blend_hours, manual_hours, normalize
from deadbot.services.linking import new_code
from deadbot.services.recommend import mode_ok, region_ok

STEAM64 = 76561198060730596


def test_manual_hours_converts_local_to_utc():
    # 19:00-23:00 in UTC-5 is 00:00-04:00 UTC
    hours = manual_hours({"start": 19, "end": 23, "utc_offset": -5})
    assert [h for h, v in enumerate(hours) if v] == [0, 1, 2, 3]
    assert sum(hours) == pytest.approx(1.0)


def test_manual_hours_wraps_midnight_and_handles_missing():
    hours = manual_hours({"start": 22, "end": 2})
    assert [h for h, v in enumerate(hours) if v] == [0, 1, 22, 23]
    assert manual_hours({}) is None and manual_hours(None) is None


def test_blend_hours():
    observed = [0] * 24
    observed[20] = 30
    manual = manual_hours({"start": 8, "end": 9})
    blended = blend_hours(observed, manual)
    assert blended[20] == pytest.approx(0.5) and blended[8] == pytest.approx(0.5)
    few = [0] * 24
    few[20] = 3  # too little history: stated availability wins
    assert blend_hours(few, manual) == manual
    assert blend_hours(few, None) == normalize(few)
    assert blend_hours(None, None) is None


def test_mode_and_region_filters():
    assert mode_ok(None, "ranked") and mode_ok("any", "casual") and mode_ok("ranked", None)
    assert not mode_ok("casual", "ranked")
    assert region_ok(None, {"na"}) and region_ok("na", set()) and region_ok("na", {"na", "eu"})
    assert not region_ok("eu", {"na"})


def test_codes_are_unambiguous():
    for _ in range(50):
        assert re.fullmatch(r"DL-[A-HJ-KM-NP-Z2-9]{6}", new_code())


def test_profile_texts_contains_is_case_insensitive():
    assert ProfileTexts("bandos dl-abc234", "").contains("DL-ABC234")
    assert ProfileTexts("bandos", "my code: DL-ABC234").contains("DL-ABC234")
    assert not ProfileTexts("bandos", "").contains("DL-ABC234")


XML = f"""<?xml version="1.0" encoding="UTF-8"?><profile>
<steamID64>{STEAM64}</steamID64><steamID><![CDATA[bandos DL-XYZ789]]></steamID>
<summary><![CDATA[hello]]></summary>
<groups><group><summary><![CDATA[DL-GROUP1]]></summary></group></groups></profile>"""


@respx.mock
async def test_profile_texts_reads_top_level_fields_only():
    route = respx.get(SteamClient.PROFILE_URL.format(steam64=STEAM64)).respond(text=XML)
    async with httpx.AsyncClient() as http:
        texts = await SteamClient(http).profile_texts(STEAM64)
    assert texts == ProfileTexts("bandos DL-XYZ789", "hello")
    assert route.calls.last.request.url.params["xml"] == "1"
    assert not texts.contains("DL-GROUP1")


@respx.mock
async def test_profile_texts_falls_back_to_web_api_name():
    respx.get(SteamClient.PROFILE_URL.format(steam64=STEAM64)).respond(503)
    respx.get(SteamClient.SUMMARIES_URL).respond(
        json={"response": {"players": [{"personaname": "bandos DL-XYZ789"}]}}
    )
    async with httpx.AsyncClient() as http:
        texts = await SteamClient(http, api_key="k").profile_texts(STEAM64)
    assert texts.contains("DL-XYZ789")
