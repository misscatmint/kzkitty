"""Presence and connect links for running alongside the REST bot"""

import asyncio
import logging

from aiohttp import web
from hikari import GatewayBot, Intents, StoppingEvent

from kzkitty.api.a2s import QueryServer, QueryServerDataError
from kzkitty.models import Server, close_db, init_db

_logger = logging.getLogger('kzkitty.sidecar')

async def _connect(request: web.Request) -> web.Response:
    """Redirect to the Steam URL for joining a server"""
    db_server = await Server.get_or_none(id=int(request.match_info['id']))
    if db_server is None or db_server.last_query is None:
        raise web.HTTPNotFound
    try:
        server = QueryServer.from_dict(db_server.last_query)
    except QueryServerDataError:
        raise web.HTTPNotFound from None
    url = server.connect_url()
    if url is None:
        raise web.HTTPNotFound
    raise web.HTTPFound(url)

def run(discord_token: str, db_url: str, host: str, port: int) -> None:
    """Show the bot as online and serve connect links"""
    bot = GatewayBot(discord_token, intents=Intents.NONE, banner=None,
                     suppress_optimization_warning=True)
    app = web.Application()
    app.add_routes([web.get(r'/connect/{id:\d+}', _connect)])
    runner = web.AppRunner(app)

    # Set up before the bot starts so that a failure here exits the process
    # (hikari only logs exceptions raised in its event listeners).
    async def start() -> None:
        await init_db(db_url)
        try:
            await runner.setup()
            await web.TCPSite(runner, host, port).start()
        except BaseException:
            # The open database would otherwise keep the process from exiting.
            await close_db()
            raise
        _logger.info('Serving connect links on %s:%d', host, port)
    asyncio.get_event_loop().run_until_complete(start())

    async def stopping(_: StoppingEvent) -> None:
        await runner.cleanup()
        await close_db()
    bot.event_manager.subscribe(StoppingEvent, stopping)

    bot.run(check_for_updates=False)
