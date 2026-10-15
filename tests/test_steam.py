import httpx
import pytest
import respx

from deadbot.data.steam import (
    STEAM64_BASE,
    InputKind,
    SteamClient,
    SteamResolveError,
    account_id_to_steam3,
    account_id_to_steam64,
    parse_steam_input,
    steam64_to_account_id,
)

STEAM64 = 76561198060730596
ACCOUNT = 100464868


def test_conversions_round_trip():
    assert steam64_to_account_id(STEAM64) == ACCOUNT
    assert account_id_to_steam64(ACCOUNT) == STEAM64
    assert account_id_to_steam3(ACCOUNT) == "[U:1:100464868]"


@pytest.mark.parametrize("bad", [0, -1, 2**32])
def test_account_id_bounds(bad):
    with pytest.raises(ValueError):
        account_id_to_steam64(bad)


def test_steam64_bounds():
    with pytest.raises(ValueError):
        steam64_to_account_id(STEAM64_BASE)


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        (f"https://steamcommunity.com/profiles/{STEAM64}/", InputKind.STEAM64, str(STEAM64)),
        (f"steamcommunity.com/profiles/{STEAM64}", InputKind.STEAM64, str(STEAM64)),
        (f"<https://steamcommunity.com/profiles/{STEAM64}>", InputKind.STEAM64, str(STEAM64)),
        ("https://steamcommunity.com/id/gabelogannewell/", InputKind.VANITY, "gabelogannewell"),
        ("http://steamcommunity.com/id/Some_Name-1", InputKind.VANITY, "Some_Name-1"),
        (str(STEAM64), InputKind.STEAM64, str(STEAM64)),
        (str(ACCOUNT), InputKind.ACCOUNT_ID, str(ACCOUNT)),
        ("[U:1:100464868]", InputKind.ACCOUNT_ID, str(ACCOUNT)),
        ("U:1:100464868", InputKind.ACCOUNT_ID, str(ACCOUNT)),
        ("STEAM_0:0:50232434", InputKind.ACCOUNT_ID, str(ACCOUNT)),
        ("STEAM_1:1:5", InputKind.ACCOUNT_ID, "11"),
        ("  gabelogannewell  ", InputKind.VANITY, "gabelogannewell"),
    ],
)
def test_parse_steam_input(text, kind, value):
    parsed = parse_steam_input(text)
    assert (parsed.kind, parsed.value) == (kind, value)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "https://steamcommunity.com/groups/foo",
        "has spaces in it",
        "a",
        "99999999999999999999",
    ],
)
def test_parse_rejects_garbage(text):
    with pytest.raises(SteamResolveError):
        parse_steam_input(text)


@respx.mock
async def test_resolve_vanity_with_api_key():
    route = respx.get(SteamClient.API_URL).respond(
        json={"response": {"success": 1, "steamid": str(STEAM64)}}
    )
    async with httpx.AsyncClient() as http:
        client = SteamClient(http, api_key="k")
        assert await client.resolve_account_id("https://steamcommunity.com/id/bandos") == ACCOUNT
    assert route.calls.last.request.url.params["vanityurl"] == "bandos"
    assert route.calls.last.request.url.params["key"] == "k"


@respx.mock
async def test_resolve_vanity_no_match_with_api_key():
    respx.get(SteamClient.API_URL).respond(
        json={"response": {"success": 42, "message": "No match"}}
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(SteamResolveError, match="No Steam profile"):
            await SteamClient(http, api_key="k").resolve_account_id("nobody_here")


@respx.mock
async def test_resolve_vanity_xml_fallback_without_key():
    xml = f"<?xml version='1.0'?><profile><steamID64>{STEAM64}</steamID64></profile>"
    respx.get("https://steamcommunity.com/id/bandos/").respond(text=xml)
    async with httpx.AsyncClient() as http:
        assert await SteamClient(http).resolve_account_id("bandos") == ACCOUNT


@respx.mock
async def test_resolve_vanity_xml_error_page_means_not_found():
    xml = (
        "<?xml version='1.0'?><response>"
        "<error>The specified profile could not be found.</error></response>"
    )
    respx.get("https://steamcommunity.com/id/ghost/").respond(text=xml)
    async with httpx.AsyncClient() as http:
        with pytest.raises(SteamResolveError, match="No Steam profile"):
            await SteamClient(http).resolve_account_id("ghost")


@respx.mock
async def test_resolve_vanity_network_failure_is_user_facing():
    respx.get(SteamClient.API_URL).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(SteamResolveError, match="didn't respond"):
            await SteamClient(http, api_key="k").resolve_account_id("bandos")


async def test_numeric_inputs_need_no_network():
    async with httpx.AsyncClient() as http:
        client = SteamClient(http)
        assert await client.resolve_account_id(str(STEAM64)) == ACCOUNT
        assert await client.resolve_account_id(str(ACCOUNT)) == ACCOUNT
