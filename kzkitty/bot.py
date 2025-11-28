import os
from datetime import timedelta
from typing import Any

from arc import (GatewayClient, GatewayContext, MemberParams, Option,
                 StrParams, slash_command)
from hikari import Color, Member, MessageFlag
from hikari.impl import (ContainerComponentBuilder,
                         MediaGalleryComponentBuilder,
                         SectionComponentBuilder, ThumbnailComponentBuilder)
from tortoise.exceptions import DoesNotExist

from kzkitty.api import (APIError, APIMapError, APIMapAmbiguousError,
                         PersonalBest, SteamError, SteamValueError,
                         avatar_for_steamid64, latest_pb_for_steamid64,
                         map_for_name, pbs_for_steamid64,
                         steamid64_for_profile)
from kzkitty.gateway import GatewayBot
from kzkitty.models import Mode, Player

bot = GatewayBot(os.environ['DISCORD_TOKEN'])
client = GatewayClient(bot)

ModeParams = StrParams('Game mode', name='mode',
                       choices=['kzt', 'skz', 'vnl'])
PlayerParams = MemberParams('Player', name='player')
TeleportParams = StrParams('Pro or teleport run', name='teleports',
                           choices=['pro', 'tp'])

@client.include
@slash_command('register', 'Register account')
async def slash_register(ctx: GatewayContext,
                         profile: Option[str, StrParams('Steam profile URL')],
                         mode_name: Option[str | None, ModeParams]=None
                         ) -> None:
    try:
        steamid64 = await steamid64_for_profile(profile)
    except SteamValueError:
        await ctx.respond('Invalid Steam profile URL!',
                          flags=MessageFlag.EPHEMERAL)
    except SteamError:
        await ctx.respond("Couldn't access Steam API!",
                          flags=MessageFlag.EPHEMERAL)
    else:
        defaults: dict[str, Any] = {'steamid64': steamid64}
        if mode_name is not None:
            defaults['mode'] = mode_name
        await Player.update_or_create(id=ctx.user.id, defaults=defaults)
        await ctx.respond(f'Registered!', flags=MessageFlag.EPHEMERAL)

@client.include
@slash_command('mode', 'Show or set default game mode')
async def slash_mode(ctx: GatewayContext,
                     mode_name: Option[str | None, ModeParams]=None) -> None:
    if mode_name is None:
        try:
            player = await Player.get(id=ctx.user.id)
        except DoesNotExist:
            await ctx.respond('No mode set!', flags=MessageFlag.EPHEMERAL)
        else:
            await ctx.respond(f'Mode set to {player.mode}.',
                              flags=MessageFlag.EPHEMERAL)
        return

    defaults = {'mode': mode_name}
    await Player.update_or_create(id=ctx.user.id, defaults=defaults)
    await ctx.respond(f'Mode set to {mode_name}.',
                      flags=MessageFlag.EPHEMERAL)

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

async def _pb_component(ctx: GatewayContext, player: Player,
                        pb: PersonalBest) -> ContainerComponentBuilder:
    player_name = pb.player_name or ctx.user.display_name
    if pb.mode == Mode.VNL:
        profile_url = f'https://vnl.kz/#/stats/{player.steamid64}'
        map_url = f'https://vnl.kz/#/map/{pb.map.name}'
        tier = pb.map.vnl_pro_tier if pb.teleports == 0 else pb.map.vnl_tier
    else:
        profile_url = f'https://kzgo.eu/players/{player.steamid64}?{pb.mode}'
        map_url = f'https://kzgo.eu/maps/{pb.map.name}?{pb.mode}'
        tier = pb.map.tier
    body = f"""## [{player_name}]({profile_url}) on [{pb.map.name}]({map_url})

**Mode:** {pb.mode.upper()}{' (PRO)' if pb.teleports == 0 else ''}
**Tier:** {tier or '(unknown)'}
**Time:** {_formattime(pb.time)}
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
    if pb.map.thumbnail is not None:
        gallery = MediaGalleryComponentBuilder()
        gallery.add_media_gallery_item(pb.map.thumbnail)
        container.add_component(gallery)
    container.add_text_display(f'-# <t:{int(pb.date.timestamp())}>')
    return container

@client.include
@slash_command('pb', 'Show personal best times')
async def slash_pb(ctx: GatewayContext,
                   map_name: Option[str, StrParams('Map name', name='map')],
                   teleports: Option[str | None, TeleportParams]=None,
                   mode_name: Option[str | None, ModeParams]=None,
                   player_member: Option[Member | None, PlayerParams]=None
                   ) -> None:
    try:
        player = await Player.get(id=(player_member or ctx.user).id)
    except DoesNotExist:
        await ctx.respond('Not registered!', flags=MessageFlag.EPHEMERAL)
        return
    if mode_name is None:
        mode = player.mode
    else:
        mode = Mode(mode_name)

    try:
        api_map = await map_for_name(map_name, mode)
        pbs = await pbs_for_steamid64(player.steamid64, api_map, mode)
    except APIMapAmbiguousError as e:
        if len(e.db_maps) > 10:
            await ctx.respond(f'More than 10 maps found!',
                              flags=MessageFlag.EPHEMERAL)
        else:
            map_names = sorted(m.name for m in e.db_maps)
            await ctx.respond(f"Multiple maps found: {', '.join(map_names)}",
                              flags=MessageFlag.EPHEMERAL)
        return
    except APIMapError:
        await ctx.respond('Map not found!', flags=MessageFlag.EPHEMERAL)
        return
    except APIError:
        await ctx.respond("Couldn't access global API!",
                          flags=MessageFlag.EPHEMERAL)
        return

    if teleports == 'pro':
        pbs = [pb for pb in pbs if pb.teleports == 0]
    elif teleports == 'tp':
        pbs = [pb for pb in pbs if pb.teleports]
    if not pbs:
        await ctx.respond('No PBs found!', flags=MessageFlag.EPHEMERAL)
        return

    pbs.sort(key=lambda pb: pb.time)
    pb = pbs[0]
    component = await _pb_component(ctx, player, pb)
    await ctx.respond(component=component)

@client.include
@slash_command('latest', 'Show most recent personal best')
async def slash_latest(ctx: GatewayContext,
                       teleports: Option[str | None, TeleportParams]=None,
                       mode_name: Option[str | None, ModeParams]=None,
                       player_member: Option[Member | None, PlayerParams]=None
                       ) -> None:
    try:
        player = await Player.get(id=(player_member or ctx.user).id)
    except DoesNotExist:
        await ctx.respond('Not registered!', flags=MessageFlag.EPHEMERAL)
        return
    if mode_name is None:
        mode = player.mode
    else:
        mode = Mode(mode_name)

    pb = await latest_pb_for_steamid64(player.steamid64, mode, teleports)
    if not pb:
        await ctx.respond("No PB found!",
                          flags=MessageFlag.EPHEMERAL)
        return

    component = await _pb_component(ctx, player, pb)
    await ctx.respond(component=component)
