from datetime import timedelta

from arc import GatewayContext
from hikari import Color
from hikari.impl import (ContainerComponentBuilder,
                         MediaGalleryComponentBuilder,
                         SectionComponentBuilder, ThumbnailComponentBuilder)

from kzkitty.api.kz import PersonalBest, Profile, Rank
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
        tier = pb.map.vnl_pro_tier if pb.teleports == 0 else pb.map.vnl_tier
    else:
        profile_url = f'https://kzgo.eu/players/{player.steamid64}?{pb.mode}'
        map_url = f'https://kzgo.eu/maps/{pb.map.name}?{pb.mode}'
        tier = pb.map.tier
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

**Mode:** {pb.mode.upper()}{' (PRO)' if pb.teleports == 0 else ''}
**Tier:** {tier or '(unknown)'}
**Time:** {_formattime(pb.time)}{f' (#{pb.place})' if top_100 else ''}
"""
    if pb.teleports:
        body += f"""**Teleports:** {pb.teleports}
"""
    body += f"""**Points:** {pb.points}
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
                       f'{profile.mode}')
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

**Mode:** {profile.mode.upper()}
**Rank:** {profile.rank}
**Points:** {profile.points:,}
**Average:** {profile.average}
"""
    return await _player_container(player, accent_color, body)
