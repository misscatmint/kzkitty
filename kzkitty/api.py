import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from xml.etree import ElementTree

from aiohttp import ClientError, ClientSession
from tortoise.exceptions import DoesNotExist

from kzkitty.models import Map, Mode, Type

logger = logging.getLogger('kzkitty.api')

class SteamError(Exception):
    pass

class SteamValueError(SteamError):
    pass

class SteamHTTPError(SteamError):
    pass

class SteamXMLError(SteamError):
    pass

class APIError(Exception):
    pass

class APIMapError(APIError):
    pass

class APIMapAmbiguousError(APIMapError):
    def __init__(self, db_maps):
        self.db_maps = db_maps

@dataclass
class APIMap:
    name: str
    tier: int
    vnl_tier: int | None
    vnl_pro_tier: int | None
    thumbnail: bytes | None

@dataclass
class PersonalBest:
    player_name: str | None
    map: APIMap
    mode: Mode
    time: timedelta
    teleports: int
    points: int
    date: datetime

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

async def _vnl_tiers() -> dict[str, tuple[int, int]]:
    url = 'https://vnl.kz/api/maps'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    logger.error("Couldn't get vnl.kz API maps (HTTP %d)",
                                 r.status)
                    return {}
                json = await r.json()
    except ClientError:
        logger.exception("Couldn't get vnl.kz API maps")
        return {}

    if not isinstance(json, list):
        logger.error('Malformed global API maps response (not a list)')
        return {}

    maps = {}
    for map_info in json:
        name = map_info.get('name')
        if not isinstance(name, str):
            logger.error('Malformed global API maps response (name not a str)')
            continue
        tp_tier = map_info.get('tpTier')
        pro_tier = map_info.get('proTier')
        if not isinstance(tp_tier, int) or not isinstance(pro_tier, int):
            logger.error('Malformed global API maps response'
                         ' (tp/pro tiers not ints)')
            continue
        maps[name] = (tp_tier, pro_tier)
    return maps

async def _vnl_tiers_for_map(name: str) -> tuple[int | None, int | None]:
    url = f'https://vnl.kz/api/maps/{name}'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    logger.error("Couldn't get vnl.kz map tiers (HTTP %d)",
                                 r.status)
                    return None, None
                json = await r.json()
    except ClientError:
        logger.exception("Couldn't get vnl.kz map tiers")
        return None, None

    if not isinstance(json, dict):
        logger.error("Malformed vnl.kz JSON (not a dict)")
        return None, None
    tp_tier = json.get('tpTier')
    if not isinstance(tp_tier, int):
        logger.error("Malformed vnl.kz JSON (tpTier not an int)")
        tp_tier = None
    pro_tier = json.get('proTier')
    if not isinstance(pro_tier, int):
        logger.error("Malformed vnl.kz JSON (proTier not an int)")
        pro_tier = None
    return tp_tier, pro_tier

async def _thumbnail_for_map(name: str) -> bytes | None:
    thumbnail_url = ('https://raw.githubusercontent.com/KZGlobalTeam/'
                     f'map-images/public/webp/medium/{name}.webp')
    thumbnail = None
    try:
        async with ClientSession() as session:
            async with session.get(thumbnail_url) as r:
                if r.status == 200:
                    thumbnail = await r.content.read()
                else:
                    logger.error("Couldn't get map thumbnail (HTTP %d)",
                                 r.status)
    except ClientError:
        logger.exception("Couldn't get map thumbnail")
    return thumbnail

async def refresh_db_maps() -> tuple[int, int]:
    logger.info('Downloading map tiers')
    url = ('https://kztimerglobal.com/api/v2.0'
           '/maps?is_validated=true&limit=9999')
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    logger.error("Couldn't get global API maps (HTTP %d)",
                                 r.status)
                    raise APIError
                json = await r.json()
    except ClientError:
        logger.exception("Couldn't get global API maps")
        raise APIError

    if not isinstance(json, list):
        logger.error('Malformed global API maps response (not a list)')
        raise APIError

    logger.info('Downloading VNL map tiers')
    vnl_tiers = await _vnl_tiers()

    new = 0
    updated = 0
    for map_info in json:
        name = map_info.get('name')
        if not isinstance(name, str):
            logger.error('Malformed global API maps response (name not a str)')
            continue
        tier = map_info.get('difficulty')
        if not isinstance(tier, int):
            logger.error('Malformed global API maps response'
                         ' (tier not an int)')
            continue
        vnl_tier, vnl_pro_tier = vnl_tiers.get(name, (None, None))
        try:
            db_map = await Map.get(name=name)
        except DoesNotExist:
            logger.info('Downloading thumbnail for map %s', name)
            thumbnail = await _thumbnail_for_map(name)
            await Map(name=name, tier=tier, vnl_tier=vnl_tier,
                      vnl_pro_tier=vnl_pro_tier, thumbnail=thumbnail).save()
            new += 1
        else:
            thumbnail = db_map.thumbnail
            if thumbnail is None:
                thumbnail = await _thumbnail_for_map(name)
            changed = False
            if db_map.tier != tier:
                logger.info('Updating tier for map %s', name)
                db_map.tier = tier
                changed = True
            if db_map.vnl_tier != vnl_tier and vnl_tier is not None:
                logger.info('Updating VNL tier for map %s', name)
                db_map.vnl_tier = vnl_tier
                changed = True
            if (db_map.vnl_pro_tier != vnl_pro_tier and
                vnl_pro_tier is not None):
                logger.info('Updating VNL pro tier for map %s', name)
                db_map.vnl_pro_tier = vnl_pro_tier
                changed = True
            if db_map.thumbnail != thumbnail and thumbnail is not None:
                logger.info('Updating thumbnail for map %s', name)
                db_map.thumbnail = thumbnail
                changed = True
            if changed:
                await db_map.save()
                updated += 1
    return new, updated

async def map_for_name(name: str, mode: Mode) -> APIMap:
    if not re.fullmatch('[A-za-z0-9_]+', name):
        raise APIMapError

    db_map = None
    try:
        db_map = await Map.get(name__iexact=name)
    except DoesNotExist:
        db_maps = list(await Map.filter(name__icontains=name))
        if len(db_maps) > 1:
            raise APIMapAmbiguousError(db_maps)
        elif db_maps:
            db_map = db_maps[0]

    if db_map is not None:
        name = db_map.name
        tier = db_map.tier
        vnl_tier = db_map.vnl_tier
        vnl_pro_tier = db_map.vnl_pro_tier
        thumbnail = db_map.thumbnail
    else:
        json = {}
        url = f'https://kztimerglobal.com/api/v2.0/maps/name/{name}'
        try:
            async with ClientSession() as session:
                async with session.get(url) as r:
                    if r.status != 200:
                        logger.error("Couldn't get global API map (HTTP %d)",
                                     r.status)
                        raise APIError
                    json = await r.json()
        except ClientError:
            logger.exception("Couldn't get global API map")
            raise APIError

        if json is None:
            raise APIMapError
        elif not isinstance(json, dict):
            logger.error('Malformed global API map response (not a dict)')
            raise APIError
        tier = json.get('difficulty')
        if not isinstance(tier, int):
            logger.error('Malformed global API map response (tier not an int)')
            raise APIError
        vnl_tier = vnl_pro_tier = thumbnail = None

    if mode == Mode.VNL and (vnl_tier is None or vnl_pro_tier is None):
        vnl_tier, vnl_pro_tier = await _vnl_tiers_for_map(name)

    if thumbnail is None:
        thumbnail = await _thumbnail_for_map(name)

    return APIMap(name=name, tier=tier, vnl_tier=vnl_tier,
                  vnl_pro_tier=vnl_pro_tier, thumbnail=thumbnail)

def _mode_for_record(record: dict) -> Mode:
    mode = {'kz_timer': Mode.KZT, 'kz_simple': Mode.SKZ,
            'kz_vanilla': Mode.VNL}.get(record.get('mode', ''))
    if mode is None:
        logger.error('Malformed global API PB (bad mode)')
        raise APIError
    return mode

def _record_to_pb(record: dict, api_map: APIMap) -> PersonalBest:
    player_name = record.get('player_name')
    if not isinstance(player_name, str) and player_name is not None:
        logger.error('Malformed global API PB (bad player_name)')
        raise APIError
    mode = _mode_for_record(record)
    time = record.get('time')
    teleports = record.get('teleports')
    points = record.get('points')
    created_on = record.get('created_on')
    if (not isinstance(time, float) or
        not isinstance(teleports, int) or
        not isinstance(points, int) or
        not isinstance(created_on, str)):
        logger.error('Malformed global API PB')
        raise APIError
    try:
        date = datetime.fromisoformat(created_on)
    except ValueError:
        logger.exception('Malformed global API PB (bad date)')
        raise APIError
    date = date.replace(tzinfo=timezone.utc)

    return PersonalBest(player_name=player_name, map=api_map,
                        time=timedelta(seconds=time), mode=mode,
                        teleports=teleports, points=points, date=date)

async def pbs_for_steamid64(steamid64: int, api_map: APIMap, mode: Mode
                            ) -> list[PersonalBest]:
    api_mode = {Mode.KZT: 'kz_timer', Mode.SKZ: 'kz_simple',
                Mode.VNL: 'kz_vanilla'}[mode]
    url = ('https://kztimerglobal.com/api/v2.0/records/top?'
           f'steamid64={steamid64}&map_name={api_map.name}&stage=0&'
           f'tickrate=128&modes_list_string={api_mode}')
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    logger.error("Couldn't get global API PBs (HTTP %d)",
                                 r.status)
                    raise APIError
                records = await r.json()
    except ClientError:
        logger.exception("Couldn't get global API PBs")
        raise APIError
    if not isinstance(records, list):
        logger.error('Malformed global API PBs (not a list)')
        raise APIError

    return [_record_to_pb(record, api_map) for record in records]

async def latest_pb_for_steamid64(steamid64: int, mode: Mode,
                                  teleport_type: Type=Type.ANY,
                                  ) -> PersonalBest | None:
    api_mode = {Mode.KZT: 'kz_timer', Mode.SKZ: 'kz_simple',
                Mode.VNL: 'kz_vanilla'}[mode]
    if teleport_type in {Type.TP, Type.ANY}:
        url = ('https://kztimerglobal.com/api/v2.0/records/top?'
               f'steamid64={steamid64}&stage=0&limit=9999&has_teleports=true&'
               f'tickrate=128&modes_list_string={api_mode}')
        try:
            async with ClientSession() as session:
                async with session.get(url) as r:
                    if r.status != 200:
                        logger.error("Couldn't get global API PBs (HTTP %d)",
                                     r.status)
                        raise APIError
                    records = await r.json()
        except ClientError:
            logger.exception("Couldn't get global API PBs")
            raise APIError
        if not isinstance(records, list):
            logger.error('Malformed global API PBs (not a list)')
            raise APIError
    else:
        records = []
    if teleport_type in {Type.PRO, Type.ANY}:
        url = ('https://kztimerglobal.com/api/v2.0/records/top?'
               f'steamid64={steamid64}&stage=0&limit=9999&has_teleports=false&'
               f'tickrate=128&modes_list_string={api_mode}')
        try:
            async with ClientSession() as session:
                async with session.get(url) as r:
                    if r.status != 200:
                        logger.error("Couldn't get global API PBs (HTTP %d)",
                                     r.status)
                        raise APIError
                    pros = await r.json()
        except ClientError:
            logger.exception("Couldn't get global API PBs")
            raise APIError
        if not isinstance(pros, list):
            logger.error('Malformed global API PBs (not a list)')
            raise APIError
    else:
        pros = []
    records.extend(pros)
    if not records:
        return None

    records.sort(key=lambda i: i.get('created_on'), reverse=True)
    record = records[0]
    mode = _mode_for_record(record)
    api_map = await map_for_name(record.get('map_name', ''), mode)
    return _record_to_pb(record, api_map)
