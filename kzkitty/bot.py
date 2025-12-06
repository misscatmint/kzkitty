import os
from typing import Any

from arc import (GatewayClient, GatewayContext, IntParams, MemberParams, Option, StrParams, slash_command)
from hikari import Intents, Member, MessageFlag
from tortoise.exceptions import DoesNotExist

from kzkitty.api.kz import (APIError, APIMapError, APIMapAmbiguousError,
                            latest_pb_for_steamid64,
                            map_for_name, pb_for_steamid64,
                            profile_for_steamid64, wrs_for_map)
from kzkitty.api.steam import (SteamError, SteamValueError,
                               steamid64_for_profile)
from kzkitty.components import map_component, pb_component, profile_component
from kzkitty.gateway import GatewayBot
from kzkitty.models import Mode, Player, Type

bot = GatewayBot(os.environ['KZKITTY_DISCORD_TOKEN'], intents=Intents.NONE)
client = GatewayClient(bot)

ModeParams = StrParams('Game mode', name='mode',
                       choices=[Mode.KZT, Mode.SKZ, Mode.VNL])
PlayerParams = MemberParams('Player', name='player')
TypeParams = StrParams('Pro or teleport run', name='type',
                       choices=[Type.PRO, Type.TP, Type.ANY])
StageParams = IntParams('Bonus stage', name='bonus', min=1)

class PlayerNotFound(Exception):
    pass

async def _get_player(ctx: GatewayContext, player_member: Member | None=None
                      ) -> Player:
    try:
        return await Player.get(user_id=(player_member or ctx.user).id,
                                server_id=ctx.guild_id)
    except DoesNotExist:
        raise PlayerNotFound

@client.set_error_handler
async def error_handler(ctx: GatewayContext, exc: Exception) -> None:
    if isinstance(exc, PlayerNotFound):
        await ctx.respond('Not registered!', flags=MessageFlag.EPHEMERAL)
        return
    elif isinstance(exc, APIMapAmbiguousError):
        if len(exc.db_maps) > 10:
            await ctx.respond('More than 10 maps found!',
                              flags=MessageFlag.EPHEMERAL)
        else:
            map_names = sorted(m.name for m in exc.db_maps)
            await ctx.respond(f"Multiple maps found: {', '.join(map_names)}",
                              flags=MessageFlag.EPHEMERAL)
        return
    elif isinstance(exc, APIMapError):
        await ctx.respond('Map not found!', flags=MessageFlag.EPHEMERAL)
        return
    elif isinstance(exc, SteamError):
        await ctx.respond("Couldn't access Steam API!",
                          flags=MessageFlag.EPHEMERAL)
    elif isinstance(exc, APIError):
        await ctx.respond("Couldn't access global API!",
                          flags=MessageFlag.EPHEMERAL)
    raise exc

@client.include
@slash_command('register', 'Register account')
async def slash_register(ctx: GatewayContext,
                         profile: Option[str, StrParams('Steam profile URL')],
                         mode_name: Option[str | None, ModeParams]=Mode.KZT
                         ) -> None:
    try:
        steamid64 = await steamid64_for_profile(profile)
    except SteamValueError:
        await ctx.respond('Invalid Steam profile URL!',
                          flags=MessageFlag.EPHEMERAL)
    else:
        defaults: dict[str, Any] = {'steamid64': steamid64}
        defaults['mode'] = mode_name
        await Player.update_or_create(user_id=ctx.user.id,
                                      server_id=ctx.guild_id,
                                      defaults=defaults)
        await ctx.respond('Registered!', flags=MessageFlag.EPHEMERAL)

@client.include
@slash_command('unregister', 'Delete account settings')
async def slash_unregister(ctx: GatewayContext) -> None:
    player = await _get_player(ctx)
    await player.delete()
    await ctx.respond('Unregistered!', flags=MessageFlag.EPHEMERAL)

@client.include
@slash_command('mode', 'Show or set default game mode')
async def slash_mode(ctx: GatewayContext,
                     mode_name: Option[str | None, ModeParams]=None) -> None:
    if mode_name is None:
        player = await _get_player(ctx)
        await ctx.respond(f'Mode set to {player.mode}.',
                          flags=MessageFlag.EPHEMERAL)
        return

    defaults = {'mode': mode_name}
    await Player.update_or_create(user_id=ctx.user.id,
                                  server_id=ctx.guild_id,
                                  defaults=defaults)
    await ctx.respond(f'Mode set to {mode_name}.',
                      flags=MessageFlag.EPHEMERAL)

@client.include
@slash_command('pb', 'Show personal best times')
async def slash_pb(ctx: GatewayContext,
                   map_name: Option[str, StrParams('Map name', name='map')],
                   type_name: Option[str, TypeParams]=Type.ANY,
                   mode_name: Option[str | None, ModeParams]=None,
                   stage: Option[int, StageParams]=0,
                   player_member: Option[Member | None, PlayerParams]=None
                   ) -> None:
    player = await _get_player(ctx, player_member)
    mode = player.mode if mode_name is None else Mode(mode_name)
    api_map = await map_for_name(map_name, mode)
    pb = await pb_for_steamid64(player.steamid64, api_map, mode,
                                Type(type_name), stage)
    if not pb:
        await ctx.respond('No PB found!', flags=MessageFlag.EPHEMERAL)
        return

    component = await pb_component(pb, player, ctx.user)
    await ctx.respond(component=component)

@client.include
@slash_command('latest', 'Show most recent personal best')
async def slash_latest(ctx: GatewayContext,
                       type_name: Option[str, TypeParams]=Type.ANY,
                       mode_name: Option[str | None, ModeParams]=None,
                       player_member: Option[Member | None, PlayerParams]=None
                       ) -> None:
    player = await _get_player(ctx, player_member)
    mode = player.mode if mode_name is None else Mode(mode_name)
    pb = await latest_pb_for_steamid64(player.steamid64, mode,
                                       Type(type_name))
    if not pb:
        await ctx.respond('No PB found!', flags=MessageFlag.EPHEMERAL)
        return

    component = await pb_component(pb, player, ctx.user)
    await ctx.respond(component=component)

@client.include
@slash_command('map', 'Show map info and world record times')
async def slash_map(ctx: GatewayContext,
                    map_name: Option[str, StrParams('Map name', name='map')],
                    mode_name: Option[str | None, ModeParams]=None,
                    stage: Option[int, StageParams]=0) -> None:
    if mode_name is not None:
        mode = Mode(mode_name)
    else:
        try:
            player = await _get_player(ctx)
        except PlayerNotFound:
            mode = Mode.KZT
        else:
            mode = player.mode
    api_map = await map_for_name(map_name, mode)
    # If the player has their mode set to VNL and they do /map on
    # a VNL-impossible map, show KZT times if they didn't explicitly ask for
    # VNL times.
    if (stage == 0 and mode_name is None and mode == Mode.VNL and
        api_map.vnl_tier == 10):
        mode = Mode.KZT
    wrs = await wrs_for_map(api_map, mode, stage)
    component = await map_component(api_map, mode, stage, wrs)
    await ctx.respond(component=component)

@client.include
@slash_command('profile', 'Show rank, point total, and point average')
async def slash_profile(ctx: GatewayContext,
                        mode_name: Option[str | None, ModeParams]=None,
                        player_member: Option[Member | None, PlayerParams]=None
                        ) -> None:
    player = await _get_player(ctx, player_member)
    mode = player.mode if mode_name is None else Mode(mode_name)
    profile = await profile_for_steamid64(player.steamid64, mode)
    component = await profile_component(profile, player, ctx.user)
    await ctx.respond(component=component)
