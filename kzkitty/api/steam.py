import logging
from urllib.parse import urlparse
from xml.etree import ElementTree

from aiohttp import ClientError, ClientSession

logger = logging.getLogger('kzkitty.api.steam')

class SteamError(Exception):
    pass

class SteamValueError(SteamError):
    pass

class SteamHTTPError(SteamError):
    pass

class SteamXMLError(SteamError):
    pass

async def _get_steam_profile(url: str) -> ElementTree.Element:
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200 or r.content_type != 'text/xml':
                    logger.error("Couldn't get Steam profile (HTTP %d)",
                                 r.status)
                    raise SteamHTTPError
                text = await r.text()
    except ClientError:
        logger.exception("Couldn't get Steam profile")
        raise SteamHTTPError

    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError:
        logger.exception("Couldn't parse Steam profile XML")
        raise SteamXMLError

async def steamid64_for_profile(url: str) -> int:
    u = urlparse(url)
    if u.netloc != 'steamcommunity.com':
        raise SteamValueError

    url = f'https://steamcommunity.com{u.path}?xml=1'
    xml = await _get_steam_profile(url)
    steamid64 = xml.find('steamID64')
    if steamid64 is None or steamid64.text is None:
        logger.error('Malformed Steam profile XML (no steamid64)')
        raise SteamXMLError
    try:
        return int(steamid64.text)
    except ValueError:
        logger.exception('Malformed Steam profile XML (bad steamid64)')
        raise SteamXMLError

async def avatar_for_steamid64(steamid64: int) -> bytes:
    url = f'https://steamcommunity.com/profiles/{steamid64}?xml=1'
    xml = await _get_steam_profile(url)
    avatar = xml.find('avatarFull')
    if avatar is None or avatar.text is None:
        logger.error('Malformed Steam profile XML (no avatar)')
        raise SteamXMLError

    try:
        async with ClientSession() as session:
            async with session.get(avatar.text) as r:
                if r.status != 200:
                    logger.error("Couldn't get Steam profile (HTTP %d)",
                                 r.status)
                    raise SteamError
                return await r.content.read()
    except ClientError:
        logger.exception("Couldn't get Steam profile")
        raise SteamError

async def name_for_steamid64(steamid64: int) -> str:
    url = f'https://steamcommunity.com/profiles/{steamid64}?xml=1'
    xml = await _get_steam_profile(url)
    steam_id = xml.find('steamID')
    if steam_id is None or steam_id.text is None:
        logger.error('Malformed Steam profile XML (no steamID)')
        raise SteamXMLError
    return steam_id.text
