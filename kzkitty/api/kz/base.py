from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from kzkitty.models import Map, Mode, Type

class APIError(Exception):
    pass

class APIConnectionError(APIError):
    pass

class APIMapError(APIError):
    pass

class APIMapNotFoundError(APIMapError):
    pass

class APIMapAmbiguousError(APIMapError):
    def __init__(self, db_maps: list[Map]):
        self.db_maps: list[Map] = db_maps

@dataclass
class APIMap:
    name: str
    mode: Mode
    bonus: int | None
    course: str | None
    tier: int | None
    tier_name: str | None
    pro_tier: int | None
    pro_tier_name: str | None
    max_tier: int | None
    thumbnail: bytes | None
    url: str

@dataclass
class PersonalBest:
    id: int
    steamid64: int
    player_name: str | None
    player_url: str
    map: APIMap
    time: timedelta
    teleports: int
    points: int
    point_scale: int
    place: int | None
    date: datetime

class Rank(StrEnum):
    UNKNOWN = 'Unknown'
    NEW = 'New'
    BEGINNER_MINUS = 'Beginner-'
    BEGINNER = 'Beginner'
    BEGINNER_PLUS = 'Beginner+'
    AMATEUR_MINUS = 'Amateur-'
    AMATEUR = 'Amateur'
    AMATEUR_PLUS = 'Amateur+'
    CASUAL_MINUS = 'Casual-'
    CASUAL = 'Casual'
    CASUAL_PLUS = 'Casual+'
    REGULAR_MINUS = 'Regular-'
    REGULAR = 'Regular'
    REGULAR_PLUS = 'Regular+'
    SKILLED_MINUS = 'Skilled-'
    SKILLED = 'Skilled'
    SKILLED_PLUS = 'Skilled+'
    EXPERT_MINUS = 'Expert-'
    EXPERT = 'Expert'
    EXPERT_PLUS = 'Expert+'
    SEMIPRO = 'Semipro'
    PRO = 'Pro'
    MASTER = 'Master'
    LEGEND = 'Legend'

@dataclass
class Profile:
    name: str | None
    mode: Mode
    rank: Rank
    points: int
    average: int | None
    url: str

class API(ABC):
    def __init__(self, mode: Mode) -> None:
        self.mode: Mode = mode

    @abstractmethod
    def has_tp_wrs(self) -> bool:
        ...

    @abstractmethod
    async def get_map(self, name: str, course: str | None=None,
                      bonus: int | None=None) -> APIMap:
        ...

    @abstractmethod
    async def get_pb(self, steamid64: int, api_map: APIMap,
                     tp_type: Type=Type.ANY) -> PersonalBest | None:
        ...

    @abstractmethod
    async def get_latest(self, steamid64: int, tp_type: Type=Type.ANY
                         ) -> PersonalBest | None:
        ...

    @abstractmethod
    async def get_wrs(self, api_map: APIMap) -> list[PersonalBest]:
        ...

    @abstractmethod
    async def get_profile(self, steamid64: int) -> Profile:
        ...
