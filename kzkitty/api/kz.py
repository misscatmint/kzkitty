import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from aiohttp import ClientError, ClientSession
from tortoise.exceptions import DoesNotExist

from kzkitty.api.steam import SteamError, name_for_steamid64
from kzkitty.models import Map, Mode, Type

logger = logging.getLogger('kzkitty.api.kz')

class APIError(Exception):
    pass

class APIMapError(APIError):
    pass

class APIMapAmbiguousError(APIMapError):
    def __init__(self, db_maps):
        self.db_maps = db_maps

class Rank(StrEnum):
    NEW = 'New'
    BEGINNER_MINUS = 'Beginner-'
    BEGINNER = 'Beginner'
    BEGINNER_PLUS = 'Beginner+'
    AMATEUR_MINUS = 'Amateur-'
    AMATEUR = 'Amateur'
    AMATEUR_PLUS = 'Amateur+'
    CASUAL_MINUS = 'Casual-'
    CASUAL = 'Casual'
    CASUAL_PLUS = 'Casual+'
    REGULAR_MINUS = 'Regular-'
    REGULAR = 'Regular'
    REGULAR_PLUS = 'Regular+'
    SKILLED_MINUS = 'Skilled-'
    SKILLED = 'Skilled'
    SKILLED_PLUS = 'Skilled+'
    EXPERT_MINUS = 'Expert-'
    EXPERT = 'Expert'
    EXPERT_PLUS = 'Expert+'
    SEMIPRO = 'Semipro'
    PRO = 'Pro'
    MASTER = 'Master'
    LEGEND = 'Legend'

@dataclass
class APIMap:
    name: str
    tier: int
    vnl_tier: int | None
    vnl_pro_tier: int | None
    thumbnail: bytes | None

@dataclass
class PersonalBest:
    id: int
    steamid64: int
    player_name: str | None
    map: APIMap
    mode: Mode
    time: timedelta
    teleports: int
    points: int
    place: int | None
    date: datetime

@dataclass
class Profile:
    player_name: str | None
    mode: Mode
    rank: Rank
    points: int
    average: int

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
                if r.status == 404:
                    return 10, 10
                elif r.status != 200:
                    raise APIError("Couldn't get vnl.kz map tiers (HTTP %d)" %
                                   r.status)
                json = await r.json()
    except ClientError as e:
        raise APIError("Couldn't get vnl.kz map tiers") from e

    if not isinstance(json, dict):
        raise APIError("Malformed vnl.kz JSON (not a dict)")
    tp_tier = json.get('tpTier')
    pro_tier = json.get('proTier')
    if (not isinstance(tp_tier, int) or
        not isinstance(pro_tier, int)):
        raise APIError("Malformed vnl.kz JSON (tpTier/proTier not an int)")
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
                    raise APIError("Couldn't get global API maps (HTTP %d)" %
                                   r.status)
                json = await r.json()
    except ClientError as e:
        raise APIError("Couldn't get global API maps") from e

    if not isinstance(json, list):
        raise APIError('Malformed global API maps response (not a list)')

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
        raise APIMapError('Invalid map name')

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
                        raise APIError("Couldn't get global API map "
                                       '(HTTP %d)' % r.status)
                    json = await r.json()
        except ClientError as e:
            raise APIError("Couldn't get global API map") from e

        if json is None:
            raise APIMapError('Map not found')
        elif not isinstance(json, dict):
            raise APIError('Malformed global API map response (not a dict)')
        tier = json.get('difficulty')
        if not isinstance(tier, int):
            raise APIError('Malformed global API map response (tier not an int)')
        vnl_tier = vnl_pro_tier = thumbnail = None

    if mode == Mode.VNL and (vnl_tier is None or vnl_pro_tier is None):
        try:
            vnl_tier, vnl_pro_tier = await _vnl_tiers_for_map(name)
        except APIMapError:
            vnl_tier = vnl_pro_tier = None
        except APIError:
            logger.exception("Couldn't get vnl.kz map tiers")
            vnl_tier = vnl_pro_tier = None

    if thumbnail is None:
        thumbnail = await _thumbnail_for_map(name)

    return APIMap(name=name, tier=tier, vnl_tier=vnl_tier,
                  vnl_pro_tier=vnl_pro_tier, thumbnail=thumbnail)

def _mode_for_record(record: dict) -> Mode:
    mode = {'kz_timer': Mode.KZT, 'kz_simple': Mode.SKZ,
            'kz_vanilla': Mode.VNL}.get(record.get('mode', ''))
    if mode is None:
        raise APIError('Malformed global API PB (bad mode)')
    return mode

async def _records_for_steamid64(steamid64: int, mode: Mode,
                                 teleport_type: Type=Type.ANY,
                                 api_map: APIMap | None=None,
                                 limit: int | None=None) -> list[dict]:
    api_mode = {Mode.KZT: 'kz_timer', Mode.SKZ: 'kz_simple',
                Mode.VNL: 'kz_vanilla'}[mode]
    url = ('https://kztimerglobal.com/api/v2.0/records/top?'
           f'steamid64={steamid64}&stage=0&tickrate=128&'
           f'modes_list_string={api_mode}')
    if teleport_type == Type.TP:
        url += '&has_teleports=true'
    elif teleport_type == Type.PRO:
        url += '&has_teleports=false'
    if api_map is not None:
        url += f'&map_name={api_map.name}'
    if limit is not None:
        url += f'&limit={limit}'
    else:
        url += f'&limit=9999'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    raise APIError("Couldn't get global API PBs (HTTP %d)" %
                                   r.status)
                records = await r.json()
    except ClientError as e:
        raise APIError("Couldn't get global API PBs") from e
    if not isinstance(records, list):
        raise APIError('Malformed global API PBs (not a list)')
    if records and not isinstance(records[0], dict):
        raise APIError('Malformed global API PBs (not a list of dicts)')
    return records

def _record_to_pb(record: dict, api_map: APIMap) -> PersonalBest:
    steamid64_str = record.get('steamid64')
    if steamid64_str is None:
        raise APIError('Malformed global API PB (missing steamid64)')
    try:
        steamid64 = int(steamid64_str)
    except ValueError as e:
        raise APIError('Malformed global API PB (bad steamid64)') from e
    player_name = record.get('player_name')
    if not isinstance(player_name, str) and player_name is not None:
        raise APIError('Malformed global API PB (bad player_name)')
    mode = _mode_for_record(record)
    record_id = record.get('id')
    time = record.get('time')
    teleports = record.get('teleports')
    points = record.get('points')
    created_on = record.get('created_on')
    if (not isinstance(record_id, int) or
        not isinstance(time, float) or
        not isinstance(teleports, int) or
        not isinstance(points, int) or
        not isinstance(created_on, str)):
        raise APIError('Malformed global API PB')
    try:
        date = datetime.fromisoformat(created_on)
    except ValueError as e:
        raise APIError('Malformed global API PB (bad date)') from e
    date = date.replace(tzinfo=timezone.utc)

    return PersonalBest(id=record_id, steamid64=steamid64,
                        player_name=player_name, map=api_map,
                        time=timedelta(seconds=time), mode=mode,
                        teleports=teleports, points=points, place=None,
                        date=date)

async def _place_for_pb(pb: PersonalBest) -> int:
    url = f'https://kztimerglobal.com/api/v2.0/records/place/{pb.id}'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    raise APIError("Couldn't get global API PB place "
                                   '(HTTP %d)' % r.status)
                place = await r.json()
    except ClientError as e:
        raise APIError("Couldn't get global API PB place") from e
    if not isinstance(place, int):
        raise APIError('Malformed global API PB place (not an int)')
    return place

async def pb_for_steamid64(steamid64: int, api_map: APIMap, mode: Mode,
                           teleport_type: Type=Type.ANY,
                           ) -> PersonalBest | None:
    records = await _records_for_steamid64(steamid64, mode, api_map=api_map,
                                           limit=2)
    pbs = [_record_to_pb(record, api_map) for record in records]
    if not pbs:
        return None

    if teleport_type == Type.PRO:
        pbs = [pb for pb in pbs if pb.teleports == 0]
    elif teleport_type == Type.TP:
        pbs = [pb for pb in pbs if pb.teleports]
    pbs.sort(key=lambda pb: pb.time)
    pb = pbs[0]
    try:
        pb.place = await _place_for_pb(pb)
    except APIError:
        logger.exception("Couldn't get global API PB place")
    return pbs[0]

async def latest_pb_for_steamid64(steamid64: int, mode: Mode,
                                  teleport_type: Type=Type.ANY,
                                  ) -> PersonalBest | None:

    if teleport_type in {Type.TP, Type.ANY}:
        records = await _records_for_steamid64(steamid64, mode,
                                               teleport_type=Type.TP)
    else:
        records = []
    if teleport_type in {Type.PRO, Type.ANY}:
        pros = await _records_for_steamid64(steamid64, mode,
                                            teleport_type=Type.PRO)
    else:
        pros = []
    records += pros
    if not records:
        return None

    def sort_key(i: dict[str, str]) -> str:
        return i.get('created_on', '')
    records.sort(key=sort_key, reverse=True)
    record = records[0]
    mode = _mode_for_record(record)
    try:
        api_map = await map_for_name(record.get('map_name', ''), mode)
    except APIMapError as e:
        raise APIError('Invalid map name from API PB') from e
    pb = _record_to_pb(record, api_map)
    try:
        pb.place = await _place_for_pb(pb)
    except APIError:
        pass
    return pb

async def _record_for_map(api_map: APIMap, mode: Mode, teleport_type: Type
                          ) -> dict | None:
    api_mode = {Mode.KZT: 'kz_timer', Mode.SKZ: 'kz_simple',
                Mode.VNL: 'kz_vanilla'}[mode]
    url = ('https://kztimerglobal.com/api/v2.0/records/top'
           f'?map_name={api_map.name}&stage=0&'
           f'modes_list_string={api_mode}&limit=1')
    if teleport_type == Type.TP:
        url += '&has_teleports=true'
    elif teleport_type == Type.PRO:
        url += '&has_teleports=false'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    raise APIError("Couldn't get global API WR "
                                   '(HTTP %d)' % r.status)
                records = await r.json()
    except ClientError as e:
        raise APIError("Couldn't get global API WR") from e
    if not isinstance(records, list):
        raise APIError('Malformed global API WR (not a list)')
    if records and not isinstance(records[0], dict):
        raise APIError('Malformed global API WR (not a list of dicts)')
    return records[0] if records else None

async def wrs_for_map(api_map: APIMap, mode: Mode) -> list[PersonalBest]:
    records = [await _record_for_map(api_map, mode, teleport_type)
               for teleport_type in (Type.TP, Type.PRO)]
    return [_record_to_pb(record, api_map) for record in records if record]

async def profile_for_steamid64(steamid64, mode: Mode) -> Profile:
    records = await _records_for_steamid64(steamid64, mode,
                                           teleport_type=Type.TP)
    records += await _records_for_steamid64(steamid64, mode,
                                            teleport_type=Type.PRO)
    if not records:
        try:
            player_name = await name_for_steamid64(steamid64)
        except SteamError:
            player_name = None
        return Profile(player_name=player_name, mode=mode, rank=Rank.NEW,
                       points=0, average=0)

    if mode == Mode.VNL:
        thresholds = [(600000, Rank.LEGEND),
                      (400000, Rank.MASTER),
                      (300000, Rank.PRO),
                      (250000, Rank.SEMIPRO),
                      (200000, Rank.EXPERT_PLUS),
                      (180000, Rank.EXPERT),
                      (160000, Rank.EXPERT_MINUS),
                      (140000, Rank.SKILLED_PLUS)]
    elif mode == Mode.SKZ:
        thresholds = [(800000, Rank.LEGEND),
                      (500000, Rank.MASTER),
                      (400000, Rank.PRO),
                      (300000, Rank.SEMIPRO),
                      (250000, Rank.EXPERT_PLUS),
                      (230000, Rank.EXPERT),
                      (200000, Rank.EXPERT_MINUS),
                      (150000, Rank.SKILLED_PLUS)]
    else:
        thresholds = [(1000000, Rank.LEGEND),
                      (800000, Rank.MASTER),
                      (600000, Rank.PRO),
                      (400000, Rank.SEMIPRO),
                      (250000, Rank.EXPERT_PLUS),
                      (230000, Rank.EXPERT),
                      (200000, Rank.EXPERT_MINUS),
                      (150000, Rank.SKILLED_PLUS)]
    thresholds += [(120000, Rank.SKILLED),
                   (100000, Rank.SKILLED_MINUS),
                   (80000, Rank.REGULAR_PLUS),
                   (70000, Rank.REGULAR),
                   (60000, Rank.REGULAR_MINUS),
                   (40000, Rank.CASUAL_PLUS),
                   (30000, Rank.CASUAL),
                   (20000, Rank.CASUAL_MINUS),
                   (10000, Rank.AMATEUR_PLUS),
                   (5000, Rank.AMATEUR),
                   (2000, Rank.AMATEUR_MINUS),
                   (1000, Rank.BEGINNER_PLUS),
                   (500, Rank.BEGINNER),
                   (1, Rank.BEGINNER_MINUS)]

    points = sum(r['points'] for r in records)
    average = points // len(records)
    rank = Rank.NEW
    for threshold, rank in thresholds:
        if points >= threshold:
            break
    return Profile(player_name=records[0].get('player_name'), mode=mode,
                   rank=rank, points=points, average=average)
