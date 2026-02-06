import logging
from datetime import timedelta

from hikari import Color, User
from hikari.impl import (ContainerComponentBuilder,
                         MediaGalleryComponentBuilder,
                         SectionComponentBuilder, ThumbnailComponentBuilder)

from kzkitty.api.kz import APIMap, PersonalBest, Profile, Rank
from kzkitty.api.steam import SteamError, avatar_for_steamid64
from kzkitty.models import Player

logger = logging.getLogger('kzkitty.components')

async def _player_container(player: Player, accent_color: Color, body: str):
    container = ContainerComponentBuilder(accent_color=accent_color)
    try:
        avatar = await avatar_for_steamid64(player.steamid64)
    except SteamError:
        logger.exception("Couldn't get player avatar")
        avatar = None
    if avatar is not None:
        thumbnail = ThumbnailComponentBuilder(media=avatar)
        section = SectionComponentBuilder(accessory=thumbnail)
        section.add_text_display(body)
        container.add_component(section)
    else:
        container.add_text_display(body)
    return container

def _formattime(td: timedelta) -> str:
    mm, ss = divmod(td.seconds, 60)
    hh, mm = divmod(mm, 60)
    if hh:
        s = '%d:%02d:%02d' % (hh, mm, ss)
    elif mm:
        s = '%d:%02d' % (mm, ss)
    else:
        s = '%d' % ss
    if td.days:
        def plural(n: int) -> tuple[int, str]:
            return n, abs(n) != 1 and 's' or ''
        s = ('%d day%s, ' % plural(td.days)) + s
    if td.microseconds:
        s = s + '.%06d' % td.microseconds
        s = s.rstrip('0').rstrip('.')
    return s

def _map_color(api_map: APIMap) -> int:
    if api_map.bonus is not None:
        return 0xcccccc
    elif api_map.max_tier == 7:
        return {1: 0x049c49, 2: 0x007053, 3: 0xf39c12, 4: 0xfd7e14,
                5: 0xe74c3c, 6: 0xc52412, 7: 0xd22ce5}.get(api_map.tier or 0,
                                                           0xcccccc)
    else:
        return {1: 0x049c49, 2: 0x007053, 3: 0xb6b007, 4: 0xf39c12,
                5: 0xfd7e14, 6: 0xe74c3c, 7: 0xc52412, 8: 0xd22ce5,
                9: 0x555555, 10: 0x000000}.get(api_map.tier or 0, 0xcccccc)

def _map_info(api_map: APIMap, pro=None) -> str:
    if api_map.bonus is not None:
        extra = f"""**Bonus**: {api_map.bonus}
"""
    elif api_map.course is not None:
        extra = f"""**Course**: {api_map.course}
"""
    else:
        extra = ''
    extra += f"**Mode**: {api_map.mode}{' (PRO)' if pro else ''}"
    if (pro is None and api_map.tier is not None and
        api_map.pro_tier is not None and api_map.tier != api_map.pro_tier):
        extra += f"""
**Tier** (TP): {api_map.tier} - {api_map.tier_name}
**Tier** (PRO): {api_map.pro_tier} - {api_map.pro_tier_name}"""
    else:
        tier = api_map.pro_tier if pro else api_map.tier
        if tier is not None:
            extra += f"""
**Tier**: {api_map.tier} - {api_map.tier_name}"""
        elif api_map.bonus is None:
            extra += """
**Tier**: (unknown)"""
    return extra

async def pb_component(pb: PersonalBest, player: Player, user: User
                       ) -> ContainerComponentBuilder:
    player_name = pb.player_name or user.display_name
    map_info = _map_info(pb.map, pb.teleports == 0)

    if pb.place is not None:
        medal = {1: ':first_place:', 2: ':second_place:',
                 3: ':third_place:'}.get(pb.place)
        top_100 = pb.place <= 100
    else:
        medal = None
        top_100 = False

    if pb.points == pb.point_scale:
        points = f'{pb.points:,} :trophy:'
    elif pb.points >= pb.point_scale - (pb.point_scale / 10):
        points = f'{pb.points:,} :fire:'
        if medal is None:
            medal = ':fire:'
    elif pb.points >= pb.point_scale - (pb.point_scale / 10 * 2):
        points = f'{pb.points:,} :sparkles:'
        if medal is None:
            medal = ':sparkles:'
    else:
        points = f'{pb.points:,}'

    if medal is not None:
        body = f'## {medal} '
    else:
        body = '## '
    body += (f'[{player_name}]({pb.player_url}) on '
             f'[{pb.map.name}]({pb.map.url})')
    body += f"""
{map_info}
**Time**: {_formattime(pb.time)}{f' (#{pb.place})' if top_100 else ''}
"""
    if pb.teleports:
        body += f"""**Teleports**: {pb.teleports}
"""
    body += f"""**Points**: {points}
"""

    if pb.teleports == 0:
        accent_color = Color(0x1e90ff)
    else:
        accent_color = Color(0xffa500)
    container = await _player_container(player, accent_color, body)
    if pb.map.thumbnail is not None:
        gallery = MediaGalleryComponentBuilder()
        gallery.add_media_gallery_item(pb.map.thumbnail)
        container.add_component(gallery)
    container.add_text_display(f'-# <t:{int(pb.date.timestamp())}>')
    return container

async def profile_component(profile: Profile, player: Player, user: User
                            ) -> ContainerComponentBuilder:
    name = profile.name or user.display_name
    colors = {Rank.BEGINNER_MINUS: 0xffffff,
              Rank.BEGINNER: 0xffffff,
              Rank.BEGINNER_PLUS: 0xffffff,
              Rank.AMATEUR_MINUS: 0x99ccff,
              Rank.AMATEUR: 0x99ccff,
              Rank.AMATEUR_PLUS: 0x99ccff,
              Rank.CASUAL_MINUS: 0x99ff99,
              Rank.CASUAL: 0x99ff99,
              Rank.CASUAL_PLUS: 0x99ff99,
              Rank.REGULAR_MINUS: 0x3eff3e,
              Rank.REGULAR: 0x3eff3e,
              Rank.REGULAR_PLUS: 0x3eff3e,
              Rank.SKILLED_MINUS: 0x800080,
              Rank.SKILLED: 0x800080,
              Rank.SKILLED_PLUS: 0x800080,
              Rank.EXPERT_MINUS: 0xda70d6,
              Rank.EXPERT: 0xda70d6,
              Rank.EXPERT_PLUS: 0xda70d6,
              Rank.SEMIPRO: 0xe84a49,
              Rank.PRO: 0xe84a49,
              Rank.MASTER: 0xff4040,
              Rank.LEGEND: 0xffd700}
    accent_color = Color(colors.get(profile.rank, 0xcccccc))
    body = f"""## [{name}]({profile.url})

**Mode**: {profile.mode}
**Rank**: {profile.rank}
**Points**: {profile.points:,}
"""
    if profile.average is not None:
        body += f"""**Average**: {profile.average}
"""
    return await _player_container(player, accent_color, body)

def _wr_time(pb: PersonalBest) -> str:
    player_name = pb.player_name or '(unknown)'
    return f'{_formattime(pb.time)} by [{player_name}]({pb.player_url})'

async def map_component(api_map: APIMap, wrs: list[PersonalBest],
                        has_tp_wrs: bool) -> ContainerComponentBuilder:
    map_info = _map_info(api_map)
    body = f"""## [{api_map.name}]({api_map.url})

{map_info}
"""
    if has_tp_wrs:
        tp_pb = pro_pb = None
        tp_pb_time = pro_pb_time = '(none)'
        for pb in wrs:
            if pb.teleports == 0:
                pro_pb = pb
            else:
                tp_pb = pb
        if tp_pb is not None:
            tp_pb_time = _wr_time(tp_pb)
        if pro_pb is not None:
            pro_pb_time = _wr_time(pro_pb)
        body += f"""**WR** (TP): {tp_pb_time}
**WR** (PRO): {pro_pb_time}
"""
    elif wrs:
        wrs.sort(key=lambda pb: pb.time)
        pb = wrs[0]
        overall_type = 'PRO' if pb.teleports == 0 else 'TP'
        body += f"""**WR** ({overall_type}): {_wr_time(pb)}
"""
        pro_pbs = [pb for pb in wrs if pb.teleports == 0]
        if pro_pbs and pb not in pro_pbs:
            pro_pb = pro_pbs[0]
            body += f"""**WR** (PRO): {_wr_time(pro_pb)}
"""
    else:
        body += """**WR**: (none)
"""

    accent_color = Color(_map_color(api_map))
    container = ContainerComponentBuilder(accent_color=accent_color)
    container.add_text_display(body)
    if api_map.thumbnail is not None:
        gallery = MediaGalleryComponentBuilder()
        gallery.add_media_gallery_item(api_map.thumbnail)
        container.add_component(gallery)
    return container
