import asyncio

import hikari
from aiocron import crontab

from kzkitty.api.kz import refresh_db_maps
from kzkitty.models import close_db, import_default_players, init_db

class GatewayBot(hikari.GatewayBot):
    async def start(self, *args, **kwargs) -> None:
        await init_db()
        asyncio.create_task(import_default_players())
        asyncio.create_task(refresh_db_maps())
        crontab('0 0 * * *', func=refresh_db_maps)
        await super().start(*args, **kwargs)

    async def close(self) -> None:
        await close_db()
        await super().close()
