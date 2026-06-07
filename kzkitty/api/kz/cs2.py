import logging
import re
from datetime import datetime, timedelta, UTC
from typing import override
from urllib.parse import quote, urlencode

from pydantic import BaseModel, ValidationError, UUID7
from tortoise.exceptions import DoesNotExist
from tortoise.transactions import in_transaction
from urllib3 import AsyncPoolManager
from urllib3.exceptions import HTTPError

from kzkitty.api.kz.base import (API, APIConnectionError, APIError, APIMap,
                                 APIMapAmbiguousError, APIMapError,
                                 APIMapNotFoundError, Rank,
                                 RefreshMapDBResult, PersonalBest, Profile)
from kzkitty.models import Course, Map, Mode, Type

_logger = logging.getLogger('kzkitty.api.kz.cs2')

class _APICourseFilter(BaseModel):
    nub_tier: str
    pro_tier: str

class _APICourseFilters(BaseModel):
    classic: _APICourseFilter
    vanilla: _APICourseFilter

class _APICourse(BaseModel):
    name: str
    filters: _APICourseFilters

class _APIMap(BaseModel):
    id: int
    name: str
    state: str
    courses: list[_APICourse]

class _APIMapResults(BaseModel):
    values: list[_APIMap]

class _APIPlayer(BaseModel):
    id: str
    name: str

class _ShallowAPIMap(BaseModel):
    name: str

class _ShallowAPICourse(BaseModel):
    name: str

class _APIRecord(BaseModel):
    id: UUID7
    player: _APIPlayer
    map: _ShallowAPIMap
    course: _ShallowAPICourse
    teleports: int
    time: timedelta
    nub_points: float | None
    nub_rank: int | None
    pro_points: float | None
    pro_rank: int | None

class _APIRecordResults(BaseModel):
    values: list[_APIRecord]

class _APIProfile(BaseModel):
    name: str
    ckz_rating: float
    vnl_rating: float

def _tier_num(name: str) -> int | None:
    return {'very-easy': 1, 'easy': 2, 'medium': 3, 'advanced': 4,
            'hard': 5, 'very-hard': 6, 'extreme': 7, 'death': 8,
            'unfeasible': 9, 'impossible': 10}.get(name)

def _tier_code(tier: int | None) -> str:
    return {1: 'very-easy', 2: 'easy', 3: 'medium', 4: 'advanced',
            5: 'hard', 6: 'very-hard', 7: 'extreme', 8: 'death',
            9: 'unfeasible', 10: 'impossible'}.get(tier or -1, 'unknown')

def _tier_name(tier: int | None) -> str:
    return {1: 'Very Easy', 2: 'Easy', 3: 'Medium', 4: 'Advanced',
            5: 'Hard', 6: 'Very Hard', 7: 'Extreme', 8: 'Death',
            9: 'Unfeasible', 10: 'Impossible'}.get(tier or -1, 'Unknown')

def _steamid_to_steamid64(steamid: str) -> int:
    parts = steamid.split(':')
    if len(parts) != 3:
        raise ValueError
    account_id = int(parts[2]) * 2
    if parts[1] == '1':
        account_id += 1
    universe = account_type = instance = 1
    return ((universe << 56) | (account_type << 52) | (instance << 32) |
            account_id)

def _profile_url(steamid64: int) -> str:
    return f'https://cs2kz.org/profile/{steamid64}'

def _record_to_pb(record: _APIRecord, api_map: APIMap) -> PersonalBest:
    try:
        steamid64 = _steamid_to_steamid64(record.player.id)
    except ValueError as e:
        raise APIError('Malformed global API Steam ID') from e
    player_url = _profile_url(steamid64)
    if record.teleports == 0:
        points = record.pro_points
        place = record.pro_rank
    else:
        points = record.nub_points
        place = record.nub_rank
    if not isinstance(points, float) or not isinstance(place, int):
        raise APIError('Malformed global API PB')
    submitted_at = datetime.fromtimestamp(record.id.time / 1000.0, tz=UTC)
    return PersonalBest(id=record.id.int, steamid64=steamid64,
                        player_name=record.player.name, player_url=player_url,
                        map=api_map, time=record.time,
                        teleports=record.teleports, points=int(points),
                        point_scale=10000, place=place, date=submitted_at)

class CS2API(API):
    @override
    def __init__(self, timeout: int | None=None) -> None:
        headers = {'User-Agent': 'kzkitty/0.1'}
        self._session: AsyncPoolManager = AsyncPoolManager(headers=headers,
                                                           timeout=timeout)

    @override
    async def close(self) -> None:
        await self._session.clear()

    @override
    async def refresh_map_db(self) -> RefreshMapDBResult:
        url = 'https://api.cs2kz.org/maps'
        try:
            r = await self._session.request('GET', url)
            if r.status != 200:
                raise APIError("Couldn't get global API maps "
                               f'(HTTP {r.status})')
            json = await r.data
        except HTTPError as e:
            raise APIError("Couldn't get global API maps") from e

        try:
            results = _APIMapResults.model_validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API maps') from e

        new = updated = deleted = 0
        for api_map in results.values:
            async with in_transaction():
                if api_map.state != 'approved':
                    try:
                        db_map = await Map.get(map_id=api_map.id, is_cs2=True)
                    except DoesNotExist:
                        pass
                    else:
                        _logger.info('Deleting map %s', api_map.name)
                        await Course.filter(map_id=api_map.id).delete()
                        await db_map.delete()
                        deleted += 1
                    continue

                is_new = False
                is_updated = False
                try:
                    db_map = await Map.get(map_id=api_map.id, is_cs2=True)
                except DoesNotExist:
                    _logger.info('Adding map %s', api_map.name)
                    await Map(map_id=api_map.id, is_cs2=True,
                              name=api_map.name).save()
                    is_new = True
                else:
                    changed = False
                    if db_map.name != api_map.name:
                        _logger.info('Updating name for map %s', api_map.name)
                        db_map.name = api_map.name
                        changed = True
                    if changed:
                        await db_map.save()
                        is_updated = True

                db_api_courses = []
                db_courses = (await Course.filter(map_id=api_map.id)
                                          .order_by('course_id'))
                for db_course in db_courses:
                    classic = _APICourseFilter(
                        nub_tier=_tier_code(db_course.tier),
                        pro_tier=_tier_code(db_course.pro_tier))
                    vanilla = _APICourseFilter(
                        nub_tier=_tier_code(db_course.vnl_tier),
                        pro_tier=_tier_code(db_course.vnl_pro_tier))
                    filters = _APICourseFilters(classic=classic,
                                                vanilla=vanilla)
                    db_api_courses.append(_APICourse(name=db_course.name,
                                                     filters=filters))

                if api_map.courses != db_api_courses:
                    if not is_new:
                        _logger.info('Updating courses for map %s',
                                     api_map.name)
                    await Course.filter(map_id=api_map.id).delete()
                    for course_id, course in enumerate(api_map.courses,
                                                       start=1):
                        filters = course.filters
                        tier = _tier_num(filters.classic.nub_tier)
                        pro_tier = _tier_num(filters.classic.pro_tier)
                        vnl_tier = _tier_num(filters.vanilla.nub_tier)
                        vnl_pro_tier = _tier_num(filters.vanilla.pro_tier)
                        await Course(name=course.name, course_id=course_id,
                                     map_id=api_map.id, tier=tier,
                                     pro_tier=pro_tier, vnl_tier=vnl_tier,
                                     vnl_pro_tier=vnl_pro_tier).save()
                    if not is_new:
                        is_updated = True

                if is_new:
                    new += 1
                elif is_updated:
                    updated += 1

        return RefreshMapDBResult(new, updated, deleted)

    async def _top_record(self, mode: Mode, latest: bool=True,
                          steamid64: int | None=None,
                          api_map: APIMap | None=None,
                          tp_type: Type | None=None) -> _APIRecord | None:
        api_mode_id = {Mode.CKZ: 'classic', Mode.VNL2: 'vanilla'}[mode]
        params: dict[str, str] = {'mode': api_mode_id, 'top': 'true',
                                  'limit': '1'}
        if steamid64 is not None:
            params['player'] = str(steamid64)
        if api_map is not None:
            if api_map.course is None:
                raise APIError('Map has no course')
            params['map'] = api_map.name
            params['course'] = api_map.course
        if tp_type == Type.TP:
            params['has_teleports'] = 'true'
        elif tp_type == Type.PRO:
            params['has_teleports'] = 'false'
        if latest:
            params['sort_by'] = 'submission-date'
            params['sort_order'] = 'descending'
        else:
            params['sort_by'] = 'time'
            params['sort_order'] = 'ascending'
        query = urlencode(params)
        url = f'https://api.cs2kz.org/records?{query}'
        try:
            r = await self._session.request('GET', url)
            if r.status != 200:
                raise APIError("Couldn't get global API PBs "
                               f'(HTTP {r.status})')
            json = await r.data
        except HTTPError as e:
            raise APIConnectionError("Couldn't get global API PBs") from e

        try:
            records = _APIRecordResults.model_validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API PBs') from e
        return records.values[0] if records.values else None

    @override
    async def get_map(self, name: str, mode: Mode, course: str | None=None,
                      bonus: int | None=None) -> APIMap:
        if bonus is not None:
            raise APIMapError("Bonuses aren't supported on CS2. "
                              'Did you mean to specify a course?')
        if not re.fullmatch('[A-za-z0-9_]+', name):
            raise APIMapError('Invalid map name')

        db_map = None
        try:
            db_map = await Map.get(name__iexact=name, is_cs2=True)
        except DoesNotExist:
            db_maps = list(await Map.filter(name__icontains=name,
                                            is_cs2=True))
            if len(db_maps) > 1:
                raise APIMapAmbiguousError(db_maps)
            elif db_maps:
                db_map = db_maps[0]

        course_id = course_name = tier = pro_tier = None
        if db_map is not None:
            name = db_map.name
            if course is None:
                db_course = await Course.filter(course_id=1,
                                                map_id=db_map.map_id).first()
            else:
                db_course = await Course.filter(name__icontains=course,
                                                map_id=db_map.map_id).first()
            if db_course is not None:
                course_id = db_course.course_id
                course_name = db_course.name
                if mode == Mode.VNL2:
                    tier = db_course.vnl_tier
                    pro_tier = db_course.vnl_pro_tier
                else:
                    tier = db_course.tier
                    pro_tier = db_course.pro_tier

        if course_id is None:
            url = f'https://api.cs2kz.org/maps/{quote(name)}'
            try:
                r = await self._session.request('GET', url)
                if r.status == 404:
                    raise APIMapNotFoundError('Map not found')
                elif r.status != 200:
                    raise APIError("Couldn't get global API map "
                                   f'(HTTP {r.status})')
                json = await r.data
            except HTTPError as e:
                raise APIConnectionError("Couldn't get global API map") from e

            try:
                api_map = _APIMap.model_validate_json(json)
            except ValidationError as e:
                raise APIError('Malformed global API map') from e

            courses = api_map.courses
            if course is None:
                if courses:
                    course_id = 1
                    course_info = courses[0]
                    course_name = course_info.name
                else:
                    raise APIMapError('Map has no courses')
            else:
                course = course.lower()
                for course_id, course_info in enumerate(courses, start=1):
                    course_name = course_info.name
                    if course in course_name.lower():
                        break
                else:
                    raise APIMapError('Map course not found')

            course_filters = course_info.filters
            course_filter = {Mode.CKZ: course_filters.classic,
                             Mode.VNL2: course_filters.vanilla}[mode]
            tier = _tier_num(course_filter.nub_tier)
            pro_tier = _tier_num(course_filter.pro_tier)

        tier_name = _tier_name(tier)
        pro_tier_name = _tier_name(pro_tier)

        url = f'https://cs2kz.org/maps/{quote(name)}'
        thumbnail_url = ('https://raw.githubusercontent.com/KZGlobalTeam/'
                         'cs2kz-images/public/webp/medium/'
                         f'{quote(name)}/{course_id}.webp')

        return APIMap(name=name, mode=mode, bonus=None,
                      course=course_name, tier=tier, tier_name=tier_name,
                      pro_tier=pro_tier, pro_tier_name=pro_tier_name,
                      max_tier=10, has_tp_wrs=False, url=url,
                      impossible=tier == 10 and pro_tier == 10,
                      thumbnail_url=thumbnail_url)

    @override
    async def get_pb(self, steamid64: int, api_map: APIMap,
                     tp_type: Type=Type.ANY) -> PersonalBest | None:
        record = await self._top_record(mode=api_map.mode,
                                        steamid64=steamid64, api_map=api_map,
                                        tp_type=tp_type)
        if record is None:
            return None
        return _record_to_pb(record, api_map)

    @override
    async def get_latest(self, steamid64: int, mode: Mode,
                         tp_type: Type=Type.ANY) -> PersonalBest | None:
        record = await self._top_record(mode=mode, steamid64=steamid64,
                                        tp_type=tp_type)
        if record is None:
            return None
        api_map = await self.get_map(record.map.name, mode,
                                     course=record.course.name)
        return _record_to_pb(record, api_map)

    @override
    async def get_wrs(self, api_map: APIMap) -> list[PersonalBest]:
        record = await self._top_record(mode=api_map.mode, api_map=api_map,
                                        latest=False)
        if record is None:
            return []
        pbs = [_record_to_pb(record, api_map)]
        pro_rank = record.pro_rank
        if pro_rank is not None:
            return pbs
        pro_record = await self._top_record(mode=api_map.mode,
                                            api_map=api_map, tp_type=Type.PRO,
                                            latest=False)
        if pro_record is not None:
            pbs.append(_record_to_pb(pro_record, api_map))
        return pbs

    @override
    async def get_profile(self, steamid64: int, mode: Mode) -> Profile:
        player_url = _profile_url(steamid64)
        url = f'https://api.cs2kz.org/players/{steamid64}'
        try:
            r = await self._session.request('GET', url)
            if r.status == 200:
                json = await r.data
            elif r.status == 404:
                return Profile(name=None, url=player_url,
                               mode=mode, rank=Rank.UNKNOWN,
                               points=0, average=None)
            else:
                raise APIError("Couldn't get global API profile "
                               f'(HTTP {r.status})')
        except HTTPError as e:
            raise APIConnectionError("Couldn't get global API PBs") from e

        try:
            profile = _APIProfile.model_validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API profile') from e

        rating = {Mode.CKZ: profile.ckz_rating,
                  Mode.VNL2: profile.vnl_rating}[mode]
        points = rating / 10
        if points == 0.0:
            rank = Rank.NEW
        else:
            thresholds = [(37500.0, Rank.LEGEND),
                          (35000.0, Rank.MASTER),
                          (30000.0, Rank.PRO),
                          (25000.0, Rank.SEMIPRO),
                          (20000.0, Rank.EXPERT),
                          (15000.0, Rank.SKILLED),
                          (10000.0, Rank.REGULAR),
                          (5000.0, Rank.CASUAL),
                          (0.0, Rank.BEGINNER)]
            rank = Rank.BEGINNER
            for threshold, rank in thresholds:
                if points >= threshold:
                    break
        return Profile(name=profile.name, url=player_url, mode=mode,
                       rank=rank, points=int(points), average=None)
