"""HTTP helpers shared across clients."""

import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# Shared session with connection pooling for reuse across requests
_SESSION = requests.Session()
_SESSION.headers.setdefault("User-Agent", "octave-sync/3.0")


def get_session() -> requests.Session:
    return _SESSION


def http_get_with_retry(
    url: str,
    headers: dict,
    params: dict,
    timeout: int = 30,
    max_attempts: int = 5,
    backoff_base: float = 2.0,
) -> requests.Response:
    """GET with exponential backoff on connection errors and 5xx responses.
    Also retries on 429 (rate limit) with retry-after handling.

    Raises on the final failure.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            r = _SESSION.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", backoff_base ** attempt))
                log.warning(
                    "HTTP 429 (rate limit) on %s (attempt %d/%d) — waiting %ds",
                    url, attempt, max_attempts, retry_after,
                )
                if attempt < max_attempts:
                    time.sleep(retry_after)
                    continue
            if r.status_code < 500:
                return r
            log.warning(
                "HTTP %d on %s (attempt %d/%d)",
                r.status_code, url, attempt, max_attempts,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            log.warning(
                "Connection error on %s (attempt %d/%d): %s",
                url, attempt, max_attempts, exc,
            )
            if attempt == max_attempts:
                raise
        if attempt < max_attempts:
            sleep = backoff_base ** attempt
            log.info("  Retrying in %.0fs…", sleep)
            time.sleep(sleep)
    r.raise_for_status()
    return r  # type: ignore[return-value]
