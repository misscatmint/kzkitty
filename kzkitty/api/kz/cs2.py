import logging
import re
from datetime import datetime, timedelta, timezone
from typing import override

from aiohttp import ClientError, ClientSession
from pydantic import BaseModel, ValidationError, UUID7
from tortoise.exceptions import DoesNotExist

from kzkitty.api.kz.base import (API, APIConnectionError, APIError, APIMap,
                                 APIMapAmbiguousError, APIMapError,
                                 APIMapNotFoundError, Rank, PersonalBest,
                                 Profile)
from kzkitty.api.steam import SteamError, name_for_steamid64
from kzkitty.models import Map, Mode, Type

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

async def _thumbnail_for_map(name: str, course_id: int=1) -> bytes | None:
    thumbnail_url = ('https://raw.githubusercontent.com/KZGlobalTeam/'
                     f'cs2kz-images/public/webp/medium/{name}/'
                     f'{course_id}.webp')
    thumbnail = None
    try:
        async with ClientSession() as session:
            async with session.get(thumbnail_url) as r:
                if r.status == 200:
                    thumbnail = await r.content.read()
                else:
                    _logger.error("Couldn't get map thumbnail (HTTP %d)",
                                  r.status)
    except ClientError:
        _logger.exception("Couldn't get map thumbnail")
    return thumbnail

def _tier_num(name: str) -> int | None:
    return {'very-easy': 1, 'easy': 2, 'medium': 3, 'advanced': 4,
            'hard': 5, 'very-hard': 6, 'extreme': 7, 'death': 8,
            'unfeasible': 9, 'impossible': 10}.get(name)

def _tier_name(tier: int | None) -> str:
    return {1: 'Very Easy', 2: 'Easy', 3: 'Medium', 4: 'Advanced',
            5: 'Hard', 6: 'Very Hard', 7: 'Extreme', 8: 'Death',
            9: 'Unfeasible', 10: 'Impossible'}.get(tier or -1, 'Unknown')

async def refresh_cs2_db_maps() -> None:
    _logger.info('Downloading CS2 map tiers')
    url = 'https://api.cs2kz.org/maps'
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
        results = _APIMapResults.model_validate_json(json)
    except ValidationError:
        _logger.exception('Malformed global API maps')
        return

    new = 0
    updated = 0
    deleted = 0
    for api_map in results.values:
        if api_map.state != 'approved':
            try:
                db_map = await Map.get(map_id=api_map.id, is_cs2=True)
            except DoesNotExist:
                pass
            else:
                await db_map.delete()
                deleted += 1
            continue

        tier = pro_tier = vnl_tier = vnl_pro_tier = course_name = None
        if api_map.courses:
            course = api_map.courses[0]
            course_name = course.name
            filters = course.filters
            tier = _tier_num(filters.classic.nub_tier)
            pro_tier = _tier_num(filters.classic.pro_tier)
            vnl_tier = _tier_num(filters.vanilla.nub_tier)
            vnl_pro_tier = _tier_num(filters.vanilla.pro_tier)

        try:
            db_map = await Map.get(map_id=api_map.id, is_cs2=True)
        except DoesNotExist:
            _logger.info('Downloading thumbnail for map %s', api_map.name)
            thumbnail = await _thumbnail_for_map(api_map.name)
            await Map(map_id=api_map.id, is_cs2=True, name=api_map.name,
                      tier=tier, pro_tier=pro_tier, vnl_tier=vnl_tier,
                      vnl_pro_tier=vnl_pro_tier, main_course=course_name,
                      thumbnail=thumbnail).save()
            new += 1
        else:
            thumbnail = db_map.thumbnail
            if thumbnail is None:
                thumbnail = await _thumbnail_for_map(api_map.name)
            changed = False
            if db_map.name != api_map.name:
                _logger.info('Updating name for map %s', api_map.name)
                db_map.name = api_map.name
                changed = True
            if db_map.main_course != course_name and course_name is not None:
                _logger.info('Updating main course for map %s', api_map.name)
                db_map.main_course = course_name
                changed = True
            if db_map.tier != tier and tier is not None:
                _logger.info('Updating tier for map %s', api_map.name)
                db_map.tier = tier
                changed = True
            if db_map.pro_tier != pro_tier and pro_tier is not None:
                _logger.info('Updating pro tier for map %s', api_map.name)
                db_map.pro_tier = pro_tier
                changed = True
            if db_map.vnl_tier != vnl_tier and vnl_tier is not None:
                _logger.info('Updating VNL tier for map %s', api_map.name)
                db_map.vnl_tier = vnl_tier
                changed = True
            if (db_map.vnl_pro_tier != vnl_pro_tier and
                vnl_pro_tier is not None):
                _logger.info('Updating VNL pro tier for map %s', api_map.name)
                db_map.vnl_pro_tier = vnl_pro_tier
                changed = True
            if db_map.thumbnail != thumbnail and thumbnail is not None:
                _logger.info('Updating thumbnail for map %s', api_map.name)
                db_map.thumbnail = thumbnail
                changed = True
            if changed:
                await db_map.save()
                updated += 1
    _logger.info('Refreshed map database (%d new, %d updated, %d deleted)',
                 new, updated, deleted)

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

async def _top_record(mode: Mode, latest=True, steamid64: int | None=None,
                      api_map: APIMap | None=None,
                      tp_type: Type | None=None) -> _APIRecord | None:
    api_mode_id = {Mode.CKZ: 'classic', Mode.VNL2: 'vanilla'}[mode]
    url = f'https://api.cs2kz.org/records?mode={api_mode_id}&top=true'
    if steamid64 is not None:
        url += f'&player={steamid64}'
    if api_map is not None:
        course = api_map.course or 'Main'
        url += f'&map={api_map.name}&course={course}&'
    if tp_type == Type.TP:
        url += '&has_teleports=true'
    elif tp_type == Type.PRO:
        url += '&has_teleports=false'
    if latest:
        url += '&sort_by=submission-date&sort_order=descending'
    else:
        url += '&sort_by=time&sort_order=ascending'
    url += '&limit=1'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    raise APIError("Couldn't get global API PBs (HTTP %d)" %
                                   r.status)
                json = await r.text()
    except ClientError as e:
        raise APIConnectionError("Couldn't get global API PBs") from e

    try:
        records = _APIRecordResults.model_validate_json(json)
    except ValidationError as e:
        raise APIError('Malformed global API PBs') from e
    return records.values[0] if records.values else None

def _record_to_pb(record: _APIRecord, api_map: APIMap) -> PersonalBest:
    steamid64 = _steamid_to_steamid64(record.player.id)
    player_url = _profile_url(steamid64) 
    if record.teleports == 0:
        points = record.pro_points
        place = record.pro_rank
    else:
        points = record.nub_points
        place = record.nub_rank
    if not isinstance(points, float) or not isinstance(place, int):
        raise APIError('Malformed global API PB')
    submitted_at = datetime.fromtimestamp(record.id.time / 1000.0,
                                          tz=timezone.utc)
    return PersonalBest(id=record.id.int, steamid64=steamid64,
                        player_name=record.player.name, player_url=player_url,
                        map=api_map, time=record.time,
                        teleports=record.teleports, points=int(points),
                        point_scale=10000, place=place,
                        date=submitted_at)

class CS2API(API):
    @override
    def has_tp_wrs(self) -> bool:
        return False

    @override
    async def get_map(self, name: str, course: str | None=None,
                      bonus: int | None=None) -> APIMap:
        if bonus is not None:
            raise APIMapError("Bonuses aren't supported on CS2. "
                              'Did you mean to specify a course?')
        if not re.fullmatch('[A-za-z0-9_]+', name):
            raise APIMapError('Invalid map name')

        db_map = None
        thumbnail = None
        try:
            db_map = await Map.get(name__iexact=name, is_cs2=True)
        except DoesNotExist:
            db_maps = list(await Map.filter(name__icontains=name,
                                            is_cs2=True))
            if len(db_maps) > 1:
                raise APIMapAmbiguousError(db_maps)
            elif db_maps:
                db_map = db_maps[0]
        if db_map is not None:
            name = db_map.name
            thumbnail = db_map.thumbnail

        if (db_map is not None and
            db_map.main_course is not None and
            (course is None or course.lower() == db_map.main_course.lower())):
            course_id = 1
            course_name = db_map.main_course
            if self.mode == Mode.VNL2:
                tier = db_map.vnl_tier
                pro_tier = db_map.vnl_pro_tier
            else:
                tier = db_map.tier
                pro_tier = db_map.pro_tier
        else:
            json = {}
            url = f'https://api.cs2kz.org/maps/{name}'
            try:
                async with ClientSession() as session:
                    async with session.get(url) as r:
                        if r.status == 404:
                            raise APIMapNotFoundError('Map not found')
                        elif r.status != 200:
                            raise APIError("Couldn't get global API map "
                                           '(HTTP %d)' % r.status)
                        json = await r.text()
            except ClientError as e:
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
                course_id = None
                course_info = None
                course_name = None
                for course_id, course_info in enumerate(courses, start=1):
                    course_name = course_info.name
                    if course.lower() in course_name.lower():
                        break
                else:
                    raise APIMapError('Map course not found')

            course_filters = course_info.filters
            course_filter = {Mode.CKZ: course_filters.classic,
                             Mode.VNL2: course_filters.vanilla}[self.mode]
            tier = _tier_num(course_filter.nub_tier)
            pro_tier = _tier_num(course_filter.pro_tier)

        tier_name = _tier_name(tier)
        pro_tier_name = _tier_name(pro_tier)

        if thumbnail is None:
            thumbnail = await _thumbnail_for_map(name, course_id)

        url = f'https://cs2kz.org/maps/{name}'

        return APIMap(name=name, mode=self.mode, bonus=None,
                      course=course_name, tier=tier, tier_name=tier_name,
                      pro_tier=tier, pro_tier_name=pro_tier_name, max_tier=7,
                      thumbnail=thumbnail, url=url)

    @override
    async def get_pb(self, steamid64: int, api_map: APIMap,
                     tp_type: Type=Type.ANY) -> PersonalBest | None:
        record = await _top_record(self.mode, steamid64=steamid64,
                                   api_map=api_map, tp_type=tp_type)
        if record is None:
            return None
        return _record_to_pb(record, api_map)

    @override
    async def get_latest(self, steamid64: int, tp_type: Type=Type.ANY
                         ) -> PersonalBest | None:
        record = await _top_record(self.mode, steamid64=steamid64,
                                   tp_type=tp_type)
        if record is None:
            return None
        api_map = await self.get_map(record.map.name, record.course.name)
        return _record_to_pb(record, api_map)

    @override
    async def get_wrs(self, api_map: APIMap) -> list[PersonalBest]:
        record = await _top_record(self.mode, api_map=api_map, latest=False)
        if record is None:
            return []
        pbs = [_record_to_pb(record, api_map)]
        pro_rank = record.pro_rank
        if pro_rank is not None:
            return pbs
        pro_record = await _top_record(self.mode, api_map=api_map,
                                       tp_type=Type.PRO, latest=False)
        if pro_record is not None:
            pbs.append(_record_to_pb(pro_record, api_map))
        return pbs

    @override
    async def get_profile(self, steamid64: int) -> Profile:
        player_url = _profile_url(steamid64)
        url = f'https://api.cs2kz.org/players/{steamid64}'
        try:
            async with ClientSession() as session:
                async with session.get(url) as r:
                    if r.status == 200:
                        json = await r.text()
                    elif r.status == 404:
                        try:
                            player_name = await name_for_steamid64(steamid64)
                        except SteamError:
                            player_name = None
                        return Profile(name=player_name, url=player_url,
                                       mode=self.mode, rank=Rank.UNKNOWN,
                                       points=0, average=None)
                    else:
                        raise APIError("Couldn't get global API profile "
                                       '(HTTP %d)' % r.status)
        except ClientError as e:
            raise APIConnectionError("Couldn't get global API PBs") from e

        try:
            profile = _APIProfile.model_validate_json(json)
        except ValidationError as e:
            raise APIError('Malformed global API profile') from e

        rating = {Mode.CKZ: profile.ckz_rating,
                  Mode.VNL2: profile.vnl_rating}[self.mode]
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
        return Profile(name=profile.name, url=player_url,
                       mode=self.mode, rank=rank, points=int(points),
                       average=None)
