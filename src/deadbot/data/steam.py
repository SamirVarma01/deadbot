"""Steam ID parsing/conversion and vanity URL resolution.

Deadlock APIs key players by *account ID* (the 32-bit number inside SteamID3 ``[U:1:N]``).
SteamID64 = account ID + STEAM64_BASE.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import StrEnum

import httpx

STEAM64_BASE = 76561197960265728
_MAX_ACCOUNT_ID = 2**32 - 1

_PROFILE_URL = re.compile(r"steamcommunity\.com/profiles/(\d{17})", re.I)
_VANITY_URL = re.compile(r"steamcommunity\.com/id/([A-Za-z0-9_-]{2,32})", re.I)
_STEAM3 = re.compile(r"^\[?U:1:(\d+)\]?$", re.I)
_STEAM2 = re.compile(r"^STEAM_[0-5]:([01]):(\d+)$", re.I)
_VANITY = re.compile(r"^[A-Za-z0-9_-]{2,32}$")


class SteamResolveError(Exception):
    """Raised with a message that is safe to show to the user."""


def account_id_to_steam64(account_id: int) -> int:
    if not 0 < account_id <= _MAX_ACCOUNT_ID:
        raise ValueError(f"invalid account id: {account_id}")
    return account_id + STEAM64_BASE


def steam64_to_account_id(steam64: int) -> int:
    account_id = steam64 - STEAM64_BASE
    if not 0 < account_id <= _MAX_ACCOUNT_ID:
        raise ValueError(f"invalid SteamID64: {steam64}")
    return account_id


def account_id_to_steam3(account_id: int) -> str:
    return f"[U:1:{account_id}]"


class InputKind(StrEnum):
    STEAM64 = "steam64"
    ACCOUNT_ID = "account_id"
    VANITY = "vanity"


@dataclass(frozen=True)
class ParsedSteamInput:
    kind: InputKind
    value: str


def parse_steam_input(text: str) -> ParsedSteamInput:
    """Classify user input as a SteamID64, account ID, or vanity name. Pure; no network."""
    s = text.strip().strip("<>").rstrip("/")
    if not s:
        raise SteamResolveError("Please provide a Steam profile URL, vanity name, or SteamID64.")

    if m := _PROFILE_URL.search(s):
        return ParsedSteamInput(InputKind.STEAM64, m.group(1))
    if m := _VANITY_URL.search(s):
        return ParsedSteamInput(InputKind.VANITY, m.group(1))
    if "steamcommunity.com" in s.lower():
        raise SteamResolveError(
            "That doesn't look like a Steam profile URL (/id/... or /profiles/...)."
        )
    if m := _STEAM3.match(s):
        return ParsedSteamInput(InputKind.ACCOUNT_ID, m.group(1))
    if m := _STEAM2.match(s):
        y, z = int(m.group(1)), int(m.group(2))
        return ParsedSteamInput(InputKind.ACCOUNT_ID, str(z * 2 + y))
    if s.isdigit():
        n = int(s)
        if n > STEAM64_BASE and len(s) == 17:
            return ParsedSteamInput(InputKind.STEAM64, s)
        if 0 < n <= _MAX_ACCOUNT_ID:
            return ParsedSteamInput(InputKind.ACCOUNT_ID, s)
        raise SteamResolveError(f"`{s}` isn't a valid SteamID64 or account ID.")
    if _VANITY.match(s):
        return ParsedSteamInput(InputKind.VANITY, s)
    raise SteamResolveError(f"Couldn't understand `{s}` as a Steam profile.")


@dataclass(frozen=True)
class ProfileTexts:
    name: str
    summary: str

    def contains(self, code: str) -> bool:
        code = code.upper()
        return code in self.name.upper() or code in self.summary.upper()


class SteamClient:
    """Resolves vanity names via ISteamUser/ResolveVanityURL, or the public profile XML
    when no Steam Web API key is configured."""

    API_URL = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
    XML_URL = "https://steamcommunity.com/id/{vanity}/?xml=1"
    PROFILE_URL = "https://steamcommunity.com/profiles/{steam64}/"
    SUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"

    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None):
        self._http = http
        self._api_key = api_key

    async def _web_api(self, url: str, **params: object) -> dict:
        r = await self._http.get(url, params={"key": self._api_key, **params})
        if r.is_error:
            # httpx's own message includes the URL, which carries the API key.
            raise httpx.HTTPStatusError(
                f"Steam Web API returned {r.status_code}", request=r.request, response=r
            )
        return r.json().get("response", {})

    async def resolve_vanity(self, vanity: str) -> int | None:
        if self._api_key:
            body = await self._web_api(self.API_URL, vanityurl=vanity)
            # success: 1 = match, 42 = no match
            return int(body["steamid"]) if body.get("success") == 1 else None

        r = await self._http.get(self.XML_URL.format(vanity=vanity), follow_redirects=True)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        try:
            node = ET.fromstring(r.text).find("steamID64")
        except ET.ParseError:
            return None
        return int(node.text) if node is not None and node.text else None

    async def profile_texts(self, steam64: int) -> ProfileTexts:
        """Current display name and "About me" text, read live (no caching) for verification.

        The display name is public even on private profiles; the summary is not."""
        name = summary = ""
        r = await self._http.get(
            self.PROFILE_URL.format(steam64=steam64),
            # All query params here: httpx's `params` replaces any query already in the URL.
            params={"xml": 1, "l": "english"},
            headers={"Cache-Control": "no-cache"},
            follow_redirects=True,
        )
        if r.is_success:
            try:
                root = ET.fromstring(r.text)
            except ET.ParseError:
                root = None
            if root is not None:
                # Direct children only: <groups> entries carry their own <summary>.
                name = root.findtext("steamID") or ""
                summary = root.findtext("summary") or ""
        if not name and self._api_key:
            body = await self._web_api(self.SUMMARIES_URL, steamids=steam64)
            players = body.get("players", [])
            name = players[0].get("personaname", "") if players else ""
        return ProfileTexts(name, summary)

    async def resolve_account_id(self, text: str) -> int:
        parsed = parse_steam_input(text)
        try:
            match parsed.kind:
                case InputKind.ACCOUNT_ID:
                    return int(parsed.value)
                case InputKind.STEAM64:
                    return steam64_to_account_id(int(parsed.value))
                case InputKind.VANITY:
                    try:
                        steam64 = await self.resolve_vanity(parsed.value)
                    except httpx.HTTPError as e:
                        raise SteamResolveError(
                            "Steam didn't respond while resolving that name. Try again, or "
                            "paste your profile URL with /profiles/<SteamID64>."
                        ) from e
                    if steam64 is None:
                        raise SteamResolveError(
                            f"No Steam profile found with the custom URL `{parsed.value}`."
                        )
                    return steam64_to_account_id(steam64)
        except ValueError as e:
            raise SteamResolveError(str(e)) from e
