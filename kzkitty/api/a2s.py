import asyncio
import ipaddress
import posixpath
import re
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum

from little_a2s import AsyncA2S, Error as A2SError
from pydantic import JsonValue, TypeAdapter, ValidationError

type QueryServerData = dict[str, JsonValue]

_HOSTNAME = re.compile(r'[A-Za-z0-9.-]+')

class QueryError(Exception):
    pass

class QueryInvalidAddressError(QueryError):
    pass

class QueryTimeoutError(QueryError):
    pass

class QueryA2SError(QueryError):
    pass

class QueryUninitializedError(QueryError):
    pass

class QueryServerDataError(Exception):
    pass

class QueryGame(StrEnum):
    CSGO = 'Counter-Strike: Global Offensive'
    CS2 = 'Counter-Strike 2'

class QueryAppID(IntEnum):
    CS2 = 730
    CSGO = 4465480

@dataclass
class QueryPlayer:
    name: str
    duration: timedelta

@dataclass
class QueryServer:
    host: str
    ip: str
    port: int
    app_id: int
    game: str
    name: str
    full_map_name: str
    map_name: str
    player_count: int
    max_players: int
    players: list[QueryPlayer]
    queried_at: datetime

    def connect_url(self) -> str | None:
        if not (self.app_id == QueryAppID.CSGO or
                (self.app_id == QueryAppID.CS2 and
                 self.game == QueryGame.CS2)):
            return None
        # The host ends up in a console command, so only allow host characters.
        if _HOSTNAME.fullmatch(self.host) is None:
            return None
        return f'steam://run/{self.app_id}//+connect%20{self.host}:{self.port}'

    def to_dict(self) -> QueryServerData:
        data: QueryServerData = _ServerAdapter.dump_python(self, mode='json')
        return data

    @classmethod
    def from_dict(cls, data: QueryServerData) -> QueryServer:
        try:
            return _ServerAdapter.validate_python(data)
        except ValidationError as e:
            raise QueryServerDataError from e

_ServerAdapter = TypeAdapter(QueryServer)

async def _resolve_address(host: str, port: int) -> tuple[str, int]:
    loop = asyncio.get_running_loop()
    try:
        addrinfo = await loop.getaddrinfo(host, port, family=socket.AF_INET,
                                          type=socket.SOCK_DGRAM)
    except socket.gaierror as e:
        raise QueryInvalidAddressError from e
    if not addrinfo:
        raise QueryInvalidAddressError
    addr = addrinfo[0]
    if len(addr) != 5:
        raise QueryInvalidAddressError
    ip_addr = addr[4][:2][0]
    if not isinstance(ip_addr, str):
        raise QueryInvalidAddressError
    ip = ipaddress.ip_address(ip_addr)
    if not ip.is_global:
        raise QueryInvalidAddressError
    return ip_addr, port

def parse_address(address: str) -> tuple[str, int]:
    if address.startswith('['):
        raise QueryInvalidAddressError
    parts = address.split(':', 1)
    if len(parts) == 2:
        host, portstr = parts
        portstr = portstr or '27015'
        if not portstr.isascii() or not portstr.isdecimal():
            raise QueryInvalidAddressError
        port = int(portstr)
    else:
        host, port = address, 27015
    if not host:
        raise QueryInvalidAddressError
    return host, port

class A2S:
    def __init__(self, timeout: float | None=None) -> None:
        self._client: AsyncA2S | None = None
        self._lock = asyncio.Lock()
        self.timeout = timeout

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.close()
                self._client = None

    async def _get_client(self) -> AsyncA2S:
        async with self._lock:
            if self._client is None or self._client.transport.is_closing():
                client = AsyncA2S.from_ipv4()
                await client.start()
                self._client = client
            return self._client

    async def query_server(self, host: str, port: int | None) -> QueryServer:
        port = port if port is not None else 27015
        if port < 1024 or port > 65535:
            raise QueryInvalidAddressError

        try:
            async with asyncio.timeout(self.timeout):
                ip, port = await _resolve_address(host, port)
                client = await self._get_client()
                info = await client.info((ip, port))
                player_info = await client.players((ip, port))
        except A2SError as e:
            raise QueryA2SError from e
        except TimeoutError as e:
            raise QueryTimeoutError from e

        players = [QueryPlayer(p.name,
                               duration=timedelta(seconds=p.duration))
                   for p in player_info.players]
        return QueryServer(host=host, ip=ip, port=port, app_id=info.id,
                           game=info.game, name=info.name,
                           full_map_name=info.map,
                           map_name=posixpath.basename(info.map),
                           player_count=info.players,
                           max_players=info.max_players, players=players,
                           queried_at=datetime.now(tz=UTC))

_a2s: A2S | None = None

def init_a2s(timeout: float | None=None) -> None:
    global _a2s
    if _a2s is None:
        _a2s = A2S(timeout=timeout)

async def close_a2s() -> None:
    global _a2s
    if _a2s is not None:
        await _a2s.close()
        _a2s = None

def get_a2s() -> A2S:
    if _a2s is None:
        raise QueryUninitializedError
    return _a2s
