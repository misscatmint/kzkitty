import asyncio
import logging
import os
import sys

from kzkitty.api.kz import close_api, init_api, refresh_map_db
from kzkitty.bot import run, runrest
from kzkitty.models import (close_db, dump_players, export_default_players,
                            init_db)

_logger = logging.getLogger('kzkitty')

async def _refresh(db_url: str) -> None:
    await init_api()
    await init_db(db_url)
    try:
        await refresh_map_db()
    finally:
        await close_api()
        await close_db()

async def _default(db_url: str) -> None:
    await init_db(db_url)
    try:
        await export_default_players()
    finally:
        await close_db()

async def _dump(db_url: str) -> None:
    await init_db(db_url)
    try:
        await dump_players()
    finally:
        await close_db()

def main(args: list[str]) -> None:
    db_url = os.environ['KZKITTY_DB']

    try:
        import uvloop
    except ImportError:
        loop = asyncio.new_event_loop()
    else:
        loop = uvloop.new_event_loop()
        _logger.info('Installing uvloop event loop')
    asyncio.set_event_loop(loop)

    if args:
        if args[0] == 'refresh':
            logging.basicConfig(level=logging.INFO)
            asyncio.run(_refresh(db_url))
            return
        elif args[0] == 'default':
            asyncio.run(_default(db_url))
            return
        elif args[0] == 'dump':
            asyncio.run(_dump(db_url))
            return

    discord_token = os.environ['KZKITTY_DISCORD_TOKEN']
    refresh_db_hours = int(os.environ.get('KZKITTY_REFRESH_DB_HOURS', 24))
    api_timeout = int(os.environ.get('KZKITTY_API_TIMEOUT', 15))
    steam_timeout = int(os.environ.get('KZKITTY_STEAM_TIMEOUT', 5))
    rest = os.environ.get('KZKITTY_REST')
    if rest:
        host, port = rest.split(':', 1)
        runrest(host, int(port), discord_token, db_url, refresh_db_hours,
                api_timeout, steam_timeout)
    else:
        run(discord_token, db_url, refresh_db_hours, api_timeout,
            steam_timeout)

if __name__ == '__main__':
    main(sys.argv[1:])
