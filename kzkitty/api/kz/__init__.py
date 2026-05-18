from kzkitty.api.kz.base import (API, APIConnectionError, APIError, APIMap,
                                 APIMapAmbiguousError, APIMapError,
                                 APIMapNotFoundError, APIUnitializedError,
                                 Rank, PersonalBest, Profile)
from kzkitty.api.kz.csgo import CSGOAPI, refresh_csgo_db_maps
from kzkitty.api.kz.cs2 import CS2API, refresh_cs2_db_maps
from kzkitty.models import Mode

__all__ = ['API', 'APIConnectionError', 'APIError', 'APIMap',
           'APIMapAmbiguousError', 'APIMapError', 'APIMapNotFoundError',
           'Rank', 'PersonalBest', 'Profile', 'api_for_mode']

_csgo_api: CSGOAPI | None = None
_cs2_api: CS2API | None = None

async def refresh_db_maps() -> None:
    await refresh_csgo_db_maps()
    await refresh_cs2_db_maps()

async def init_api(timeout: int | None=None) -> None:
    global _csgo_api, _cs2_api
    if _csgo_api is None:
        _csgo_api = CSGOAPI(timeout=timeout)
    if _cs2_api is None:
        _cs2_api = CS2API(timeout=timeout)

async def close_api() -> None:
    global _csgo_api, _cs2_api
    if _csgo_api is not None:
        await _csgo_api.close()
        _csgo_api = None
    if _cs2_api is not None:
        await _cs2_api.close()
        _csw_api = None

def api_for_mode(mode: Mode) -> API:
    if _csgo_api is None or _cs2_api is None:
        raise APIUnitializedError
    if mode in {Mode.KZT, Mode.SKZ, Mode.VNL}:
        return _csgo_api
    else:
        return _cs2_api
