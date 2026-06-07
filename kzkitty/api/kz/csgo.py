import logging
import re
from datetime import datetime, timedelta, UTC
from typing import Annotated, override
from urllib.parse import quote, quote_plus, urlencode

from pydantic import (AfterValidator, BaseModel, Field, TypeAdapter,
                      ValidationError)
from tortoise.exceptions import DoesNotExist
from tortoise.transactions import in_transaction
from urllib3 import AsyncPoolManager
from urllib3.exceptions import HTTPError

from kzkitty.api.kz.base import (API, APIConnectionError, APIError, APIMap,
                                 APIMapAmbiguousError, APIMapError,
                                 APIMapNotFoundError, Rank,
                                 RefreshMapDBResult, PersonalBest, Profile)
from kzkitty.models import Map, Mode, Type

_logger = logging.getLogger('kzkitty.api.kz.csgo')

class _APIMap(BaseModel):
    id: int
    name: str
    difficulty: int
    validated: bool

class _APIMapRecordFilter(BaseModel):
    id: int
    map_id: int
    stage: int
    mode_id: int
    tickrate: int
    has_teleports: bool

class _VNLMap(BaseModel):
    id: int
    tp_tier: int = Field(alias='tpTier')
    pro_tier: int = Field(alias='proTier')

def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)

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

_APIMapResult: TypeAdapter[_APIMap | None] = TypeAdapter(_APIMap | None)
_APIMapList = TypeAdapter(list[_APIMap])
_VNLMapList = TypeAdapter(list[_VNLMap])
_APIMapRecordFilterList = TypeAdapter(list[_APIMapRecordFilter])
_APIRecordList = TypeAdapter(list[_APIRecord])
_APIPlayerRankList = TypeAdapter(list[_APIPlayerRank])
_APIPlace = TypeAdapter(int)

class _GOKZTopRecord(BaseModel):
    map_name: str
    stage: int
    teleports: int

_GOKZTopRecordList = TypeAdapter(list[_GOKZTopRecord])

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

def _record_to_pb(record: _APIRecord, api_map: APIMap, place: int | None=None
                  ) -> PersonalBest:
    player_url = _profile_url(record.steamid64, api_map.mode)
    return PersonalBest(id=record.id, steamid64=record.steamid64,
                        player_name=record.player_name, player_url=player_url,
                        map=api_map, time=record.time,
                        teleports=record.teleports, points=record.points,
                        point_scale=1000, place=place, date=record.created_on)

class CSGOAPI(API):
    @override
    def __init__(self, timeout: int | None=None) -> None:
        headers = {'User-Agent': 'kzkitty/0.1'}
        self._session: AsyncPoolManager = AsyncPoolManager(headers=headers,
                                                           timeout=timeout)

    @override
    async def close(self) -> None:
        await self._session.clear()

    async def _skz_filters(self, api_maps: list[_APIMap]) -> dict[int, bool]:
        url = ('https://kztimerglobal.com/api/v2.0/record_filters?'
               'mode_ids=201&stages=0&tickrates=128&has_teleports=false&'
               'limit=9999')
        try:
            r = await self._session.request('GET', url)
            if r.status != 200:
                raise APIError("Couldn't get global API SKZ record filters "
                               f'(HTTP {r.status})')
            json = await r.data
        except HTTPError as e:
            raise APIError("Couldn't get global API SKZ record filters") from e
        try:
            filters = _APIMapRecordFilterList.validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API map record filters') from e
        skz_maps = {f.map_id for f in filters}
        return {m.id: m.id in skz_maps for m in api_maps}

    async def _vnl_tiers_for_map(self, map_name: str) -> tuple[int, int]:
        url = f'https://vnlkz.com/api/maps/{quote(map_name)}'
        try:
            r = await self._session.request('GET', url)
            if r.status == 404:
                return 10, 10
            elif r.status != 200:
                raise APIError("Couldn't get VNL map tiers "
                               f'(HTTP {r.status})')
            json = await r.data
        except HTTPError as e:
            raise APIConnectionError("Couldn't get VNL map tiers") from e
        try:
            vnl_map = _VNLMap.model_validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed VNL API map') from e
        return vnl_map.tp_tier, vnl_map.pro_tier

    async def _vnl_tiers(self) -> dict[int, tuple[int, int]]:
        url = 'https://vnlkz.com/api/maps'
        try:
            r = await self._session.request('GET', url)
            if r.status != 200:
                raise APIError("Couldn't get VNL API maps "
                               f'(HTTP {r.status})')
            json = await r.data
        except HTTPError as e:
            raise APIError("Couldn't get VNL API maps") from e
        try:
            vnl_maps = _VNLMapList.validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed VNL API maps') from e
        return {m.id: (m.tp_tier, m.pro_tier) for m in vnl_maps}

    @override
    async def refresh_map_db(self) -> RefreshMapDBResult:
        url = 'https://kztimerglobal.com/api/v2.0/maps?limit=9999'
        try:
            r = await self._session.request('GET', url)
            if r.status != 200:
                raise APIError("Couldn't get global API maps "
                               f'(HTTP {r.status})')
            json = await r.data
        except HTTPError as e:
            raise APIError("Couldn't get global API maps") from e

        try:
            api_maps = _APIMapList.validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API maps') from e

        skz_filters: dict[int, bool] = {}
        try:
            skz_filters = await self._skz_filters(api_maps)
        except APIError:
            _logger.exception("Couldn't get SKZ map record filters")

        vnl_tiers: dict[int, tuple[int, int]] | None = None
        try:
            vnl_tiers = await self._vnl_tiers()
        except APIError:
            _logger.exception("Couldn't get VNL map tiers")

        new = updated = deleted = 0
        for api_map in api_maps:
            async with in_transaction():
                if not api_map.validated:
                    try:
                        db_map = await Map.get(map_id=api_map.id,
                                               is_cs2=False)
                    except DoesNotExist:
                        pass
                    else:
                        _logger.info('Deleting map %s', api_map.name)
                        await db_map.delete()
                        deleted += 1
                    continue

                vnl_tier: int | None
                vnl_pro_tier: int | None
                if vnl_tiers is not None:
                    vnl_tier, vnl_pro_tier = vnl_tiers.get(api_map.id,
                                                           (10, 10))
                else:
                    vnl_tier = vnl_pro_tier = None
                try:
                    db_map = await Map.get(map_id=api_map.id, is_cs2=False)
                except DoesNotExist:
                    _logger.info('Adding map %s', api_map.name)
                    await Map(map_id=api_map.id, is_cs2=False,
                              name=api_map.name, tier=api_map.difficulty,
                              pro_tier=api_map.difficulty, vnl_tier=vnl_tier,
                              vnl_pro_tier=vnl_pro_tier,
                              skz_possible=skz_filters.get(api_map.id)).save()
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
                        _logger.info('Updating VNL tier for map %s',
                                     api_map.name)
                        db_map.vnl_tier = vnl_tier
                        changed = True
                    if (db_map.vnl_pro_tier != vnl_pro_tier and
                        vnl_pro_tier is not None):
                        _logger.info('Updating VNL pro tier for map %s',
                                     api_map.name)
                        db_map.vnl_pro_tier = vnl_pro_tier
                        changed = True
                    skz_possible = skz_filters.get(api_map.id)
                    if db_map.skz_possible != skz_possible:
                        _logger.info('Updating SKZ possible status for map %s',
                                     api_map.name)
                        db_map.skz_possible = skz_possible
                        changed = True
                    if changed:
                        await db_map.save()
                        updated += 1
        return RefreshMapDBResult(new, updated, deleted)

    async def _gokz_top_for_steamid64(self, steamid64: int, mode: Mode,
                                      tp_type: Type=Type.ANY,
                                      stage: int | None=None
                                      ) -> _GOKZTopRecord | None:
        api_mode = {Mode.KZT: 'KZT', Mode.SKZ: 'SKZ', Mode.VNL: 'VNL'}[mode]
        params: dict[str, str] = {'identifier': str(steamid64),
                                  'scope': api_mode,
                                  'sort_by': 'created_at',
                                  'sort_order': 'desc',
                                  'limit': '1'}
        if stage is not None:
            params['stage'] = str(stage)
        if tp_type == Type.TP:
            params['type'] = 'NUB'
        elif tp_type == Type.PRO:
            params['type'] = 'PRO'
        query = urlencode(params)
        url = f'https://api.gokz.top/v1/records/pb?{query}'
        try:
            r = await self._session.request('GET', url)
            if r.status != 200:
                raise APIError(f"Couldn't get gokz.top PBs (HTTP {r.status})")
            json = await r.data
        except HTTPError as e:
            raise APIConnectionError("Couldn't get gokz.top PBs") from e

        try:
            records = _GOKZTopRecordList.validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed gokz.top PBs') from e
        return records[0] if records else None

    async def _records_for_steamid64(self, steamid64: int, mode: Mode,
                                     tp_type: Type=Type.ANY,
                                     map_name: str | None=None,
                                     stage: int | None=None,
                                     limit: int | None=None,
                                     ) -> list[_APIRecord]:
        api_mode = {Mode.KZT: 'kz_timer', Mode.SKZ: 'kz_simple',
                    Mode.VNL: 'kz_vanilla'}[mode]
        params: dict[str, str] = {'steamid64': str(steamid64),
                                  'tickrate': '128',
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
        try:
            r = await self._session.request('GET', url)
            if r.status != 200:
                raise APIError("Couldn't get global API PBs "
                               f'(HTTP {r.status})')
            json = await r.data
        except HTTPError as e:
            raise APIConnectionError("Couldn't get global API PBs") from e

        try:
            return _APIRecordList.validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API PBs') from e

    async def _record_for_map(self, api_map: APIMap, tp_type: Type,
                              stage: int | None=None) -> _APIRecord | None:
        api_mode = {Mode.KZT: 'kz_timer', Mode.SKZ: 'kz_simple',
                    Mode.VNL: 'kz_vanilla'}[api_map.mode]
        stage = stage or 0
        params: dict[str, str] = {'map_name': api_map.name,
                                  'stage': str(stage),
                                  'modes_list_string': api_mode,
                                  'limit': '1'}
        if tp_type == Type.TP:
            params['has_teleports'] = 'true'
        elif tp_type == Type.PRO:
            params['has_teleports'] = 'false'
        query = urlencode(params)
        url = f'https://kztimerglobal.com/api/v2.0/records/top?{query}'
        try:
            r = await self._session.request('GET', url)
            if r.status != 200:
                raise APIError("Couldn't get global API WR "
                               f'(HTTP {r.status})')
            json = await r.data
        except HTTPError as e:
            raise APIConnectionError("Couldn't get global API WR") from e
        try:
            records = _APIRecordList.validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API PBs') from e
        return records[0] if records else None

    async def _place_for_pb(self, pb: PersonalBest) -> int:
        url = f'https://kztimerglobal.com/api/v2.0/records/place/{pb.id}'
        try:
            r = await self._session.request('GET', url)
            if r.status != 200:
                raise APIError("Couldn't get global API PB place "
                               f'(HTTP {r.status})')
            json = await r.data
        except HTTPError as e:
            raise APIConnectionError("Couldn't get global API PB place") from e
        try:
            return _APIPlace.validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API place') from e

    @override
    async def get_map(self, name: str, mode: Mode, course: str | None=None,
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
            skz_possible = db_map.skz_possible
        else:
            url = f'https://kztimerglobal.com/api/v2.0/maps/name/{quote(name)}'
            try:
                r = await self._session.request('GET', url)
                if r.status != 200:
                    raise APIError("Couldn't get global API map "
                                   f'(HTTP {r.status})')
                json = await r.data
            except HTTPError as e:
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

            url = ('https://kztimerglobal.com/api/v2.0/record_filters?'
                   f'map_ids={api_map.id}&mode_ids=201&stages=0&tickrates=128'
                   '&has_teleports=false&limit=1')
            try:
                r = await self._session.request('GET', url)
                if r.status != 200:
                    _logger.error("Couldn't get global API SKZ record filter "
                                  '(HTTP %s)', r.status)
                    skz_possible = None
                json = await r.data
            except HTTPError:
                _logger.exception("Couldn't get global API SKZ record filter")
                skz_possible = None
            else:
                try:
                    filters = _APIMapRecordFilterList.validate_json(json)
                except ValidationError:
                    _logger.exception('Malformed global API map record filter')
                    skz_possible = None
                else:
                    skz_possible = bool(filters)

        if course is not None:
            raise APIMapError("Courses aren't supported on CS:GO. "
                              'Did you mean to specify a bonus?')
        bonus = bonus or None

        max_tier = 7
        if bonus:
            # Knowing if a bonus is possible would require downloading the
            # map's record filters from the API, but it doesn't have a way of
            # easily getting the filters for every bonus.
            tier = tier_name = pro_tier = pro_tier_name = impossible = None
        else:
            if mode == Mode.VNL:
                max_tier = 10
                if vnl_tier is None or vnl_pro_tier is None:
                    try:
                        tier, pro_tier = await self._vnl_tiers_for_map(name)
                    except APIError:
                        _logger.exception("Couldn't get VNL map tiers")
                        tier = pro_tier = None
                else:
                    tier = vnl_tier
                    pro_tier = vnl_pro_tier
                impossible = (tier == 10 and pro_tier == 10
                              if tier is not None and pro_tier is not None
                              else None)
            elif name.startswith('vnl_'): # noqa: SIM114
                impossible = True
            elif name.startswith('skz_') and mode != Mode.SKZ:
                impossible = True
            elif mode == mode.SKZ:
                impossible = (not skz_possible
                              if skz_possible is not None else None)
            else:
                impossible = False
            tier_name = _tier_name(tier, mode)
            pro_tier_name = _tier_name(pro_tier, mode)

        url = _map_url(name, mode, bonus or 0)
        thumbnail_url = _thumbnail_url(name)

        return APIMap(name=name, mode=mode, bonus=bonus or None,
                      course=None, tier=tier, tier_name=tier_name,
                      pro_tier=pro_tier, pro_tier_name=pro_tier_name,
                      max_tier=max_tier, impossible=impossible,
                      has_tp_wrs=True, url=url, thumbnail_url=thumbnail_url)

    @override
    async def get_pb(self, steamid64: int, api_map: APIMap,
                     tp_type: Type=Type.ANY) -> PersonalBest | None:
        records = await self._records_for_steamid64(steamid64, api_map.mode,
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
            pb.place = await self._place_for_pb(pb)
        except APIError:
            _logger.exception("Couldn't get global API PB place")
        return pbs[0]

    @override
    async def get_latest(self, steamid64: int, mode: Mode,
                         tp_type: Type=Type.ANY) -> PersonalBest | None:
        # Attempt to get the latest PB from gokz.top before asking the global
        # API directly. gokz.top is able to return times for recently released
        # maps, whereas the global API has an issue that prevents them from
        # showing up in certain areas of the API (like when asking for the
        # latest runs for a user).
        try:
            gokz_top = await self._gokz_top_for_steamid64(steamid64, mode,
                                                          stage=0,
                                                          tp_type=tp_type)
        except APIError:
            _logger.exception("Couldn't get latest PB from gokz.top")
        else:
            if gokz_top is not None:
                try:
                    api_map = await self.get_map(gokz_top.map_name, mode,
                                                 bonus=gokz_top.stage)
                except APIMapError:
                    _logger.exception("Invalid map name from gokz.top")
                else:
                    tp_type = Type.PRO if gokz_top.teleports == 0 else Type.TP
                    return await self.get_pb(steamid64, api_map, tp_type)

        records: list[_APIRecord]
        pros: list[_APIRecord]
        if tp_type in {Type.TP, Type.ANY}:
            records = await self._records_for_steamid64(steamid64, mode,
                                                        stage=0,
                                                        tp_type=Type.TP)
        else:
            records = []
        if tp_type in {Type.PRO, Type.ANY}:
            pros = await self._records_for_steamid64(steamid64, mode,
                                                     stage=0,
                                                     tp_type=Type.PRO)
        else:
            pros = []
        records += pros
        if not records:
            return None

        records.sort(key=lambda r: r.created_on, reverse=True)
        record = records[0]
        try:
            api_map = await self.get_map(record.map_name, mode)
        except APIMapError as e:
            raise APIError('Invalid map name from API PB') from e
        pb = _record_to_pb(record, api_map)
        try:
            pb.place = await self._place_for_pb(pb)
        except APIError:
            _logger.exception("Couldn't get global API PB place")
        return pb

    @override
    async def get_wrs(self, api_map: APIMap) -> list[PersonalBest]:
        bonus = api_map.bonus or 0
        records = [await self._record_for_map(api_map, tp_type, bonus)
                   for tp_type in (Type.TP, Type.PRO)]
        return [_record_to_pb(record, api_map, place=1)
                for record in records if record]

    @override
    async def get_profile(self, steamid64: int, mode: Mode) -> Profile:
        player_url = _profile_url(steamid64, mode)
        api_mode_id = {Mode.KZT: '200', Mode.SKZ: '201',
                       Mode.VNL: '202'}[mode]
        params: dict[str, str] = {'steamid64s': str(steamid64), 'stages': '0',
                                  'mode_ids': api_mode_id, 'tickrates': '128'}
        query = urlencode(params)
        url = f'https://kztimerglobal.com/api/v2.0/player_ranks?{query}'
        try:
            r = await self._session.request('GET', url)
            if r.status != 200:
                raise APIError("Couldn't get global API ranks "
                               f'(HTTP {r.status})')
            json = await r.data
        except HTTPError as e:
            raise APIConnectionError("Couldn't get global API ranks") from e
        try:
            api_ranks = _APIPlayerRankList.validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API ranks') from e
        if not api_ranks:
            return Profile(name=None, url=player_url, mode=mode,
                           rank=Rank.NEW, points=0, average=0)

        api_rank = api_ranks[0]

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
        rank = Rank.NEW
        for threshold, rank in thresholds:
            if api_rank.points >= threshold:
                break
        return Profile(name=api_rank.player_name, url=player_url,
                       mode=mode, rank=rank, points=api_rank.points,
                       average=int(api_rank.average))
