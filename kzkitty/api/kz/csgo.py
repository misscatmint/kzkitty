import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, override
from urllib.parse import quote, quote_plus, urlencode

from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import AfterValidator, BaseModel, TypeAdapter, ValidationError
from tortoise.exceptions import DoesNotExist
from tortoise.transactions import in_transaction

from kzkitty.api.kz.base import (API, APIConnectionError, APIError, APIMap,
                                 APIMapAmbiguousError, APIMapError,
                                 APIMapNotFoundError, Rank, PersonalBest,
                                 Profile)
from kzkitty.api.steam import SteamError, name_for_steamid64
from kzkitty.models import Map, Mode, Type

_logger = logging.getLogger('kzkitty.api.kz.csgo')

class _APIMap(BaseModel):
    id: int
    name: str
    difficulty: int
    validated: bool

class _VNLMap(BaseModel):
    id: int
    tpTier: int
    proTier: int

def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc)

class _APIRecord(BaseModel):
    id: int
    steamid64: int
    player_name: str | None
    map_name: str
    stage: int
    time: timedelta
    teleports: int
    points: int
    created_on: Annotated[datetime, AfterValidator(_utc_datetime)]

class _APIPlayerRank(BaseModel):
    player_name: str | None
    points: int
    average: float

_APIMapResult = TypeAdapter(_APIMap | None)
_APIMapList = TypeAdapter(list[_APIMap])
_VNLMapList = TypeAdapter(list[_VNLMap])
_APIRecordList = TypeAdapter(list[_APIRecord])
_APIPlayerRankList = TypeAdapter(list[_APIPlayerRank])
_APIPlace = TypeAdapter(int)

async def _vnl_tiers() -> dict[int, tuple[int, int]]:
    url = 'https://vnlkz.com/api/maps'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    raise APIError("Couldn't get VNL API maps (HTTP %d)",
                                   r.status)
                json = await r.text()
    except ClientError as e:
        raise APIError("Couldn't get VNL API maps") from e
    try:
        vnl_maps = _VNLMapList.validate_json(json)
    except ValidationError as e:
        raise APIError('Malformed VNL API maps') from e
    return {m.id: (m.tpTier, m.proTier) for m in vnl_maps}

async def _vnl_tiers_for_map(name: str) -> tuple[int, int]:
    url = f'https://vnlkz.com/api/maps/{quote(name)}'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status == 404:
                    return 10, 10
                elif r.status != 200:
                    raise APIError("Couldn't get VNL map tiers (HTTP %d)" %
                                   r.status)
                json = await r.text()
    except ClientError as e:
        raise APIConnectionError("Couldn't get VNL map tiers") from e
    try:
        vnl_map = _VNLMap.model_validate_json(json)
    except ValidationError as e:
        raise APIError('Malformed VNL API map') from e
    return vnl_map.tpTier, vnl_map.proTier

async def refresh_csgo_db_maps() -> None:
    _logger.info('Downloading CSGO map info')
    url = 'https://kztimerglobal.com/api/v2.0/maps?limit=9999'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    _logger.error("Couldn't get global API maps (HTTP %d)",
                                  r.status)
                    return
                json = await r.text()
    except ClientError:
        _logger.exception("Couldn't get global API maps")
        return

    try:
        api_maps = _APIMapList.validate_json(json)
    except ValidationError:
        _logger.exception('Malformed global API maps')
        return

    _logger.info('Downloading CSGO VNL map tiers')
    try:
        vnl_tiers: dict[int, tuple[int, int]] | None = await _vnl_tiers()
    except APIError:
        _logger.exception("Couldn't get VNL map tiers")
        vnl_tiers = None

    new = 0
    updated = 0
    deleted = 0
    for api_map in api_maps:
        async with in_transaction():
            if not api_map.validated:
                try:
                    db_map = await Map.get(map_id=api_map.id, is_cs2=False)
                except DoesNotExist:
                    pass
                else:
                    await db_map.delete()
                    deleted += 1
                continue

            if vnl_tiers is not None:
                vnl_tier, vnl_pro_tier = vnl_tiers.get(api_map.id, (10, 10))
            else:
                vnl_tier = vnl_pro_tier = None
            try:
                db_map = await Map.get(map_id=api_map.id, is_cs2=False)
            except DoesNotExist:
                await Map(map_id=api_map.id, is_cs2=False, name=api_map.name,
                          tier=api_map.difficulty,
                          pro_tier=api_map.difficulty, vnl_tier=vnl_tier,
                          vnl_pro_tier=vnl_pro_tier).save()
                new += 1
            else:
                changed = False
                if db_map.name != api_map.name:
                    _logger.info('Updating name for map %s', api_map.name)
                    db_map.name = api_map.name
                    changed = True
                if db_map.tier != api_map.difficulty:
                    _logger.info('Updating tier for map %s', api_map.name)
                    db_map.tier = db_map.pro_tier = api_map.difficulty
                    changed = True
                if db_map.vnl_tier != vnl_tier and vnl_tier is not None:
                    _logger.info('Updating VNL tier for map %s', api_map.name)
                    db_map.vnl_tier = vnl_tier
                    changed = True
                if (db_map.vnl_pro_tier != vnl_pro_tier and
                    vnl_pro_tier is not None):
                    _logger.info('Updating VNL pro tier for map %s',
                                 api_map.name)
                    db_map.vnl_pro_tier = vnl_pro_tier
                    changed = True
                if changed:
                    await db_map.save()
                    updated += 1
    _logger.info('Refreshed map database (%d new, %d updated, %d deleted)',
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
        return f'https://vnlkz.com/#/stats/{steamid64}'
    else:
        return (f'https://kzgo.eu/players/{steamid64}?'
                f'{quote_plus(mode.lower())}')

def _map_url(name: str, mode: Mode, stage: int) -> str:
    if stage != 0:
        return (f'https://kzgo.eu/maps/{quote(name)}?'
                f'{quote_plus(mode.lower())}&bonus={stage}')
    elif mode == Mode.VNL:
        return f'https://vnlkz.com/#/map/{quote(name)}'
    else:
        return f'https://kzgo.eu/maps/{quote(name)}?{quote_plus(mode.lower())}'

def _thumbnail_url(name: str) -> str:
    return ('https://raw.githubusercontent.com/KZGlobalTeam/map-images/'
            f'public/webp/medium/{quote(name)}.webp')

async def _records_for_steamid64(steamid64: int, mode: Mode,
                                 tp_type: Type=Type.ANY,
                                 map_name: str | None=None,
                                 stage: int | None=None,
                                 limit: int | None=None,
                                 timeout: int | None=None
                                 ) -> list[_APIRecord]:
    api_mode = {Mode.KZT: 'kz_timer', Mode.SKZ: 'kz_simple',
                Mode.VNL: 'kz_vanilla'}[mode]
    params: dict[str, str] = {'steamid64': str(steamid64), 'tickrate': '128',
                              'modes_list_string': api_mode}
    if stage is not None:
        params['stage'] = str(stage)
    if tp_type == Type.TP:
        params['has_teleports'] = 'true'
    elif tp_type == Type.PRO:
        params['has_teleports'] = 'false'
    if map_name is not None:
        params['map_name'] = map_name
    if limit is not None:
        params['limit'] = str(limit)
    else:
        params['limit'] = '9999'
    query = urlencode(params)
    url = f'https://kztimerglobal.com/api/v2.0/records/top?{query}'
    ctimeout = ClientTimeout(total=timeout) if timeout is not None else None
    try:
        async with ClientSession(timeout=ctimeout) as session:
            async with session.get(url) as r:
                if r.status != 200:
                    raise APIError("Couldn't get global API PBs (HTTP %d)" %
                                   r.status)
                json = await r.text()
    except ClientError as e:
        raise APIConnectionError("Couldn't get global API PBs") from e

    try:
        return _APIRecordList.validate_json(json)
    except ValidationError as e:
        raise APIError('Malformed global API PBs') from e

def _record_to_pb(record: _APIRecord, api_map: APIMap) -> PersonalBest:
    player_url = _profile_url(record.steamid64, api_map.mode)
    return PersonalBest(id=record.id, steamid64=record.steamid64,
                        player_name=record.player_name, player_url=player_url,
                        map=api_map, time=record.time,
                        teleports=record.teleports, points=record.points,
                        point_scale=1000, place=None, date=record.created_on)

async def _place_for_pb(pb: PersonalBest, timeout: int | None=None) -> int:
    url = f'https://kztimerglobal.com/api/v2.0/records/place/{pb.id}'
    ctimeout = ClientTimeout(total=timeout) if timeout is not None else None
    try:
        async with ClientSession(timeout=ctimeout) as session:
            async with session.get(url) as r:
                if r.status != 200:
                    raise APIError("Couldn't get global API PB place "
                                   '(HTTP %d)' % r.status)
                json = await r.text()
    except ClientError as e:
        raise APIConnectionError("Couldn't get global API PB place") from e
    try:
        return _APIPlace.validate_json(json)
    except ValidationError as e:
        raise APIError('Malformed global API place') from e

async def _record_for_map(api_map: APIMap, mode: Mode, tp_type: Type,
                          stage: int | None=None, timeout: int | None=None
                          ) -> _APIRecord | None:
    api_mode = {Mode.KZT: 'kz_timer', Mode.SKZ: 'kz_simple',
                Mode.VNL: 'kz_vanilla'}[mode]
    stage = stage or 0
    params: dict[str, str] = {'map_name': api_map.name, 'stage': str(stage),
                              'modes_list_string': api_mode, 'limit': '1'}
    if tp_type == Type.TP:
        params['has_teleports'] = 'true'
    elif tp_type == Type.PRO:
        params['has_teleports'] = 'false'
    query = urlencode(params)
    url = f'https://kztimerglobal.com/api/v2.0/records/top?{query}'
    ctimeout = ClientTimeout(total=timeout) if timeout is not None else None
    try:
        async with ClientSession(timeout=ctimeout) as session:
            async with session.get(url) as r:
                if r.status != 200:
                    raise APIError("Couldn't get global API WR "
                                   '(HTTP %d)' % r.status)
                json = await r.text()
    except ClientError as e:
        raise APIConnectionError("Couldn't get global API WR") from e
    try:
        records = _APIRecordList.validate_json(json)
    except ValidationError as e:
        raise APIError('Malformed global API PBs') from e
    return records[0] if records else None

class CSGOAPI(API):
    @override
    def has_tp_wrs(self) -> bool:
        return True

    @override
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
        else:
            json = {}
            url = f'https://kztimerglobal.com/api/v2.0/maps/name/{quote(name)}'
            ctimeout = (ClientTimeout(total=self.timeout)
                        if self.timeout is not None else None)
            try:
                async with ClientSession(timeout=ctimeout) as session:
                    async with session.get(url) as r:
                        if r.status != 200:
                            raise APIError("Couldn't get global API map "
                                           '(HTTP %d)' % r.status)
                        json = await r.text()
            except ClientError as e:
                raise APIConnectionError("Couldn't get global API map") from e

            try:
                api_map = _APIMapResult.validate_json(json)
            except ValidationError as e:
                raise APIError('Malformed global API map') from e
            if api_map is None:
                raise APIMapNotFoundError('Map not found')
            name = api_map.name
            tier = pro_tier = api_map.difficulty
            vnl_tier = vnl_pro_tier = None

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
                        _logger.exception("Couldn't get VNL map tiers")
                        tier = pro_tier = None
                else:
                    tier = vnl_tier
                    pro_tier = vnl_pro_tier
            tier_name = _tier_name(tier, self.mode)
            pro_tier_name = _tier_name(pro_tier, self.mode)

        url = _map_url(name, self.mode, bonus or 0)
        thumbnail_url = _thumbnail_url(name)

        return APIMap(name=name, mode=self.mode, bonus=bonus or None,
                      course=None, tier=tier, tier_name=tier_name,
                      pro_tier=pro_tier, pro_tier_name=pro_tier_name,
                      max_tier=max_tier, url=url, thumbnail_url=thumbnail_url)

    async def get_pb(self, steamid64: int, api_map: APIMap,
                     tp_type: Type=Type.ANY) -> PersonalBest | None:
        records = await _records_for_steamid64(steamid64, self.mode,
                                               map_name=api_map.name,
                                               stage=api_map.bonus or 0,
                                               limit=2, timeout=self.timeout)
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
            pb.place = await _place_for_pb(pb, timeout=self.timeout)
        except APIError:
            _logger.exception("Couldn't get global API PB place")
        return pbs[0]

    @override
    async def get_latest(self, steamid64: int, tp_type: Type=Type.ANY
                         ) -> PersonalBest | None:
        if tp_type in {Type.TP, Type.ANY}:
            records = await _records_for_steamid64(steamid64, self.mode,
                                                   stage=0, tp_type=Type.TP,
                                                   timeout=self.timeout)
        else:
            records = []
        if tp_type in {Type.PRO, Type.ANY}:
            pros = await _records_for_steamid64(steamid64, self.mode,
                                                stage=0, tp_type=Type.PRO,
                                                timeout=self.timeout)
        else:
            pros = []
        records += pros
        if not records:
            return None

        records.sort(key=lambda r: r.created_on, reverse=True)
        record = records[0]
        try:
            api_map = await self.get_map(record.map_name)
        except APIMapError as e:
            raise APIError('Invalid map name from API PB') from e
        pb = _record_to_pb(record, api_map)
        try:
            pb.place = await _place_for_pb(pb, timeout=self.timeout)
        except APIError:
            _logger.exception("Couldn't get global API PB place")
        return pb

    @override
    async def get_wrs(self, api_map: APIMap) -> list[PersonalBest]:
        bonus = api_map.bonus or 0
        records = [await _record_for_map(api_map, self.mode, tp_type, bonus)
                   for tp_type in (Type.TP, Type.PRO)]
        return [_record_to_pb(record, api_map)
                for record in records if record]

    @override
    async def get_profile(self, steamid64: int) -> Profile:
        player_url = _profile_url(steamid64, self.mode)
        api_mode_id = {Mode.KZT: '200', Mode.SKZ: '201',
                       Mode.VNL: '202'}[self.mode]
        params: dict[str, str] = {'steamid64s': str(steamid64), 'stages': '0',
                                  'mode_ids': api_mode_id, 'tickrates': '128'}
        query = urlencode(params)
        url = f'https://kztimerglobal.com/api/v2.0/player_ranks?{query}'
        ctimeout = (ClientTimeout(total=self.timeout)
                    if self.timeout is not None else None)
        try:
            async with ClientSession(timeout=ctimeout) as session:
                async with session.get(url) as r:
                    if r.status != 200:
                        raise APIError("Couldn't get global API ranks "
                                       '(HTTP %d)' % r.status)
                    json = await r.text()
        except ClientError as e:
            raise APIConnectionError("Couldn't get global API ranks") from e
        try:
            api_ranks = _APIPlayerRankList.validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API ranks') from e
        if not api_ranks:
            try:
                player_name = await name_for_steamid64(steamid64,
                                                       timeout=self.timeout)
            except SteamError:
                player_name = None
            return Profile(name=player_name, url=player_url, mode=self.mode,
                           rank=Rank.NEW, points=0, average=0)

        api_rank = api_ranks[0]

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
            if api_rank.points >= threshold:
                break
        return Profile(name=api_rank.player_name, url=player_url,
                       mode=self.mode, rank=rank, points=api_rank.points,
                       average=int(api_rank.average))
