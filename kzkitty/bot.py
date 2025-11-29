import os
from typing import Any

from arc import (GatewayClient, GatewayContext, MemberParams, Option,
                 StrParams, slash_command)
from hikari import Member, MessageFlag
from tortoise.exceptions import DoesNotExist

from kzkitty.api.kz import (APIError, APIMapError, APIMapAmbiguousError,
                            latest_pb_for_steamid64,
                            map_for_name, pb_for_steamid64,
                            profile_for_steamid64)
from kzkitty.api.steam import (SteamError, SteamValueError,
                               steamid64_for_profile)
from kzkitty.components import pb_component, profile_component
from kzkitty.gateway import GatewayBot
from kzkitty.models import Mode, Player, Type

bot = GatewayBot(os.environ['KZKITTY_DISCORD_TOKEN'])
client = GatewayClient(bot)

ModeParams = StrParams('Game mode', name='mode',
                       choices=[Mode.KZT, Mode.SKZ, Mode.VNL])
PlayerParams = MemberParams('Player', name='player')
TypeParams = StrParams('Pro or teleport run', name='type',
                       choices=[Type.PRO, Type.TP, Type.ANY])

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
        await ctx.respond('Registered!', flags=MessageFlag.EPHEMERAL)

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

@client.include
@slash_command('pb', 'Show personal best times')
async def slash_pb(ctx: GatewayContext,
                   map_name: Option[str, StrParams('Map name', name='map')],
                   type_name: Option[str, TypeParams]=Type.ANY,
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
        pb = await pb_for_steamid64(player.steamid64, api_map, mode,
                                    Type(type_name))
    except APIMapAmbiguousError as e:
        if len(e.db_maps) > 10:
            await ctx.respond('More than 10 maps found!',
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

    if not pb:
        await ctx.respond('No PB found!', flags=MessageFlag.EPHEMERAL)
        return

    component = await pb_component(ctx, player, pb)
    await ctx.respond(component=component)

@client.include
@slash_command('latest', 'Show most recent personal best')
async def slash_latest(ctx: GatewayContext,
                       type_name: Option[str, TypeParams]=Type.ANY,
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
        pb = await latest_pb_for_steamid64(player.steamid64, mode,
                                           Type(type_name))
    except APIError:
        await ctx.respond("Couldn't access global API!",
                          flags=MessageFlag.EPHEMERAL)
        return
    if not pb:
        await ctx.respond("No PB found!",
                          flags=MessageFlag.EPHEMERAL)
        return

    component = await pb_component(ctx, player, pb)
    await ctx.respond(component=component)

@client.include
@slash_command('profile', 'Show rank, point total, and point average')
async def slash_profile(ctx: GatewayContext,
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
        profile = await profile_for_steamid64(player.steamid64, mode)
    except APIError:
        await ctx.respond("Couldn't access global API!",
                          flags=MessageFlag.EPHEMERAL)
        return

    component = await profile_component(ctx, player, profile)
    await ctx.respond(component=component)
