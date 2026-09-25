import asyncio
import ipaddress
import posixpath
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from little_a2s import AsyncA2S, Error as A2SError

class QueryError(Exception):
    def __init__(self, /, msg: str | None=None, *, query_time: datetime
                 ) -> None:
        super().__init__(*[msg] if msg is not None else [])
        self.query_time: datetime = query_time

class QueryInvalidAddressError(QueryError):
    pass

class QueryTimeoutError(QueryError):
    pass

class QueryA2SError(QueryError):
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
    query_time: datetime

_timeout: int | None = None

def init_a2s(timeout: int | None=None) -> None:
    global _timeout
    if _timeout is None:
        _timeout = timeout

async def _resolve_address(host: str, port: int, query_time: datetime
                           ) -> tuple[str, int]:
    loop = asyncio.get_running_loop()
    try:
        addrinfo = await loop.getaddrinfo(host, port,
                                          type=socket.SOCK_DGRAM)
    except socket.gaierror as e:
        raise QueryInvalidAddressError(query_time=query_time) from e
    if not addrinfo:
        raise QueryInvalidAddressError(query_time=query_time)
    addr = addrinfo[0]
    if len(addr) != 5:
        raise QueryInvalidAddressError(query_time=query_time)
    ip_addr = addr[4][:2][0]
    if not isinstance(ip_addr, str):
        raise QueryInvalidAddressError(query_time=query_time)
    ip = ipaddress.ip_address(ip_addr)
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise QueryInvalidAddressError(query_time=query_time)
    return ip_addr, port

def split_address(address: str) -> tuple[str, str]:
    parts = address.split(':', 1)
    if len(parts) == 2:
        host, port = parts
    else:
        host, port = address, ''
    if not port:
        port = '27015'
    return host, port

async def query_server(host: str, port: int | None, timeout: int | None=None
                       ) -> Server:
    port = port if port is not None else 27015
    timeout = timeout if timeout is not None else _timeout

    query_time = datetime.now(tz=UTC)
    try:
        async with asyncio.timeout(timeout):
            ip, port = await _resolve_address(host, port, query_time)
            a2s = AsyncA2S.from_addr(host, port)
            async with a2s:
                info = await a2s.info()
                player_info = await a2s.players()
    except A2SError as e:
        raise QueryA2SError(query_time=query_time) from e
    except TimeoutError as e:
        raise QueryTimeoutError(query_time=query_time) from e

    players = [Player(p.name, duration=timedelta(seconds=p.duration))
               for p in player_info.players]
    return Server(host=host, ip=ip, port=port, game=info.game, name=info.name,
                  full_map_name=info.map,
                  map_name=posixpath.basename(info.map),
                  player_count=info.players, max_players=info.max_players,
                  players=players, query_time=query_time)
