import csv
import logging
import os

import hikari
from aiocron import crontab

from kzkitty.api import APIError, refresh_db_maps
from kzkitty.models import Mode, Player, close_db, init_db

logger = logging.getLogger('kzkitty.gateway')

class GatewayBot(hikari.GatewayBot):
    async def start(self, *args, **kwargs) -> None:
        await init_db()

        initial_user_file = os.environ.get('KZKITTY_INITIAL_PLAYERS')
        if initial_user_file is not None:
            if await Player.all().count() == 0:
                users = []
                with open(initial_user_file, newline='') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        users.append(Player(id=int(row['id']),
                                            steamid64=int(row['steamid64']),
                                            mode=Mode(row['mode'])))
                await Player.bulk_create(users)
                logger.info('Imported %d users from %s', len(users),
                            initial_user_file)

        try:
            new, updated = await refresh_db_maps()
        except APIError:
            logger.exception('Failed to refresh map database')
        else:
            logger.info('Refreshed map database (%d new, %d updated)',
                        new, updated)

        crontab('0 0 * * *', func=refresh_db_maps)

        await super().start(*args, **kwargs)

    async def close(self) -> None:
        await close_db()
        await super().close()
