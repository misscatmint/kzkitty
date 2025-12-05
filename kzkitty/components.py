from datetime import timedelta

from hikari import Color, User
from hikari.impl import (ContainerComponentBuilder,
                         MediaGalleryComponentBuilder,
                         SectionComponentBuilder, ThumbnailComponentBuilder)

from kzkitty.api.kz import APIMap, PersonalBest, Profile, Rank
from kzkitty.api.steam import SteamError, avatar_for_steamid64
from kzkitty.models import Mode, Player

async def _player_container(player: Player, accent_color: Color, body: str):
    container = ContainerComponentBuilder(accent_color=accent_color)
    try:
        avatar = await avatar_for_steamid64(player.steamid64)
    except SteamError:
        avatar = None
    if avatar is not None:
        thumbnail = ThumbnailComponentBuilder(media=avatar)
        section = SectionComponentBuilder(accessory=thumbnail)
        section.add_text_display(body)
        container.add_component(section)
    else:
        container.add_text_display(body)
    return container

def _map_url(api_map: APIMap, mode: Mode, stage: int) -> str:
    if stage != 0:
        return (f'https://kzgo.eu/maps/{api_map.name}?{mode.lower()}'
                f'&bonus={stage}')
    elif mode == Mode.VNL:
        return f'https://vnl.kz/#/map/{api_map.name}'
    else:
        return f'https://kzgo.eu/maps/{api_map.name}?{mode.lower()}'

def _profile_url(steamid64: int, mode: Mode) -> str:
    if mode == Mode.VNL:
        return f'https://vnl.kz/#/stats/{steamid64}'
    else:
        return f'https://kzgo.eu/players/{steamid64}?{mode.lower()}'

def _tier_name(tier: int, mode: Mode) -> str:
    if mode == Mode.VNL:
        names = {1: 'Very Easy', 2: 'Easy', 3: 'Medium', 4: 'Advanced',
                 5: 'Hard', 6: 'Very Hard', 7: 'Extreme', 8: 'Death',
                 9: 'Unfeasible', 10: 'Impossible'}
    else:
        names = {1: 'Very Easy', 2: 'Easy', 3: 'Medium', 4: 'Hard',
                 5: 'Very Hard', 6: 'Extreme', 7: 'Death'}
    return names.get(tier, 'Unknown')

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

async def pb_component(pb: PersonalBest, player: Player, user: User
                       ) -> ContainerComponentBuilder:
    player_name = pb.player_name or user.display_name
    profile_url = _profile_url(player.steamid64, pb.mode)
    map_url = _map_url(pb.map, pb.mode, pb.stage)

    if pb.stage != 0:
        extra = f'**Bonus**: {pb.stage}'
    elif pb.mode == Mode.VNL:
        tier_num = pb.map.vnl_pro_tier if pb.teleports == 0 else pb.map.vnl_tier
        if tier_num is not None:
            extra = f'**Tier**: {tier_num} - {_tier_name(tier_num, pb.mode)}'
        else:
            extra = '**Tier**: (unknown)'
    else:
        extra = f'**Tier**: {pb.map.tier} - {_tier_name(pb.map.tier, pb.mode)}'

    if pb.place is not None:
        medal = {1: ':first_place:', 2: ':second_place:',
                 3: ':third_place:'}.get(pb.place)
        top_100 = pb.place <= 100
    else:
        medal = None
        top_100 = False

    if pb.points == 1000:
        points = f'{pb.points} :trophy:'
    elif pb.points >= 900:
        points = f'{pb.points} :fire:'
        if medal is None:
            medal = ':fire:'
    elif pb.points >= 800:
        points = f'{pb.points} :sparkles:'
        if medal is None:
            medal = ':sparkles:'
    else:
        points = f'{pb.points}'

    if medal is not None:
        body = f'## {medal} '
    else:
        body = '## '
    body += f"""[{player_name}]({profile_url}) on [{pb.map.name}]({map_url})

**Mode**: {pb.mode}{' (PRO)' if pb.teleports == 0 else ''}
{extra}
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
    player_name = profile.player_name or user.display_name
    profile_url = _profile_url(player.steamid64, profile.mode)
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
    body = f"""## [{player_name}]({profile_url})

**Mode**: {profile.mode}
**Rank**: {profile.rank}
**Points**: {profile.points:,}
**Average**: {profile.average}
"""
    return await _player_container(player, accent_color, body)

async def map_component(api_map: APIMap, mode: Mode, stage: int,
                        wrs: list[PersonalBest]) -> ContainerComponentBuilder:
    map_url = _map_url(api_map, mode, stage)
    if stage != 0:
        extra = f'**Bonus**: {stage}'
        color = 0xcccccc
    elif mode == Mode.VNL:
        if api_map.vnl_tier is not None and api_map.vnl_pro_tier is not None:
            tier_name = _tier_name(api_map.vnl_tier, mode)
            if api_map.vnl_tier != api_map.vnl_pro_tier:
                pro_tier_name = _tier_name(api_map.vnl_pro_tier, mode)
                extra = f"""**Tier** (TP): {api_map.vnl_tier} - {tier_name}
**Tier** (PRO): {api_map.vnl_pro_tier} - {pro_tier_name}"""
            else:
                extra = f'**Tier**: {api_map.vnl_tier} - {tier_name}'
        else:
            extra = '**Tier**: (unknown)'
        color = {1: 0x049c49, 2: 0x007053, 3: 0xb6b007, 4: 0xf39c12,
                 5: 0xfd7e14, 6: 0xe74c3c, 7: 0xc52412, 8: 0xd22ce5,
                 9: 0x555555, 10: 0x000000}.get(api_map.vnl_tier or 0,
                                                0xcccccc)
    else:
        tier_name = _tier_name(api_map.tier, mode)
        extra = f'**Tier**: {api_map.tier} - {tier_name}'
        color = {1: 0x049c49, 2: 0x007053, 3: 0xf39c12, 4: 0xfd7e14,
                 5: 0xe74c3c, 6: 0xc52412, 7: 0xd22ce5}.get(api_map.tier,
                                                            0xcccccc)

    tp_pb = pro_pb = None
    tp_pb_time = pro_pb_time = '(none)'
    for pb in wrs:
        if pb.teleports == 0:
            pro_pb = pb
        else:
            tp_pb = pb
    if tp_pb is not None:
        tp_player_name = tp_pb.player_name or '(unknown)'
        tp_profile_url = _profile_url(tp_pb.steamid64, mode)
        tp_pb_time = (f'{_formattime(tp_pb.time)} '
                      f'by [{tp_player_name}]({tp_profile_url})')
    if pro_pb is not None:
        pro_player_name = pro_pb.player_name or '(unknown)'
        pro_profile_url = _profile_url(pro_pb.steamid64, mode)
        pro_pb_time = (f'{_formattime(pro_pb.time)} '
                       f'by [{pro_player_name}]({pro_profile_url})')

    body = f"""## [{api_map.name}]({map_url})

**Mode**: {mode}
{extra}
**WR** (TP): {tp_pb_time}
**WR** (PRO): {pro_pb_time}
"""
    accent_color = Color(color)
    container = ContainerComponentBuilder(accent_color=accent_color)
    container.add_text_display(body)
    if api_map.thumbnail is not None:
        gallery = MediaGalleryComponentBuilder()
        gallery.add_media_gallery_item(api_map.thumbnail)
        container.add_component(gallery)
    return container
