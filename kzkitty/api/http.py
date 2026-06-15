from urllib3 import AsyncPoolManager
from urllib3.exceptions import HTTPError

__all__ = ['AsyncPoolManager', 'HTTPError', 'make_http_pool']

def make_http_pool(timeout: int | None=None) -> AsyncPoolManager:
    headers = {'User-Agent': 'kzkitty/0.1'}
    return AsyncPoolManager(headers=headers, timeout=timeout)
