from dataclasses import dataclass
from typing import Self
from urllib.parse import urlsplit
from xml.etree import ElementTree

from niquests import AsyncSession, RequestException

class SteamError(Exception):
    pass

class SteamUnitializedError(SteamError):
    pass

class SteamValueError(SteamError):
    pass

@dataclass
class SteamProfile:
    name: str
    avatar_url: str

class Steam:
    def __init__(self, timeout: int | None=None) -> None:
        self._session = AsyncSession(timeout=timeout)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, _exc, _val, _tb) -> None:
        await self.close()

    async def close(self) -> None:
        await self._session.close()

    async def _get_profile(self, url: str) -> ElementTree.Element:
        try:
            r = await self._session.get(url, stream=True)
            if r.status_code != 200:
                raise SteamError("Couldn't get Steam profile (HTTP %d)"
                                 % r.status_code)
            elif r.oheaders.content_type != 'text/xml':
                raise SteamError("Couldn't get Steam profile (not text/xml)")
            text = await r.text
            if text is None:
                raise SteamError("Couldn't get Steam profile (bad encoding)")
        except RequestException as e:
            raise SteamError("Couldn't get Steam profile") from e

        try:
            return ElementTree.fromstring(text)
        except ElementTree.ParseError as e:
            raise SteamError("Couldn't parse Steam profile XML") from e

    async def steamid64_for_profile(self, url: str) -> int:
        u = urlsplit(url)
        if u.netloc != 'steamcommunity.com':
            raise SteamValueError

        url = f'https://steamcommunity.com{u.path}?xml=1'
        xml = await self._get_profile(url)
        steamid64 = xml.find('steamID64')
        if steamid64 is None or steamid64.text is None:
            raise SteamError('Malformed Steam profile XML (no steamid64)')
        try:
            return int(steamid64.text)
        except ValueError as e:
            raise SteamError('Malformed Steam profile XML '
                             '(bad steamid64)') from e

    async def profile_for_steamid64(self, steamid64: int) -> SteamProfile:
        url = f'https://steamcommunity.com/profiles/{steamid64}?xml=1'
        xml = await self._get_profile(url)
        steam_id = xml.find('steamID')
        if steam_id is None or steam_id.text is None:
            raise SteamError('Malformed Steam profile XML (no steamID)')
        avatar = xml.find('avatarFull')
        if avatar is None or avatar.text is None:
            raise SteamError('Malformed Steam profile XML (no avatar)')
        return SteamProfile(name=steam_id.text, avatar_url=avatar.text)

_steam: Steam | None = None

async def init_steam(timeout: int | None=None) -> None:
    global _steam
    if _steam is None:
        _steam = Steam(timeout=timeout)

async def close_steam() -> None:
    global _steam
    if _steam is not None:
        await _steam.close()
        _steam = None

def get_steam() -> Steam:
    if _steam is None:
        raise SteamUnitializedError
    return _steam
