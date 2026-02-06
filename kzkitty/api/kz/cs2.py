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

logger = logging.getLogger('kzkitty.api.kz.cs2')

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
                    logger.error("Couldn't get map thumbnail (HTTP %d)",
                                 r.status)
    except ClientError:
        logger.exception("Couldn't get map thumbnail")
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
    logger.info('Downloading CS2 map tiers')
    url = 'https://api.cs2kz.org/maps'
    try:
        async with ClientSession() as session:
            async with session.get(url) as r:
                if r.status != 200:
                    logger.error("Couldn't get global API maps (HTTP %d)",
                                 r.status)
                    return
                results = await r.json()
    except ClientError:
        logger.exception("Couldn't get global API maps")
        return

    if not isinstance(results, dict):
        logger.error('Malformed global API maps response (not a dict)')
        return
    maps = results.get('values')
    if not isinstance(maps, list):
        logger.error('Malformed global API maps response (maps not a list)')
        return

    new = 0
    updated = 0
    deleted = 0
    for map_info in maps:
        map_id = map_info.get('id')
        if not isinstance(map_id, int):
            logger.error('Malformed global API maps response (id not an int)')
            continue
        state = map_info.get('state')
        if not isinstance(state, str):
            logger.error('Malformed global API maps response'
                         ' (state not a str)')
            continue

        if state != 'approved':
            try:
                db_map = await Map.get(map_id=map_id, is_cs2=True)
            except DoesNotExist:
                pass
            else:
                await db_map.delete()
                deleted += 1
            continue

        name = map_info.get('name')
        if not isinstance(name, str):
            logger.error('Malformed global API maps response (name not a str)')
            continue
        courses = map_info.get('courses')
        if not isinstance(courses, list):
            logger.error('Malformed global API maps response'
                         ' (courses not a list)')
            continue
        tier = pro_tier = vnl_tier = vnl_pro_tier = course_name = None
        if courses:
            course_info = courses[0]
            if not isinstance(course_info, dict):
                logger.error('Malformed global API maps response'
                             ' (course info not a dict)')
                continue
            course_name = course_info.get('name')
            if not isinstance(course_name, str):
                raise APIError('Malformed global API map response'
                               ' (course name not a str)')
            filters = course_info.get('filters')
            if not isinstance(filters, dict):
                logger.error('Malformed global API maps response'
                             ' (filters not a dict)')
                continue
            classic_filter = filters.get('classic')
            if (not isinstance(classic_filter, dict) and
                classic_filter is not None):
                logger.error('Malformed global API maps response'
                             ' (filters not a dict)')
                continue
            if classic_filter is not None:
                tier_code = classic_filter.get('nub_tier')
                pro_tier_code = classic_filter.get('pro_tier')
                if (not isinstance(tier_code, str) or
                    not isinstance(pro_tier_code, str)):
                    logger.error('Malformed global API maps response'
                                 ' (classic tier not a str)')
                    continue
                tier = _tier_num(tier_code) or 10
                pro_tier = _tier_num(pro_tier_code) or 10
            vanilla_filter = filters.get('vanilla')
            if (not isinstance(vanilla_filter, dict) and
                vanilla_filter is not None):
                logger.error('Malformed global API maps response'
                             ' (filters not a dict)')
                continue
            if vanilla_filter is not None:
                vnl_tier_code = vanilla_filter.get('nub_tier')
                vnl_pro_tier_code = vanilla_filter.get('pro_tier')
                if (not isinstance(vnl_tier_code, str) or
                    not isinstance(vnl_pro_tier_code, str)):
                    logger.error('Malformed global API maps response'
                                 ' (vanilla tier not a str)')
                    continue
                vnl_tier = _tier_num(vnl_tier_code) or 10
                vnl_pro_tier = _tier_num(vnl_pro_tier_code) or 10

        try:
            db_map = await Map.get(map_id=map_id, is_cs2=True)
        except DoesNotExist:
            logger.info('Downloading thumbnail for map %s', name)
            thumbnail = await _thumbnail_for_map(name)
            await Map(map_id=map_id, is_cs2=True, name=name, tier=tier,
                      pro_tier=pro_tier, vnl_tier=vnl_tier,
                      vnl_pro_tier=vnl_pro_tier, main_course=course_name,
                      thumbnail=thumbnail).save()
            new += 1
        else:
            thumbnail = db_map.thumbnail
            if thumbnail is None:
                thumbnail = await _thumbnail_for_map(name)
            changed = False
            if db_map.main_course != course_name and course_name is not None:
                logger.info('Updating main_course for map %s', name)
                db_map.main_course = course_name
                changed = True
            if db_map.tier != tier and tier is not None:
                logger.info('Updating tier for map %s', name)
                db_map.tier = tier
                changed = True
            if db_map.pro_tier != pro_tier and pro_tier is not None:
                logger.info('Updating pro tier for map %s', name)
                db_map.pro_tier = pro_tier
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
                      tp_type: Type | None=None) -> dict | None:
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
                results = await r.json()
    except ClientError as e:
        raise APIConnectionError("Couldn't get global API PBs") from e
    if not isinstance(results, dict):
        raise APIError('Malformed global API PBs (results not a dict)')
    records = results.get('values')
    if not isinstance(records, list):
        raise APIError('Malformed global API PBs (not a list)')
    if records and not isinstance(records[0], dict):
        raise APIError('Malformed global API PBs (not a list of dicts)')
    return records[0] if records else None

def _record_to_pb(record: dict, api_map: APIMap) -> PersonalBest:
    player = record.get('player')
    if not isinstance(player, dict):
        raise APIError('Malformed global API PB (player not a dict)')
    steamid = player.get('id')
    player_name = player.get('name')
    record_id = record.get('id')
    teleports = record.get('teleports')
    time = record.get('time')
    submitted_at = record.get('submitted_at')
    if (not isinstance(steamid, str) or
        not isinstance(player_name, str) or
        not isinstance(record_id, int) or
        not isinstance(teleports, int) or
        not isinstance(time, float) or
        not isinstance(submitted_at, str)):
        raise APIError('Malformed global API PB')
    try:
        date = datetime.fromisoformat(submitted_at)
    except ValueError as e:
        raise APIError('Malformed global API PB (bad date)') from e
    date = date.replace(tzinfo=timezone.utc)
    steamid64 = _steamid_to_steamid64(steamid)
    player_url = _profile_url(steamid64) 
    if teleports == 0:
        points = record.get('pro_points')
        place = record.get('pro_rank')
    else:
        points = record.get('nub_points')
        place = record.get('nub_rank')
    if not isinstance(points, float) or not isinstance(place, int):
        raise APIError('Malformed global API PB')
    return PersonalBest(id=record_id, steamid64=steamid64,
                        player_name=player_name, player_url=player_url,
                        map=api_map, time=timedelta(seconds=time),
                        teleports=teleports, points=int(points),
                        point_scale=10000, place=place, date=date)

class CS2API(API):
    def has_tp_wrs(self) -> bool:
        return False

    async def get_map(self, name: str, course: str | None=None,
                      bonus: int | None=None) -> APIMap:
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

        if (db_map is not None and
            (course is None or course.lower() == db_map.main_course.lower())):
            if bonus is not None:
                raise APIMapError("Bonuses aren't supported on CS2. "
                                  'Did you mean to specify a course?')

            course_id = 1
            course_name = db_map.main_course or 'Main'
            if self.mode == Mode.VNL2:
                tier = db_map.vnl_tier
                pro_tier = db_map.vnl_pro_tier
            else:
                tier = db_map.tier
                pro_tier = db_map.pro_tier
            thumbnail = db_map.thumbnail
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
                        json = await r.json()
            except ClientError as e:
                raise APIConnectionError("Couldn't get global API map") from e

            if bonus is not None:
                raise APIMapError("Bonuses aren't supported on CS2. "
                                  'Did you mean to specify a course?')

            if not isinstance(json, dict):
                raise APIError('Malformed global API map response '
                               '(not a dict)')

            courses = json.get('courses')
            if not isinstance(courses, list):
                raise APIError('Malformed global API map response '
                               '(courses not a list)')
            if courses and course is None:
                course_id = 1
                course_info = courses[0]
                if not isinstance(course_info, dict):
                    raise APIError('Malformed global API map response '
                                   '(course not a dict)')
                course_name = course_info.get('name')
                if not isinstance(course_name, str):
                    raise APIError('Malformed global API map response '
                                   '(course name not a str)')
            else:
                course = course or 'Main'
                course_id = None
                course_info = None
                course_name = None
                for course_id, course_info in enumerate(courses, start=1):
                    if not isinstance(course_info, dict):
                        raise APIError('Malformed global API map response '
                                       '(course not a dict)')
                    course_name = course_info.get('name')
                    if not isinstance(course_name, str):
                        raise APIError('Malformed global API map response '
                                       '(course name not a str)')
                    if course.lower() in course_name.lower():
                        break
                else:
                    raise APIMapError('Map course not found')

            course_filters = course_info.get('filters')
            if not isinstance(course_filters, dict):
                raise APIError('Malformed global API map response '
                               '(course filters not a dict)')
            if self.mode == Mode.VNL2:
                course_filter = course_filters.get('vanilla') 
            else:
                course_filter = course_filters.get('classic')
            if course_filter is None:
                tier = pro_tier = None
                tier_name = pro_tier_name = 'Unknown'
            else:
                tier_code = course_filter.get('nub_tier')
                pro_tier_code = course_filter.get('pro_tier')
                if (not isinstance(tier_code, str) or
                    not isinstance(pro_tier_code, str)):
                    raise APIError('Malformed global API map response '
                                   '(tier not a astr)')
                tier = _tier_num(tier_code)
                pro_tier = _tier_num(pro_tier_code)

        tier_name = _tier_name(tier)
        pro_tier_name = _tier_name(pro_tier)

        if thumbnail is None:
            thumbnail = await _thumbnail_for_map(name, course_id)

        url = f'https://cs2kz.org/maps/{name}'

        return APIMap(name=name, mode=self.mode, bonus=None,
                      course=course_name, tier=tier, tier_name=tier_name,
                      pro_tier=tier, pro_tier_name=pro_tier_name, max_tier=7,
                      thumbnail=thumbnail, url=url)

    async def get_pb(self, steamid64: int, api_map: APIMap,
                     tp_type: Type=Type.ANY) -> PersonalBest | None:
        record = await _top_record(self.mode, steamid64=steamid64,
                                   api_map=api_map, tp_type=tp_type)
        if record is None:
            return None
        return _record_to_pb(record, api_map)

    async def get_latest(self, steamid64: int, tp_type: Type=Type.ANY
                         ) -> PersonalBest | None:
        record = await _top_record(self.mode, steamid64=steamid64,
                                   tp_type=tp_type)
        if record is None:
            return None
        map_info = record.get('map')
        if not isinstance(map_info, dict):
            raise APIError('Malformed global API PBs (map not a dict)')
        map_name = map_info.get('name')
        if not isinstance(map_name, str):
            raise APIError('Malformed global API PBs (map name not a str)')
        course_info = record.get('course')
        if not isinstance(course_info, dict):
            raise APIError('Malformed global API PBs (course not a dict)')
        course = course_info.get('name')
        if not isinstance(course, str):
            raise APIError('Malformed global API PBs (course name not a str)')
        api_map = await self.get_map(map_name, course)
        return _record_to_pb(record, api_map)

    async def get_wrs(self, api_map: APIMap) -> list[PersonalBest]:
        record = await _top_record(self.mode, api_map=api_map, latest=False)
        if record is None:
            return []
        pbs = [_record_to_pb(record, api_map)]
        pro_rank = record.get('pro_rank')
        if pro_rank is not None:
            return pbs
        pro_record = await _top_record(self.mode, api_map=api_map,
                                       tp_type=Type.PRO, latest=False)
        if pro_record is not None:
            pbs.append(_record_to_pb(pro_record, api_map))
        return pbs

    async def get_profile(self, steamid64: int) -> Profile:
        player_url = _profile_url(steamid64)
        api_mode_id = {Mode.CKZ: 'classic', Mode.VNL2: 'vanilla'}[self.mode]
        url = (f'https://api.cs2kz.org/players/{steamid64}/profile?'
               f'mode={api_mode_id}')
        try:
            async with ClientSession() as session:
                async with session.get(url) as r:
                    if r.status == 200:
                        profile = await r.json()
                    elif r.status == 404:
                        try:
                            player_name = await name_for_steamid64(steamid64)
                        except SteamError:
                            player_name = None
                        return Profile(name=player_name, url=player_url,
                                       mode=self.mode, rank=Rank.UNKNOWN,
                                       points=0, average=None)
                    else:
                        raise APIError("Couldn't get global API PBs "
                                       '(HTTP %d)' % r.status)
        except ClientError as e:
            raise APIConnectionError("Couldn't get global API PBs") from e
        if not isinstance(profile, dict):
            raise APIError('Malformed global API profile (not a dict)')
        rating = profile.get('rating')
        if not isinstance(rating, float):
            raise APIError('Malformed global API ranks (rating not a float)')

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
        return Profile(name=profile.get('name'), url=player_url,
                       mode=self.mode, rank=rank, points=int(points),
                       average=None)
