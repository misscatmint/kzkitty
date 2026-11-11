import asyncio
import logging
import os
import sys

from kzkitty import sidecar
from kzkitty.api.http import HTTPError, make_http_pool
from kzkitty.api.kz import close_api, init_api, refresh_map_db
from kzkitty.bot import run, runrest
from kzkitty.models import (close_db, dump_players, export_default_players,
                            init_db)

_DEFAULT_REST_ADDRESS = '0.0.0.0:8080'
_DEFAULT_SIDECAR_ADDRESS = '0.0.0.0:8081'

_logger = logging.getLogger('kzkitty')

def _address(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(':')
    if not host or not port.isdecimal() or not 0 < int(port) < 65536:
        raise ValueError(f'Expected host:port, got {value!r}')
    return host, int(port)

async def _healthcheck() -> None:
    """Exit with an error unless the REST bot's web server is answering"""
    rest = os.environ.get('KZKITTY_REST')
    if not rest:
        sys.exit('KZKITTY_REST is not set')
    host, port = _address(_DEFAULT_REST_ADDRESS if rest == '1' else rest)
    pool = make_http_pool(timeout=3)
    try:
        # Any HTTP response (hikari answers GET with 405) means it's up.
        await pool.request('GET', f'http://{host}:{port}/', retries=False)
    except HTTPError as e:
        sys.exit(f'REST bot not answering on {host}:{port}: {e}')
    finally:
        await pool.clear()

async def _refresh(db_url: str, api_timeout: int) -> None:
    init_api(timeout=api_timeout)
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
    api_timeout = int(os.environ.get('KZKITTY_API_TIMEOUT', 3))

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
            asyncio.run(_refresh(db_url, api_timeout))
            return
        elif args[0] == 'default':
            asyncio.run(_default(db_url))
            return
        elif args[0] == 'dump':
            asyncio.run(_dump(db_url))
            return
        elif args[0] == 'healthcheck':
            asyncio.run(_healthcheck())
            return

    discord_token = os.environ['KZKITTY_DISCORD_TOKEN']
    if args and args[0] == 'sidecar':
        host, port = _address(os.environ.get('KZKITTY_SIDECAR',
                                             _DEFAULT_SIDECAR_ADDRESS))
        sidecar.run(discord_token, db_url, host, port)
        return

    refresh_db_hours = int(os.environ.get('KZKITTY_REFRESH_DB_HOURS', 24))
    refresh_server_mins = int(os.environ.get('KZKITTY_REFRESH_SERVER_MINS',
                                             1))
    steam_timeout = int(os.environ.get('KZKITTY_STEAM_TIMEOUT', 5))
    a2s_timeout = int(os.environ.get('KZKITTY_A2S_TIMEOUT', 5))
    public_url = os.environ.get('KZKITTY_URL', '').rstrip('/') or None
    rest = os.environ.get('KZKITTY_REST')
    if rest:
        host, port = _address(_DEFAULT_REST_ADDRESS if rest == '1' else rest)
        runrest(host, port, discord_token, db_url, refresh_db_hours,
                refresh_server_mins, api_timeout, steam_timeout, a2s_timeout,
                public_url)
    else:
        run(discord_token, db_url, refresh_db_hours, refresh_server_mins,
            api_timeout, steam_timeout, a2s_timeout, public_url)

if __name__ == '__main__':
    main(sys.argv[1:])
