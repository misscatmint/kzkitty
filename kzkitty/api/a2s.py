import asyncio
import ipaddress
import posixpath
import socket
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from little_a2s import AsyncA2S, Error as A2SError

class QueryError(Exception):
    pass

class QueryConnectionError(Exception):
    pass

class QueryA2SError(Exception):
    pass

class Game(StrEnum):
    CSGO = 'Counter-Strike: Global Offensive'
    CS2 = 'Counter-Strike 2'

@dataclass
class Player:
    name: str
    duration: timedelta

@dataclass
class Server:
    host: str
    ip: str
    port: int
    game: str
    name: str
    full_map_name: str
    map_name: str
    player_count: int
    max_players: int
    players: list[Player]

_timeout: int | None = None

def init_a2s(timeout: int | None=None) -> None:
    global _timeout
    if _timeout is None:
        _timeout = timeout

async def _resolve_address(host: str, port: int) -> tuple[str, int]:
    loop = asyncio.get_running_loop()
    try:
        addrinfo = await loop.getaddrinfo(host, port,
                                          type=socket.SOCK_DGRAM)
    except socket.gaierror as e:
        raise ValueError('Invalid address') from e
    if not addrinfo:
        raise ValueError('Invalid address')
    addr = addrinfo[0]
    if len(addr) != 5:
        raise ValueError('Invalid address')
    ip_addr = addr[4][:2][0]
    if not isinstance(ip_addr, str):
        raise ValueError('Invalid address')
    ip = ipaddress.ip_address(ip_addr)
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise ValueError('Invalid address')
    return ip_addr, port

async def query_server(host: str, port: int | None, timeout: int | None=None
                       ) -> Server:
    port = port if port is not None else 27015
    timeout = timeout if timeout is not None else _timeout

    try:
        async with asyncio.timeout(timeout):
            ip, port = await _resolve_address(host, port)
            a2s = AsyncA2S.from_addr(host, port)
            async with a2s:
                info = await a2s.info()
                player_info = await a2s.players()
    except A2SError as e:
        raise QueryA2SError from e
    except TimeoutError as e:
        raise QueryConnectionError from e

    players = [Player(p.name, duration=timedelta(seconds=p.duration))
               for p in player_info.players]
    return Server(host=host, ip=ip, port=port, game=info.game, name=info.name,
                  full_map_name=info.map,
                  map_name=posixpath.basename(info.map),
                  player_count=info.players, max_players=info.max_players,
                  players=players)
