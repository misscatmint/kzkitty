import csv
import logging
import os

import hikari
from aiocron import crontab

from kzkitty.api.kz import APIError, refresh_db_maps
from kzkitty.models import Mode, Player, close_db, init_db

logger = logging.getLogger('kzkitty.gateway')

class GatewayBot(hikari.GatewayBot):
    async def start(self, *args, **kwargs) -> None:
        await init_db()

        default_player_file = os.environ.get('KZKITTY_DEFAULT_PLAYERS')
        if default_player_file is not None:
            users = []
            with open(default_player_file, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    user_id = int(row['user_id'])
                    server_id = int(row['server_id'])
                    if not await Player.exists(user_id=user_id,
                                               server_id=server_id):
                        users.append(Player(user_id=user_id,
                                            server_id=server_id,
                                            steamid64=int(row['steamid64']),
                                            mode=Mode(row['mode'])))
            await Player.bulk_create(users)
            if users:
                logger.info('Imported %d players from %s', len(users),
                            default_player_file)

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
