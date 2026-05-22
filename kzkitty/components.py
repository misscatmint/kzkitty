import logging
from datetime import timedelta

from hikari import Color, User
from hikari.impl import (ContainerComponentBuilder,
                         MediaGalleryComponentBuilder,
                         SectionComponentBuilder, ThumbnailComponentBuilder)

from kzkitty.api.kz import APIMap, PersonalBest, Profile, Rank
from kzkitty.api.steam import SteamError, get_steam
from kzkitty.models import Player

_logger = logging.getLogger('kzkitty.components')

async def _avatar_url(player: Player) -> str | None:
    steam = get_steam()
    try:
        steam_profile = await steam.profile_for_steamid64(player.steamid64)
    except SteamError:
        _logger.exception("Couldn't get player avatar")
        return None
    return steam_profile.avatar_url

async def _avatar_container(avatar_url: str | None, accent_color: Color,
                            body: str) -> ContainerComponentBuilder:
    container = ContainerComponentBuilder(accent_color=accent_color)
    if avatar_url is not None:
        thumbnail = ThumbnailComponentBuilder(media=avatar_url)
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
        s = f'{hh:d}:{mm:02d}:{ss:02d}'
    elif mm:
        s = f'{mm:d}:{ss:02d}'
    else:
        s = f'{ss:d}'
    if td.days:
        def plural(n: int) -> str:
            return 's' if abs(n) != 1 else ''
        s = f'{td.days:d} day{plural(td.days)}, {s}'
    if td.microseconds:
        s = f'{s}.{round(td.microseconds, -3):06d}'
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

def _map_info(api_map: APIMap, pro: bool | None=None) -> str:
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

    body = f'## {medal} ' if medal is not None else '## '
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

    accent_color = Color(0x1e90ff) if pb.teleports == 0 else Color(0xffa500)
    avatar_url = await _avatar_url(player)
    container = await _avatar_container(avatar_url, accent_color, body)
    gallery = MediaGalleryComponentBuilder()
    gallery.add_media_gallery_item(pb.map.thumbnail_url)
    container.add_component(gallery)
    container.add_text_display(f'-# <t:{int(pb.date.timestamp())}>')
    return container

async def profile_component(profile: Profile, player: Player, user: User
                            ) -> ContainerComponentBuilder:
    if profile.name is None:
        steam = get_steam()
        try:
            steam_profile = await steam.profile_for_steamid64(player.steamid64)
        except SteamError:
            _logger.exception("Couldn't get new player name/avatar")
            name = user.display_name
            avatar_url = None
        else:
            name = steam_profile.name
            avatar_url = steam_profile.avatar_url
    else:
        name = profile.name
        avatar_url = await _avatar_url(player)
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
    return await _avatar_container(avatar_url, accent_color, body)

def _wr_time(pb: PersonalBest) -> str:
    player_name = pb.player_name or pb.steamid64
    return f'{_formattime(pb.time)} by [{player_name}]({pb.player_url})'

async def map_component(api_map: APIMap, wrs: list[PersonalBest],
                        ) -> ContainerComponentBuilder:
    map_info = _map_info(api_map)
    body = f"""## [{api_map.name}]({api_map.url})

{map_info}
"""
    if api_map.has_tp_wrs:
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
    gallery = MediaGalleryComponentBuilder()
    gallery.add_media_gallery_item(api_map.thumbnail_url)
    container.add_component(gallery)
    return container
