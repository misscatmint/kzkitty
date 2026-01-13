import asyncio
import logging
import sys

from tortoise import run_async

from kzkitty.api.kz import refresh_db_maps
from kzkitty.models import dump_players, export_default_players, init_db

logger = logging.getLogger('kzkitty')

async def _refresh() -> None:
    await init_db()
    await refresh_db_maps()

async def _default() -> None:
    await init_db()
    await export_default_players()

async def _dump() -> None:
    await init_db()
    await dump_players()

def main(args: list[str]) -> None:
    try:
        import uvloop
    except ImportError:
        loop = asyncio.new_event_loop()
    else:
        loop = uvloop.new_event_loop()
        logger.info('Installing uvloop event loop')
    asyncio.set_event_loop(loop)

    if args:
        if args[0] == 'refresh':
            logging.basicConfig(level=logging.INFO)
            logger.info('Refreshing map database')
            run_async(_refresh())
            return
        elif args[0] == 'default':
            run_async(_default())
            return
        elif args[0] == 'dump':
            run_async(_dump())
            return

    from kzkitty.bot import bot
    bot.run()

if __name__ == '__main__':
    main(sys.argv[1:])
