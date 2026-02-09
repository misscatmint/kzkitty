import asyncio
import logging
import os
from typing import Any

from arc import (AutocompleteData, AutodeferMode, GatewayClient,
                 GatewayContext, IntParams, MemberParams, Option, StrParams,
                 slash_command)
from arc.utils import IntervalLoop
from hikari import GatewayBot, Intents, Member, MessageFlag
from tortoise.exceptions import DoesNotExist

from kzkitty.api.kz import (API, APIConnectionError, APIError, APIMap,
                            APIMapError, APIMapNotFoundError,
                            APIMapAmbiguousError, api_for_mode,
                            refresh_db_maps)
from kzkitty.api.steam import (SteamError, SteamValueError,
                               steamid64_for_profile)
from kzkitty.components import map_component, pb_component, profile_component
from kzkitty.models import (Map, Mode, Player, Type, close_db,
                            import_default_players, init_db)

bot = GatewayBot(os.environ['KZKITTY_DISCORD_TOKEN'], intents=Intents.NONE)
client = GatewayClient(bot)
logger = logging.getLogger('kzkitty.bot')
refresh_db_loop = IntervalLoop(refresh_db_maps, hours=1, run_on_start=True)

@client.add_startup_hook
async def startup_hook(_: GatewayClient) -> None:
    await init_db()
    asyncio.create_task(import_default_players())
    refresh_db_loop.start()

@client.add_shutdown_hook
async def shutdown_hook(_: GatewayClient) -> None:
    await close_db()

async def autocomplete_map(data: AutocompleteData[GatewayClient, str]
                           ) -> list[str]:
    if not data.focused_value:
        return []
    name = data.focused_value.lower()
    if len(name) < 3 or name in {'kz_', 'bkz', 'bkz_'}:
        return []
    maps = (await Map.filter(name__contains=name)
                     .order_by('name')
                     .limit(25)
                     .distinct()
                     .values('name'))
    return [m['name'] for m in maps]

MapParams = StrParams('Map name', name='map',
                      autocomplete_with=autocomplete_map)
ModeParams = StrParams('Game mode', name='mode',
                       choices=[Mode.KZT, Mode.SKZ, Mode.VNL, Mode.CKZ,
                                Mode.VNL2])
PlayerParams = MemberParams('Player', name='player')
TypeParams = StrParams('Pro or teleport run', name='type',
                       choices=[Type.PRO, Type.TP, Type.ANY])
CourseParams = StrParams('Course')
BonusParams = IntParams('Bonus', min=1)

class PlayerNotFound(Exception):
    pass

async def _get_player(ctx: GatewayContext, player_member: Member | None=None
                      ) -> Player:
    try:
        return await Player.get(user_id=(player_member or ctx.user).id,
                                server_id=ctx.guild_id)
    except DoesNotExist:
        raise PlayerNotFound

async def _get_map(mode: Mode, mode_name: str | None, map_name: str,
                   course: str | None=None, bonus: int | None=None
                   ) -> tuple[API, APIMap]:
    api = api_for_mode(mode)
    try:
        api_map = await api.get_map(map_name, course, bonus)
    except (APIConnectionError, APIMapNotFoundError) as e:
        if mode_name is not None:
            raise
        elif mode in {Mode.KZT, Mode.SKZ, Mode.VNL}:
            mode = Mode.CKZ
        else:
            mode = Mode.KZT
        if isinstance(e, APIConnectionError):
            logger.exception('API connection failure during map lookup')
        api = api_for_mode(mode)
        api_map = await api.get_map(map_name, course, bonus)
    else:
        # If the player has their mode set to VNL and they do /map on
        # a VNL-impossible map, show KZT/CKZ times if they didn't explicitly
        # ask for VNL times.
        if (bonus is None and mode_name is None and
            mode in {Mode.VNL, Mode.VNL2} and api_map.tier == 10):
            mode = Mode.KZT if mode == Mode.VNL else Mode.CKZ
            api = api_for_mode(mode)
            api_map = await api.get_map(map_name, course, bonus)
    return api, api_map

@client.set_error_handler
async def error_handler(ctx: GatewayContext, exc: Exception) -> None:
    if isinstance(exc, PlayerNotFound):
        await ctx.respond('Not registered', flags=MessageFlag.EPHEMERAL)
        return
    elif isinstance(exc, APIMapAmbiguousError):
        if len(exc.db_maps) > 10:
            await ctx.respond('More than 10 maps found',
                              flags=MessageFlag.EPHEMERAL)
        else:
            map_names = sorted(m.name for m in exc.db_maps)
            await ctx.respond(f"Multiple maps found: {', '.join(map_names)}",
                              flags=MessageFlag.EPHEMERAL)
        return
    elif isinstance(exc, APIMapError):
        await ctx.respond(str(exc), flags=MessageFlag.EPHEMERAL)
        return
    elif isinstance(exc, SteamError):
        await ctx.respond("Couldn't access Steam API",
                          flags=MessageFlag.EPHEMERAL)
    elif isinstance(exc, APIError):
        await ctx.respond("Couldn't access global API",
                          flags=MessageFlag.EPHEMERAL)
    raise exc

@client.include
@slash_command('register', 'Register account',
               autodefer=AutodeferMode.EPHEMERAL)
async def slash_register(ctx: GatewayContext,
                         profile: Option[str, StrParams('Steam profile URL')],
                         mode_name: Option[str | None, ModeParams]=Mode.KZT
                         ) -> None:
    try:
        steamid64 = await steamid64_for_profile(profile)
    except SteamValueError:
        await ctx.respond('Invalid Steam profile URL',
                          flags=MessageFlag.EPHEMERAL)
    else:
        defaults: dict[str, Any] = {'steamid64': steamid64}
        defaults['mode'] = mode_name
        await Player.update_or_create(user_id=ctx.user.id,
                                      server_id=ctx.guild_id,
                                      defaults=defaults)
        await ctx.respond('Registered', flags=MessageFlag.EPHEMERAL)

@client.include
@slash_command('unregister', 'Delete account settings')
async def slash_unregister(ctx: GatewayContext) -> None:
    player = await _get_player(ctx)
    await player.delete()
    await ctx.respond('Unregistered', flags=MessageFlag.EPHEMERAL)

@client.include
@slash_command('mode', 'Show or set default game mode')
async def slash_mode(ctx: GatewayContext,
                     mode_name: Option[str | None, ModeParams]=None) -> None:
    if mode_name is None:
        player = await _get_player(ctx)
        await ctx.respond(f'Mode set to {player.mode}',
                          flags=MessageFlag.EPHEMERAL)
        return

    defaults = {'mode': mode_name}
    await Player.update_or_create(user_id=ctx.user.id,
                                  server_id=ctx.guild_id,
                                  defaults=defaults)
    await ctx.respond(f'Mode set to {mode_name}',
                      flags=MessageFlag.EPHEMERAL)

@client.include
@slash_command('pb', 'Show personal best times', autodefer=True)
async def slash_pb(ctx: GatewayContext,
                   map_name: Option[str, MapParams],
                   type_name: Option[str, TypeParams]=Type.ANY,
                   mode_name: Option[str | None, ModeParams]=None,
                   course: Option[str | None, CourseParams]=None,
                   bonus: Option[int | None, BonusParams]=None,
                   player_member: Option[Member | None, PlayerParams]=None
                   ) -> None:
    player = await _get_player(ctx, player_member)
    mode = player.mode if mode_name is None else Mode(mode_name)
    api, api_map = await _get_map(mode, mode_name, map_name, course, bonus)
    pb = await api.get_pb(player.steamid64, api_map, Type(type_name))
    if not pb:
        await ctx.respond('No times found', flags=MessageFlag.EPHEMERAL)
        return
    component = await pb_component(pb, player, ctx.user)
    await ctx.respond(component=component)

@client.include
@slash_command('latest', 'Show most recent personal best', autodefer=True)
async def slash_latest(ctx: GatewayContext,
                       type_name: Option[str, TypeParams]=Type.ANY,
                       mode_name: Option[str | None, ModeParams]=None,
                       player_member: Option[Member | None, PlayerParams]=None
                       ) -> None:
    player = await _get_player(ctx, player_member)
    mode = player.mode if mode_name is None else Mode(mode_name)
    api = api_for_mode(mode)
    pb = await api.get_latest(player.steamid64, Type(type_name))
    if not pb:
        await ctx.respond('No times found', flags=MessageFlag.EPHEMERAL)
        return

    component = await pb_component(pb, player, ctx.user)
    await ctx.respond(component=component)

@client.include
@slash_command('map', 'Show map info and world record times', autodefer=True)
async def slash_map(ctx: GatewayContext,
                    map_name: Option[str, MapParams],
                    mode_name: Option[str | None, ModeParams]=None,
                    course: Option[str | None, CourseParams]=None,
                    bonus: Option[int | None, BonusParams]=None) -> None:
    if mode_name is not None:
        mode = Mode(mode_name)
    else:
        try:
            player = await _get_player(ctx)
        except PlayerNotFound:
            mode = Mode.KZT
        else:
            mode = player.mode

    api, api_map = await _get_map(mode, mode_name, map_name, course, bonus)
    wrs = await api.get_wrs(api_map)
    component = await map_component(api_map, wrs, api.has_tp_wrs())
    await ctx.respond(component=component)

@client.include
@slash_command('profile', 'Show rank, point total, and point average',
               autodefer=True)
async def slash_profile(ctx: GatewayContext,
                        mode_name: Option[str | None, ModeParams]=None,
                        player_member: Option[Member | None, PlayerParams]=None
                        ) -> None:
    player = await _get_player(ctx, player_member)
    mode = player.mode if mode_name is None else Mode(mode_name)
    api = api_for_mode(mode)
    profile = await api.get_profile(player.steamid64)
    component = await profile_component(profile, player, ctx.user)
    await ctx.respond(component=component)
