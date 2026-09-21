"""Bot object and command implementations"""

import asyncio
import contextlib
import logging
from typing import Annotated, Any, cast

from arc import (AutocompleteData, AutodeferMode, Context, GatewayClient,
                 IntParams, MemberParams, RESTClient, StrParams,
                 slash_command)
from arc.abc.client import Client
from arc.utils import IntervalLoop
from hikari import (ForbiddenError, GatewayBot, Intents, Member, MessageFlag,
                    NotFoundError, RESTBot)
from tortoise.exceptions import DoesNotExist

from kzkitty.api import a2s
from kzkitty.api.kz import (API, APIConnectionError, APIError, APIMap,
                            APIMapError, APIMapNotFoundError,
                            APIMapAmbiguousError, api_for_mode, close_api,
                            init_api, refresh_map_db)
from kzkitty.api.steam import (SteamError, SteamValueError, close_steam,
                               get_steam, init_steam)
from kzkitty.components import (map_component, pb_component,
                                profile_component, server_component,
                                server_failed_component)
from kzkitty.models import (Map, Mode, Player, Server, Type, close_db,
                            import_defaults, init_db)

type _Client = Client[Any] # pyright: ignore[reportExplicitAny]
type _Context = Context[Any] # pyright: ignore[reportExplicitAny]

_logger = logging.getLogger('kzkitty.bot')
_tasks: set[asyncio.Task[None]] = set()

def _setup(client: _Client, db_url: str, refresh_db_hours: int,
           refresh_server_mins: int, api_timeout: int, steam_timeout: int,
           a2s_timeout: int) -> None:
    """Register bot commands and hooks"""
    client.set_error_handler(_handle_error)
    client.include(_slash_register)
    client.include(_slash_unregister)
    client.include(_slash_mode)
    client.include(_slash_pb)
    client.include(_slash_latest)
    client.include(_slash_map)
    client.include(_slash_profile)
    client.include(_slash_server)

    # This uses minutes because the hours and days parameters are broken in arc
    refresh_db_loop = IntervalLoop(refresh_map_db, hours=refresh_db_hours,
                                   run_on_start=True)
    refresh_servers_loop = IntervalLoop(refresh_servers,
                                        minutes=refresh_server_mins,
                                        run_on_start=True)
    async def startup(client: _Client) -> None:
        init_api(timeout=api_timeout)
        init_steam(timeout=steam_timeout)
        a2s.init_a2s(timeout=a2s_timeout)
        await init_db(db_url)
        task = asyncio.create_task(import_defaults())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        refresh_db_loop.start()
        refresh_servers_loop.start(client)
    client.add_startup_hook(startup)

    async def shutdown(_: _Client) -> None:
        await close_api()
        await close_steam()
        await close_db()
    client.add_shutdown_hook(shutdown)

def run(discord_token: str, db_url: str, refresh_db_hours: int=24,
        refresh_server_mins: int=1, api_timeout: int=15, steam_timeout: int=5,
        a2s_timeout: int=5) -> None:
    """Start the bot's main event loop (as a gateway bot)"""
    bot = GatewayBot( # ty: ignore[call-non-callable]
                     discord_token, intents=Intents.NONE, banner=None,
                     suppress_optimization_warning=True)
    client = GatewayClient(bot)
    _setup(client, db_url, refresh_db_hours, refresh_server_mins, api_timeout,
           steam_timeout, a2s_timeout)
    bot.run(check_for_updates=False)

def runrest(host: str, port: int, discord_token: str, db_url: str,
            refresh_db_hours: int=24, refresh_server_mins: int=1,
            api_timeout: int=15, steam_timeout: int=5, a2s_timeout: int=5
            ) -> None:
    """Start the bot's main event loop (as a REST bot)"""
    bot = RESTBot(discord_token, banner=None,
                  suppress_optimization_warning=True)
    client = RESTClient(bot)
    _setup(client, db_url, refresh_db_hours, refresh_server_mins, api_timeout,
           steam_timeout, a2s_timeout)
    bot.run(host=host, port=port, check_for_updates=False)

async def _autocomplete_map(data: AutocompleteData[_Client, str]) -> list[str]:
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

async def _autocomplete_address(data: AutocompleteData[_Client, str]
                                ) -> list[str]:
    """Autocomplete server addresses for slash commands"""
    servers = (Server.filter(server_id=data.guild_id)
                     .order_by('-is_default', 'address')
                     .limit(25))
    if data.focused_value:
        address = data.focused_value.lower()
        servers = servers.filter(address=address)
    return [s['address'] for s in await servers.values('address')]

type _SteamProfileURLOption = Annotated[str, StrParams('Steam profile URL')]
type _MapOption = Annotated[str,
                            StrParams('Map name', name='map',
                                      autocomplete_with=_autocomplete_map)]
type _ModeOption = Annotated[str,
                             StrParams('Game mode', name='mode',
                                       choices=[Mode.KZT, Mode.SKZ, Mode.VNL,
                                                Mode.CKZ, Mode.VNL2])]
type _MaybeModeOption = Annotated[str | None,
                                  StrParams('Game mode', name='mode',
                                            choices=[Mode.KZT, Mode.SKZ,
                                                     Mode.VNL, Mode.CKZ,
                                                     Mode.VNL2])]
type _MaybePlayerOption = Annotated[Member | None,
                                    MemberParams('Player', name='player')]
type _TypeOption = Annotated[str,
                             StrParams('Pro or teleport run', name='type',
                                       choices=[Type.PRO, Type.TP, Type.ANY])]
type _MaybeCourseOption = Annotated[str | None, StrParams('Course')]
type _MaybeBonusOption = Annotated[int | None, IntParams('Bonus', min=1)]
type _MaybeAddressOption = Annotated[
    str | None,
    StrParams('Address', name='address',
              autocomplete_with=_autocomplete_address)
]

class _PlayerNotFound(Exception):
    pass

async def _get_player(ctx: _Context, player_member: Member | None=None
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

    # If a player asks for a map impossible for their default mode (and an
    # explicit mode isn't set), try to return the map in a more possible mode
    # so times can still be shown.
    #
    # Note that on CSGO impossible is None for bonuses as we can't easily get
    # map record filters for all bonuses from the API. Automatic fallback
    # therefore isn't possible in this case.
    if mode_name is None and api_map.impossible:
        old_mode = mode
        if api_map.name.startswith('vnl_'):
            mode = Mode.VNL
        elif api_map.name.startswith('skz_'):
            mode = Mode.SKZ
        else:
            mode = {Mode.KZT: Mode.KZT,
                    Mode.SKZ: Mode.KZT,
                    Mode.VNL: Mode.KZT,
                    Mode.CKZ: Mode.VNL2,
                    Mode.VNL2: Mode.CKZ}[mode]
        if mode != old_mode:
            old_api = api
            old_api_map = api_map
            api = api_for_mode(mode)
            api_map = await api.get_map(map_name, mode, course, bonus)
            if api_map.impossible:
                # This is unlikely to be possible, but if falling back
                # produces another impossible map response, we return things
                # in the original mode to avoid confusion around tiers, etc.
                return old_api, old_api_map

    return api, api_map

async def _get_server_map(server: a2s.Server) -> APIMap | None:
    api_map = None
    mode = ({a2s.Game.CSGO: Mode.KZT, a2s.Game.CS2: Mode.CKZ}
            .get(cast('a2s.Game', server.game)))
    if mode is not None:
        with contextlib.suppress(APIMapNotFoundError):
            _, api_map = await _get_map(mode, Mode.KZT, server.map_name)
    return api_map

async def _handle_error(ctx: _Context, exc: Exception) -> None:
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
async def _slash_register(ctx: _Context, profile: _SteamProfileURLOption,
                          mode_name: _ModeOption=Mode.KZT) -> None:
    """Register the user with a given Steam profile and game mode"""
    steam = get_steam()
    try:
        steamid64 = await steam.steamid64_for_profile(profile)
    except SteamValueError:
        await ctx.respond('Invalid Steam profile URL',
                          flags=MessageFlag.EPHEMERAL)
        return

    defaults: dict[str, int | Mode] = {'steamid64': steamid64}
    defaults['mode'] = Mode(mode_name)
    await Player.update_or_create( # pyright: ignore[reportUnknownMemberType]
                                  user_id=ctx.user.id,
                                  server_id=ctx.guild_id,
                                  defaults=defaults)
    await ctx.respond('Registered', flags=MessageFlag.EPHEMERAL)

@slash_command('unregister', 'Delete account settings')
async def _slash_unregister(ctx: _Context) -> None:
    """Unregister the user"""
    player = await _get_player(ctx)
    await player.delete()
    await ctx.respond('Unregistered', flags=MessageFlag.EPHEMERAL)

@slash_command('mode', 'Show or set default game mode')
async def _slash_mode(ctx: _Context, mode_name: _MaybeModeOption=None) -> None:
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
async def _slash_pb(ctx: _Context, map_name: _MapOption,
                    type_name: _TypeOption=Type.ANY,
                    mode_name: _MaybeModeOption=None,
                    course: _MaybeCourseOption=None,
                    bonus: _MaybeBonusOption=None,
                    player_member: _MaybePlayerOption=None) -> None:
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
async def _slash_latest(ctx: _Context, type_name: _TypeOption=Type.ANY,
                        mode_name: _MaybeModeOption=None,
                        player_member: _MaybePlayerOption=None) -> None:
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
async def _slash_map(ctx: _Context, map_name: _MapOption,
                     mode_name: _MaybeModeOption=None,
                     course: _MaybeCourseOption=None,
                     bonus: _MaybeBonusOption=None) -> None:
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
    component = map_component(api_map, wrs)
    await ctx.respond(component=component)

@slash_command('profile', 'Show rank, point total, and point average',
               autodefer=True)
async def _slash_profile(ctx: _Context, mode_name: _MaybeModeOption=None,
                         player_member: _MaybePlayerOption=None) -> None:
    """Look up the user's profile information"""
    player = await _get_player(ctx, player_member)
    mode = player.mode if mode_name is None else Mode(mode_name)
    api = api_for_mode(mode)
    profile = await api.get_profile(player.steamid64, mode)
    component = await profile_component(profile, player, ctx.user)
    await ctx.respond(component=component)

def _split_address(address: str) -> tuple[str, str]:
    parts = address.split(':', 1)
    if len(parts) == 2:
        host, port = parts
    else:
        host, port = address, ''
    if not port:
        port = '27015'
    return host, port

@slash_command('server', "Show server's current map and players",
               autodefer=True)
async def _slash_server(ctx: _Context, address: _MaybeAddressOption=None
                        ) -> None:
    """Look up a server's curent map and players"""
    if address is None:
        db_server = (await Server.filter(server_id=ctx.guild_id,
                                         is_default=True)
                                 .first())
        if db_server is None:
            await ctx.respond('No default server',
                              flags=MessageFlag.EPHEMERAL)
            return
        address = db_server.address

    host, port = _split_address(address)
    if not host or not port.isdigit():
        await ctx.respond('Invalid address',
                          flags=MessageFlag.EPHEMERAL)
        return
    try:
        server = await a2s.query_server(host, int(port))
    except a2s.QueryA2SError:
        _logger.exception('a2s query failed for %s:%s', host, port)
        await ctx.respond("Server query failed",
                          flags=MessageFlag.EPHEMERAL)
        return
    except a2s.QueryConnectionError:
        await ctx.respond("Couldn't connect to server",
                          flags=MessageFlag.EPHEMERAL)
        return
    except a2s.QueryInvalidAddressError:
        await ctx.respond('Invalid address',
                          flags=MessageFlag.EPHEMERAL)
        return

    api_map = await _get_server_map(server)
    component = server_component(server, api_map)
    await ctx.respond(component=component)

async def _refresh_server(client: _Client, db_server: Server) -> None:
    if db_server.channel_id is None:
        return

    message = None
    if db_server.message_id is not None:
        try:
            message = await client.rest.fetch_message(db_server.channel_id,
                                                      db_server.message_id)
        except ForbiddenError:
            _logger.info('server message %s in channel %s forbidden',
                         db_server.channel_id, db_server.message_id)
        except NotFoundError:
            _logger.info('server message %s in channel %s not found',
                         db_server.channel_id, db_server.message_id)

    host, port = _split_address(db_server.address)
    if host and port.isdigit():
        try:
            server = await a2s.query_server(host, int(port))
        except a2s.QueryA2SError:
            _logger.exception('a2s query failed for %s:%s', host, port)
            component = server_failed_component(db_server.address,
                                                'Server query failed')
        except a2s.QueryConnectionError:
            _logger.exception('a2s connect failed for %s:%s', host, port)
            component = server_failed_component(db_server.address,
                                                'Server connection failed')
        except a2s.QueryInvalidAddressError:
            component = server_failed_component(db_server.address,
                                                'Invalid server address')
        else:
            api_map = await _get_server_map(server)
            component = server_component(server, api_map)
    else:
        component = server_failed_component(db_server.address,
                                            'Invalid server address')

    if message is not None:
        await message.edit(component=component)
    else:
        try:
            message = await client.rest.create_message(
                channel=db_server.channel_id, component=component)
        except ForbiddenError:
            _logger.info('creating server message in channel %s forbidden',
                         db_server.channel_id)
        except NotFoundError:
            _logger.info('server message channel %s not found',
                         db_server.channel_id)
        else:
            db_server.message_id = int(message.id)
            await db_server.save(update_fields=['message_id'])

async def refresh_servers(client: _Client) -> None:
    async for db_server in Server.filter(channel_id__isnull=False):
        await _refresh_server(client, db_server)
