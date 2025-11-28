import asyncio
import logging
import sys

from tortoise import run_async

from kzkitty.api import refresh_db_maps
from kzkitty.bot import bot
from kzkitty.models import init_db

logger = logging.getLogger('kzkitty')

async def _refresh() -> None:
    await init_db()
    await refresh_db_maps()

def main(args: list[str]) -> None:
    try:
        import uvloop
    except ImportError:
        pass
    else:
        loop = uvloop.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.info('Installed uvloop event loop')

    if args and args[0] == 'refresh':
        logger.info('Refreshing map database')
        run_async(_refresh())
        return

    bot.run()

if __name__ == '__main__':
    main(sys.argv[1:])
