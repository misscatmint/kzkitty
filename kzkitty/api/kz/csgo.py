import logging
import re
from datetime import datetime, timedelta, timezone

from aiohttp import ClientError, ClientSession
from tortoise.exceptions import DoesNotExist

from kzkitty.api.kz.base import (API, APIConnectionError, APIError, APIMap,
                                 APIMapAmbiguousError, APIMapError,
                                 APIMapNotFoundError, Rank, PersonalBest,
                                 Profile)
from kzkitty.api.steam import SteamError, name_for_steamid64
from kzkitty.models import Map, Mode, Type

logger = logging.getLogger('kzkitty.api.kz.csgo')

async def _vnl_tiers() -> dict[int, tuple[int, int]]:
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
        logger.error('Malformed vnl.kz API maps response (not a list)')
        return {}

    maps = {}
    for map_info in json:
        map_id = map_info.get('id')
        if not isinstance(map_id, int):
            logger.error('Malformed vnl.kz API maps response (id not an int)')
            continue
        tp_tier = map_info.get('tpTier')
        pro_tier = map_info.get('proTier')
        if not isinstance(tp_tier, int) or not isinstance(pro_tier, int):
            logger.error('Malformed vnl.kz API maps response'
                         ' (tp/pro tiers not ints)')
            continue
        maps[map_id] = (tp_tier, pro_tier)
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
        raise APIConnectionError("Couldn't get vnl.kz map tiers") from e

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

async def refresh_csgo_db_maps() -> None:
    logger.info('Downloading CSGO map tiers')
    url = 'https://kztimerglobal.com/api/v2.0/maps?limit=9999'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    logger.error("Couldn't get global API maps (HTTP %d)",
                                 r.status)
                    return
                json = await r.json()
    except ClientError:
        logger.exception("Couldn't get global API maps")
        return

    if not isinstance(json, list):
        logger.error('Malformed global API maps response (not a list)')
        return

    logger.info('Downloading CSGO VNL map tiers')
    vnl_tiers = await _vnl_tiers()

    new = 0
    updated = 0
    deleted = 0
    for map_info in json:
        map_id = map_info.get('id')
        if not isinstance(map_id, int):
            logger.error('Malformed global API maps response (id not an int)')
            continue
        name = map_info.get('name')
        if not isinstance(name, str):
            logger.error('Malformed global API maps response (name not a str)')
            continue
        tier = map_info.get('difficulty')
        if not isinstance(tier, int):
            logger.error('Malformed global API maps response'
                         ' (tier not an int)')
            continue
        validated = map_info.get('validated')
        if not isinstance(validated, bool):
            logger.error('Malformed global API maps response'
                         ' (validated not a bool)')
            continue

        if not validated:
            try:
                db_map = await Map.get(map_id=map_id, is_cs2=False)
            except DoesNotExist:
                pass
            else:
                await db_map.delete()
                deleted += 1
            continue

        vnl_tier, vnl_pro_tier = vnl_tiers.get(map_id, (10, 10))
        try:
            db_map = await Map.get(map_id=map_id, is_cs2=False)
        except DoesNotExist:
            logger.info('Downloading thumbnail for map %s', name)
            thumbnail = await _thumbnail_for_map(name)
            await Map(map_id=map_id, is_cs2=False, name=name, tier=tier,
                      pro_tier=tier, vnl_tier=vnl_tier,
                      vnl_pro_tier=vnl_pro_tier, thumbnail=thumbnail).save()
            new += 1
        else:
            thumbnail = db_map.thumbnail
            if thumbnail is None:
                thumbnail = await _thumbnail_for_map(name)
            changed = False
            if db_map.tier != tier:
                logger.info('Updating tier for map %s', name)
                db_map.tier = db_map.pro_tier = tier
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
    logger.info('Refreshed map database (%d new, %d updated, %d deleted)',
                new, updated, deleted)

def _tier_name(tier: int | None, mode: Mode) -> str:
    if mode == Mode.VNL:
        names = {1: 'Very Easy', 2: 'Easy', 3: 'Medium', 4: 'Advanced',
                 5: 'Hard', 6: 'Very Hard', 7: 'Extreme', 8: 'Death',
                 9: 'Unfeasible', 10: 'Impossible'}
    else:
        names = {1: 'Very Easy', 2: 'Easy', 3: 'Medium', 4: 'Hard',
                 5: 'Very Hard', 6: 'Extreme', 7: 'Death'}
    return names.get(tier or -1, 'Unknown')

def _profile_url(steamid64: int, mode: Mode) -> str:
    if mode == Mode.VNL:
        return f'https://vnl.kz/#/stats/{steamid64}'
    else:
        return f'https://kzgo.eu/players/{steamid64}?{mode.lower()}'

def _map_url(name: str, mode: Mode, stage: int) -> str:
    if stage != 0:
        return (f'https://kzgo.eu/maps/{name}?{mode.lower()}'
                f'&bonus={stage}')
    elif mode == Mode.VNL:
        return f'https://vnl.kz/#/map/{name}'
    else:
        return f'https://kzgo.eu/maps/{name}?{mode.lower()}'

async def _records_for_steamid64(steamid64: int, mode: Mode,
                                 tp_type: Type=Type.ANY,
                                 map_name: str | None=None,
                                 stage: int | None=None,
                                 limit: int | None=None) -> list[dict]:
    api_mode = {Mode.KZT: 'kz_timer', Mode.SKZ: 'kz_simple',
                Mode.VNL: 'kz_vanilla'}[mode]
    url = ('https://kztimerglobal.com/api/v2.0/records/top?'
           f'steamid64={steamid64}&tickrate=128&'
           f'modes_list_string={api_mode}')
    if stage is not None:
        url += f'&stage={stage}'
    if tp_type == Type.TP:
        url += '&has_teleports=true'
    elif tp_type == Type.PRO:
        url += '&has_teleports=false'
    if map_name is not None:
        url += f'&map_name={map_name}'
    if limit is not None:
        url += f'&limit={limit}'
    else:
        url += '&limit=9999'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    raise APIError("Couldn't get global API PBs (HTTP %d)" %
                                   r.status)
                records = await r.json()
    except ClientError as e:
        raise APIConnectionError("Couldn't get global API PBs") from e
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
    stage = record.get('stage')
    record_id = record.get('id')
    time = record.get('time')
    teleports = record.get('teleports')
    points = record.get('points')
    created_on = record.get('created_on')
    if (not isinstance(stage, int) or
        not isinstance(record_id, int) or
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
    player_url = _profile_url(steamid64, api_map.mode)
    return PersonalBest(id=record_id, steamid64=steamid64,
                        player_name=player_name, player_url=player_url,
                        map=api_map, time=timedelta(seconds=time),
                        teleports=teleports, points=points, point_scale=1000,
                        place=None, date=date)

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
        raise APIConnectionError("Couldn't get global API PB place") from e
    if not isinstance(place, int):
        raise APIError('Malformed global API PB place (not an int)')
    return place

async def _record_for_map(api_map: APIMap, mode: Mode, tp_type: Type,
                          stage: int | None=None) -> dict | None:
    api_mode = {Mode.KZT: 'kz_timer', Mode.SKZ: 'kz_simple',
                Mode.VNL: 'kz_vanilla'}[mode]
    stage = stage or 0
    url = ('https://kztimerglobal.com/api/v2.0/records/top'
           f'?map_name={api_map.name}&stage={stage}&'
           f'modes_list_string={api_mode}&limit=1')
    if tp_type == Type.TP:
        url += '&has_teleports=true'
    elif tp_type == Type.PRO:
        url += '&has_teleports=false'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    raise APIError("Couldn't get global API WR "
                                   '(HTTP %d)' % r.status)
                records = await r.json()
    except ClientError as e:
        raise APIConnectionError("Couldn't get global API WR") from e
    if not isinstance(records, list):
        raise APIError('Malformed global API WR (not a list)')
    if records and not isinstance(records[0], dict):
        raise APIError('Malformed global API WR (not a list of dicts)')
    return records[0] if records else None

class CSGOAPI(API):
    def has_tp_wrs(self) -> bool:
        return True

    async def get_map(self, name: str, course: str | None=None,
                      bonus: int | None=None) -> APIMap:
        if not re.fullmatch('[A-za-z0-9_]+', name):
            raise APIMapError('Invalid map name')

        db_map = None
        try:
            db_map = await Map.get(name__iexact=name, is_cs2=False)
        except DoesNotExist:
            db_maps = list(await Map.filter(name__icontains=name,
                                            is_cs2=False))
            if len(db_maps) > 1:
                raise APIMapAmbiguousError(db_maps)
            elif db_maps:
                db_map = db_maps[0]

        if db_map is not None:
            name = db_map.name
            tier = db_map.tier
            pro_tier = db_map.pro_tier
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
                raise APIConnectionError("Couldn't get global API map") from e

            if json is None:
                raise APIMapNotFoundError('Map not found')
            elif not isinstance(json, dict):
                raise APIError('Malformed global API map response '
                               '(not a dict)')
            tier = pro_tier = json.get('difficulty')
            if not isinstance(tier, int):
                raise APIError('Malformed global API map response '
                               '(tier not an int)')
            vnl_tier = vnl_pro_tier = thumbnail = None

        if course is not None:
            raise APIMapError("Courses aren't supported on CS:GO. "
                              'Did you mean to specify a bonus?')
        bonus = bonus or None

        max_tier = 7
        if bonus:
            tier = tier_name = pro_tier = pro_tier_name = None
        else:
            if self.mode == Mode.VNL:
                max_tier = 10
                if vnl_tier is None or vnl_pro_tier is None:
                    try:
                        tier, pro_tier = await _vnl_tiers_for_map(name)
                    except APIError:
                        logger.exception("Couldn't get vnl.kz map tiers")
                        tier = pro_tier = None
                else:
                    tier = vnl_tier
                    pro_tier = vnl_pro_tier
            tier_name = _tier_name(tier, self.mode)
            pro_tier_name = _tier_name(pro_tier, self.mode)

        if thumbnail is None:
            thumbnail = await _thumbnail_for_map(name)

        url = _map_url(name, self.mode, bonus or 0)

        return APIMap(name=name, mode=self.mode, bonus=bonus or None,
                      course=None, tier=tier, tier_name=tier_name,
                      pro_tier=pro_tier, pro_tier_name=pro_tier_name,
                      max_tier=max_tier, thumbnail=thumbnail, url=url)

    async def get_pb(self, steamid64: int, api_map: APIMap,
                     tp_type: Type=Type.ANY) -> PersonalBest | None:
        records = await _records_for_steamid64(steamid64, self.mode,
                                               map_name=api_map.name,
                                               stage=api_map.bonus or 0,
                                               limit=2)
        pbs = [_record_to_pb(record, api_map) for record in records]
        if not pbs:
            return None

        if tp_type == Type.PRO:
            pbs = [pb for pb in pbs if pb.teleports == 0]
        elif tp_type == Type.TP:
            pbs = [pb for pb in pbs if pb.teleports]
        pbs.sort(key=lambda pb: pb.time)
        pb = pbs[0]
        try:
            pb.place = await _place_for_pb(pb)
        except APIError:
            logger.exception("Couldn't get global API PB place")
        return pbs[0]

    async def get_latest(self, steamid64: int, tp_type: Type=Type.ANY
                         ) -> PersonalBest | None:
        if tp_type in {Type.TP, Type.ANY}:
            records = await _records_for_steamid64(steamid64, self.mode,
                                                   stage=0, tp_type=Type.TP)
        else:
            records = []
        if tp_type in {Type.PRO, Type.ANY}:
            pros = await _records_for_steamid64(steamid64, self.mode,
                                                stage=0, tp_type=Type.PRO)
        else:
            pros = []
        records += pros
        if not records:
            return None

        def sort_key(i: dict[str, str]) -> str:
            return i.get('created_on', '')
        records.sort(key=sort_key, reverse=True)
        record = records[0]
        map_name = record.get('map_name')
        if not isinstance(map_name, str):
            raise APIError('Invalid map name from API PB (not a str)')
        try:
            api_map = await self.get_map(map_name)
        except APIMapError as e:
            raise APIError('Invalid map name from API PB') from e
        pb = _record_to_pb(record, api_map)
        try:
            pb.place = await _place_for_pb(pb)
        except APIError:
            logger.exception("Couldn't get global API PB place")
        return pb

    async def get_wrs(self, api_map: APIMap) -> list[PersonalBest]:
        bonus = api_map.bonus or 0
        records = [await _record_for_map(api_map, self.mode, tp_type, bonus)
                   for tp_type in (Type.TP, Type.PRO)]
        return [_record_to_pb(record, api_map)
                for record in records if record]

    async def get_profile(self, steamid64: int) -> Profile:
        player_url = _profile_url(steamid64, self.mode)
        api_mode_id = {Mode.KZT: 200, Mode.SKZ: 201, Mode.VNL: 202}[self.mode]
        url = ('https://kztimerglobal.com/api/v2.0/player_ranks?'
               f'steamid64s={steamid64}&stages=0&mode_ids={api_mode_id}&'
               'tickrates=128')
        try:
            async with ClientSession() as session:
                async with session.get(url) as r:
                    if r.status != 200:
                        raise APIError("Couldn't get global API PBs "
                                       '(HTTP %d)' % r.status)
                    results = await r.json()
        except ClientError as e:
            raise APIConnectionError("Couldn't get global API PBs") from e
        if not isinstance(results, list):
            raise APIError('Malformed global API ranks (not a list)')
        if results and not isinstance(results[0], dict):
            raise APIError('Malformed global API ranks (not a list of dicts)')
        if not results:
            try:
                player_name = await name_for_steamid64(steamid64)
            except SteamError:
                player_name = None
            return Profile(name=player_name, url=player_url, mode=self.mode,
                           rank=Rank.NEW, points=0, average=0)

        info = results[0]
        points = info.get('points')
        if not isinstance(points, int):
            raise APIError('Malformed global API ranks (points not an int)')

        average = info.get('average')
        if not isinstance(average, float):
            raise APIError('Malformed global API ranks (average not a float)')

        if self.mode == Mode.VNL:
            thresholds = [(600000, Rank.LEGEND),
                          (400000, Rank.MASTER),
                          (300000, Rank.PRO),
                          (250000, Rank.SEMIPRO),
                          (200000, Rank.EXPERT_PLUS),
                          (180000, Rank.EXPERT),
                          (160000, Rank.EXPERT_MINUS),
                          (140000, Rank.SKILLED_PLUS)]
        elif self.mode == Mode.SKZ:
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
        rank = Rank.NEW
        for threshold, rank in thresholds:
            if points >= threshold:
                break
        return Profile(name=info.get('player_name'), url=player_url,
                       mode=self.mode, rank=rank, points=points,
                       average=int(average))
