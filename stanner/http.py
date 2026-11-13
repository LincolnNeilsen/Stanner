import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

_sessions: dict[tuple, requests.Session] = {}


def session(retries: int = 4, backoff: float = 2) -> requests.Session:
    """Shared sessions keyed by retry policy. Sources that throttle by IP (MusicBrainz on shared
    GitHub runners) ask for more patient retries."""
    key = (retries, backoff)
    if key not in _sessions:
        s = requests.Session()
        retry = Retry(
            total=retries,
            backoff_factor=backoff,
            backoff_max=120,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.headers["User-Agent"] = config.USER_AGENT
        _sessions[key] = s
    return _sessions[key]


class RateLimiter:
    """Minimum spacing between calls, per source."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self):
        delta = time.monotonic() - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.monotonic()


def get(url, *, params=None, headers=None, limiter: RateLimiter | None = None, ok404=False,
        retries: int = 4, backoff: float = 2):
    if limiter:
        limiter.wait()
    r = session(retries, backoff).get(url, params=params, headers=headers, timeout=30)
    if ok404 and r.status_code == 404:
        return None
    r.raise_for_status()
    return r
