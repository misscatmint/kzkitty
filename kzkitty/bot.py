"""Bot object and command implementations"""

import asyncio
import logging
from typing import Any

from arc import (AutocompleteData, AutodeferMode, Context, GatewayClient,
                 IntParams, MemberParams, Option, RESTClient, StrParams,
                 slash_command)
from arc.abc.client import Client
from arc.utils import IntervalLoop
from hikari import GatewayBot, Intents, Member, MessageFlag, RESTBot
from tortoise.exceptions import DoesNotExist

from kzkitty.api.kz import (API, APIConnectionError, APIError, APIMap,
                            APIMapError, APIMapNotFoundError,
                            APIMapAmbiguousError, api_for_mode, close_api,
                            init_api, refresh_map_db)
from kzkitty.api.steam import (SteamError, SteamValueError, close_steam,
                               get_steam, init_steam)
from kzkitty.components import map_component, pb_component, profile_component
from kzkitty.models import (Map, Mode, Player, Type, close_db,
                            import_default_players, init_db)

_logger = logging.getLogger('kzkitty.bot')

_ClientT = Client[Any] # pyright: ignore[reportExplicitAny]
_ContextT = Context[Any] # pyright: ignore[reportExplicitAny]

_tasks: set[asyncio.Task[Any]] = set() # pyright: ignore[reportExplicitAny]

def _setup(client: _ClientT, db_url: str, refresh_db_hours: int,
           api_timeout: int, steam_timeout: int) -> None:
    """Register bot commands and hooks"""
    client.set_error_handler(_handle_error)
    client.include(_slash_register) # ty: ignore[invalid-argument-type]
    client.include(_slash_unregister) # ty: ignore[invalid-argument-type]
    client.include(_slash_mode) # ty: ignore[invalid-argument-type]
    client.include(_slash_pb) # ty: ignore[invalid-argument-type]
    client.include(_slash_latest) # ty: ignore[invalid-argument-type]
    client.include(_slash_map) # ty: ignore[invalid-argument-type]
    client.include(_slash_profile) # ty: ignore[invalid-argument-type]

    # This uses minutes because the hours and days parameters are broken in arc
    refresh_db_loop = IntervalLoop(refresh_map_db,
                                   minutes=refresh_db_hours * 60,
                                   run_on_start=True)
    async def startup(_: _ClientT) -> None:
        await init_api(timeout=api_timeout)
        await init_steam(timeout=steam_timeout)
        await init_db(db_url)
        task = asyncio.create_task(import_default_players())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        refresh_db_loop.start()
    client.add_startup_hook(startup)

    async def shutdown(_: _ClientT) -> None:
        await close_api()
        await close_steam()
        await close_db()
    client.add_shutdown_hook(shutdown)

def run(discord_token: str, db_url: str, refresh_db_hours: int=24,
        api_timeout: int=15, steam_timeout: int=5) -> None:
    """Start the bot's main event loop (as a gateway bot)"""
    bot = GatewayBot(discord_token, intents=Intents.NONE, banner=None,
                     suppress_optimization_warning=True)
    client = GatewayClient(bot)
    _setup(client, db_url, refresh_db_hours, api_timeout, steam_timeout)
    bot.run(check_for_updates=False)

def runrest(host: str, port: int, discord_token: str, db_url: str,
            refresh_db_hours: int=24, api_timeout: int=15,
            steam_timeout: int=5) -> None:
    """Start the bot's main event loop (as a REST bot)"""
    bot = RESTBot(discord_token, banner=None,
                  suppress_optimization_warning=True)
    client = RESTClient(bot)
    _setup(client, db_url, refresh_db_hours, api_timeout, steam_timeout)
    bot.run(host=host, port=port, check_for_updates=False)

async def _autocomplete_map(data: AutocompleteData[_ClientT, str]) -> list[str]:
    """Autocomplete map names for slash commands"""
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

_MapParams = StrParams('Map name', name='map',
                       autocomplete_with=_autocomplete_map)
_ModeParams = StrParams('Game mode', name='mode',
                        choices=[Mode.KZT, Mode.SKZ, Mode.VNL, Mode.CKZ,
                                 Mode.VNL2])
_PlayerParams = MemberParams('Player', name='player')
_TypeParams = StrParams('Pro or teleport run', name='type',
                        choices=[Type.PRO, Type.TP, Type.ANY])
_CourseParams = StrParams('Course')
_BonusParams = IntParams('Bonus', min=1)

class _PlayerNotFound(Exception):
    pass

async def _get_player(ctx: _ContextT, player_member: Member | None=None
                      ) -> Player:
    """Look up a registered player.

    If the user isn't registered, this raises an error for the error handler
    to present a friendly error message.
    """
    try:
        return await Player.get(user_id=(player_member or ctx.user).id,
                                server_id=ctx.guild_id)
    except DoesNotExist:
        raise _PlayerNotFound

async def _get_map(mode: Mode, mode_name: str | None, map_name: str,
                   course: str | None=None, bonus: int | None=None
                   ) -> tuple[API, APIMap]:
    """Look up a map/course/bonus for a given mode.

    If the user hasn't explicitly chosen a specific mode, this will fall back
    to looking up the map for both CS:GO and CS2.
    """
    api = api_for_mode(mode)
    try:
        api_map = await api.get_map(map_name, mode, course, bonus)
    except (APIConnectionError, APIMapNotFoundError) as e:
        if mode_name is not None:
            raise
        mode = {Mode.KZT: Mode.CKZ,
                Mode.SKZ: Mode.CKZ,
                Mode.VNL: Mode.VNL2,
                Mode.CKZ: Mode.KZT,
                Mode.VNL2: Mode.VNL}[mode]
        if isinstance(e, APIConnectionError):
            _logger.exception('API connection failure during map lookup')
        api = api_for_mode(mode)
        api_map = await api.get_map(map_name, mode, course, bonus)
        return api, api_map
    else:
        # If the player has their mode set to VNL and they do /map on
        # a VNL-impossible map, show KZT/CKZ times if they didn't explicitly
        # ask for VNL times.
        if (bonus is None and mode_name is None and
            mode in {Mode.VNL, Mode.VNL2} and api_map.tier == 10):
            mode = Mode.KZT if mode == Mode.VNL else Mode.CKZ
            api = api_for_mode(mode)
            api_map = await api.get_map(map_name, mode, course, bonus)
        return api, api_map

async def _handle_error(ctx: _ContextT, exc: Exception) -> None:
    """Turn certain exceptions into friendly error messages.

    SteamError and APIError will still get raised.
    """
    if isinstance(exc, _PlayerNotFound):
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

@slash_command('register', 'Register account',
               autodefer=AutodeferMode.EPHEMERAL)
async def _slash_register(ctx: _ContextT,
                          profile: Option[str,
                                          StrParams('Steam profile URL')],
                          mode_name: Option[str, _ModeParams]=Mode.KZT
                          ) -> None:
    """Register the user with a given Steam profile and game mode"""
    steam = get_steam()
    try:
        steamid64 = await steam.steamid64_for_profile(profile)
    except SteamValueError:
        await ctx.respond('Invalid Steam profile URL',
                          flags=MessageFlag.EPHEMERAL)
    else:
        defaults: dict[str, int | Mode] = {'steamid64': steamid64}
        defaults['mode'] = Mode(mode_name)
        await Player.update_or_create(user_id=ctx.user.id,
                                      server_id=ctx.guild_id,
                                      defaults=defaults)
        await ctx.respond('Registered', flags=MessageFlag.EPHEMERAL)

@slash_command('unregister', 'Delete account settings')
async def _slash_unregister(ctx: _ContextT) -> None:
    """Unregister the user"""
    player = await _get_player(ctx)
    await player.delete()
    await ctx.respond('Unregistered', flags=MessageFlag.EPHEMERAL)

@slash_command('mode', 'Show or set default game mode')
async def _slash_mode(ctx: _ContextT,
                      mode_name: Option[str | None, _ModeParams]=None
                      ) -> None:
    """Set the user's default game mode"""
    player = await _get_player(ctx)
    if mode_name is None:
        await ctx.respond(f'Mode set to {player.mode}',
                          flags=MessageFlag.EPHEMERAL)
        return
    player.mode = Mode(mode_name)
    await player.save()
    await ctx.respond(f'Mode set to {mode_name}',
                      flags=MessageFlag.EPHEMERAL)

@slash_command('pb', 'Show personal best times', autodefer=True)
async def _slash_pb(ctx: _ContextT,
                    map_name: Option[str, _MapParams],
                    type_name: Option[str, _TypeParams]=Type.ANY,
                    mode_name: Option[str | None, _ModeParams]=None,
                    course: Option[str | None, _CourseParams]=None,
                    bonus: Option[int | None, _BonusParams]=None,
                    player_member: Option[Member | None, _PlayerParams]=None
                    ) -> None:
    """Look up a personal best time"""
    player = await _get_player(ctx, player_member)
    mode = player.mode if mode_name is None else Mode(mode_name)
    api, api_map = await _get_map(mode, mode_name, map_name, course, bonus)
    pb = await api.get_pb(player.steamid64, api_map, Type(type_name))
    if not pb:
        await ctx.respond('No times found', flags=MessageFlag.EPHEMERAL)
        return
    component = await pb_component(pb, player, ctx.user)
    await ctx.respond(component=component)

@slash_command('latest', 'Show most recent personal best', autodefer=True)
async def _slash_latest(ctx: _ContextT,
                        type_name: Option[str, _TypeParams]=Type.ANY,
                        mode_name: Option[str | None, _ModeParams]=None,
                        player_member: Option[Member | None,
                                              _PlayerParams]=None
                        ) -> None:
    """Look up the user's latest personal best for a given game mode"""
    player = await _get_player(ctx, player_member)
    mode = player.mode if mode_name is None else Mode(mode_name)
    api = api_for_mode(mode)
    pb = await api.get_latest(player.steamid64, mode, Type(type_name))
    if not pb:
        await ctx.respond('No times found', flags=MessageFlag.EPHEMERAL)
        return

    component = await pb_component(pb, player, ctx.user)
    await ctx.respond(component=component)

@slash_command('map', 'Show map info and world record times', autodefer=True)
async def _slash_map(ctx: _ContextT,
                     map_name: Option[str, _MapParams],
                     mode_name: Option[str | None, _ModeParams]=None,
                     course: Option[str | None, _CourseParams]=None,
                     bonus: Option[int | None, _BonusParams]=None) -> None:
    """Look up map info"""
    if mode_name is not None:
        mode = Mode(mode_name)
    else:
        try:
            player = await _get_player(ctx)
        except _PlayerNotFound:
            mode = Mode.KZT
        else:
            mode = player.mode

    api, api_map = await _get_map(mode, mode_name, map_name, course, bonus)
    wrs = await api.get_wrs(api_map)
    component = await map_component(api_map, wrs)
    await ctx.respond(component=component)

@slash_command('profile', 'Show rank, point total, and point average',
               autodefer=True)
async def _slash_profile(ctx: _ContextT,
                         mode_name: Option[str | None, _ModeParams]=None,
                         player_member: Option[Member | None,
                                               _PlayerParams]=None
                         ) -> None:
    """Look up the user's profile information"""
    player = await _get_player(ctx, player_member)
    mode = player.mode if mode_name is None else Mode(mode_name)
    api = api_for_mode(mode)
    profile = await api.get_profile(player.steamid64, mode)
    component = await profile_component(profile, player, ctx.user)
    await ctx.respond(component=component)
