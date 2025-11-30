from datetime import timedelta

from arc import GatewayContext
from hikari import Color
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

async def pb_component(ctx: GatewayContext, player: Player, pb: PersonalBest
                       ) -> ContainerComponentBuilder:
    player_name = pb.player_name or ctx.user.display_name
    if pb.mode == Mode.VNL:
        profile_url = f'https://vnl.kz/#/stats/{player.steamid64}'
        map_url = f'https://vnl.kz/#/map/{pb.map.name}'
        tier_num = pb.map.vnl_pro_tier if pb.teleports == 0 else pb.map.vnl_tier
        if tier_num is not None:
            tier = f'{tier_num} - {_tier_name(tier_num, pb.mode)}'
        else:
            tier = '(unknown)'
    else:
        profile_url = (f'https://kzgo.eu/players/{player.steamid64}?'
                       f'{pb.mode.lower()}')
        map_url = f'https://kzgo.eu/maps/{pb.map.name}?{pb.mode.lower()}'
        tier = f'{pb.map.tier} - {_tier_name(pb.map.tier, pb.mode)}'
    if pb.place is not None:
        medal = {1: ':first_place:', 2: ':second_place:',
                 3: ':third_place:'}.get(pb.place)
        top_100 = pb.place <= 100
    else:
        medal = None
        top_100 = False
    if medal is not None:
        body = f'## {medal} '
    elif pb.points >= 900:
        body = '## :fire: '
    elif pb.points >= 800:
        body = '## :sparkles: '
    else:
        body = '## '
    body += f"""[{player_name}]({profile_url}) on [{pb.map.name}]({map_url})

**Mode**: {pb.mode}{' (PRO)' if pb.teleports == 0 else ''}
**Tier**: {tier}
**Time**: {_formattime(pb.time)}{f' (#{pb.place})' if top_100 else ''}
"""
    if pb.teleports:
        body += f"""**Teleports**: {pb.teleports}
"""
    body += f"""**Points**: {pb.points}
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

async def profile_component(ctx: GatewayContext, player: Player,
                            profile: Profile) -> ContainerComponentBuilder:
    player_name = profile.player_name or ctx.user.display_name
    if profile.mode == Mode.VNL:
        profile_url = f'https://vnl.kz/#/stats/{player.steamid64}'
    else:
        profile_url = (f'https://kzgo.eu/players/{player.steamid64}?'
                       f'{profile.mode.lower()}')
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

async def map_component(ctx: GatewayContext, api_map: APIMap, mode: Mode,
                        wrs: list[PersonalBest]) -> ContainerComponentBuilder:
    if mode == Mode.VNL:
        map_url = f'https://vnl.kz/#/map/{api_map.name}'
        if api_map.vnl_tier is not None and api_map.vnl_pro_tier is not None:
            tier_name = _tier_name(api_map.vnl_tier, mode)
            if api_map.vnl_tier != api_map.vnl_pro_tier:
                pro_tier_name = _tier_name(api_map.vnl_pro_tier, mode)
                tier = f"""**Tier** (TP): {api_map.vnl_tier} - {tier_name}
    **Tier** (PRO): {api_map.vnl_pro_tier} - {pro_tier_name}"""
            else:
                tier = f'**Tier**: {api_map.vnl_tier} - {tier_name}'
        else:
            tier = '**Tier**: (unknown)'
        color = {1: 0x049c49, 2: 0x007053, 3: 0xb6b007, 4: 0xf39c12,
                 5: 0xfd7e14, 6: 0xe74c3c, 7: 0xc52412, 8: 0xd22ce5,
                 9: 0x555555, 10: 0x000000}.get(api_map.vnl_tier or 0,
                                                0xcccccc)
    else:
        map_url = f'https://kzgo.eu/maps/{api_map.name}?{mode.lower()}'
        tier_name = _tier_name(api_map.tier, mode)
        tier = f'**Tier**: {api_map.tier} - {tier_name}'
        color = {1: 0x049c49, 2: 0x007053, 3: 0xf39c12, 4: 0xfd7e14,
                 5: 0xe74c3c, 6: 0xc52412, 7: 0xd22ce5}.get(api_map.tier,
                                                            0xcccccc)

    tp_pb = pro_pb = None
    for pb in wrs:
        if pb.teleports == 0:
            pro_pb = pb
        else:
            tp_pb = pb

    body = f"""## [{api_map.name}]({map_url})

**Mode**: {mode}
{tier}
**WR** (TP): {_formattime(tp_pb.time) if tp_pb is not None else '(none)'}
**WR** (PRO): {_formattime(pro_pb.time) if pro_pb is not None else '(none)'}
"""
    accent_color = Color(color)
    container = ContainerComponentBuilder(accent_color=accent_color)
    container.add_text_display(body)
    if api_map.thumbnail is not None:
        gallery = MediaGalleryComponentBuilder()
        gallery.add_media_gallery_item(api_map.thumbnail)
        container.add_component(gallery)
    return container
