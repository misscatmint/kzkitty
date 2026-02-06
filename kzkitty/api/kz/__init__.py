from kzkitty.api.kz.base import (API, APIConnectionError, APIError, APIMap,
                                 APIMapAmbiguousError, APIMapError,
                                 APIMapNotFoundError, Rank, PersonalBest,
                                 Profile, Type)
from kzkitty.api.kz.csgo import CSGOAPI, refresh_csgo_db_maps
from kzkitty.api.kz.cs2 import CS2API, refresh_cs2_db_maps
from kzkitty.models import Mode

__all__ = ('API', 'APIConnectionError', 'APIError', 'APIMap',
           'APIMapAmbiguousError', 'APIMapError', 'APIMapNotFoundError',
           'Rank', 'PersonalBest', 'Profile', 'Type', 'api_for_mode')

async def refresh_db_maps() -> None:
    await refresh_csgo_db_maps()
    await refresh_cs2_db_maps()

def api_for_mode(mode: Mode) -> API:
    if mode in {Mode.KZT, Mode.SKZ, Mode.VNL}:
        return CSGOAPI(mode)
    else:
        return CS2API(mode)
