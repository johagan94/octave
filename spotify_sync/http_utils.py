"""HTTP helpers shared across clients."""

import logging
import time

import requests

log = logging.getLogger(__name__)


def http_get_with_retry(
    url: str,
    headers: dict,
    params: dict,
    timeout: int = 30,
    max_attempts: int = 5,
    backoff_base: float = 2.0,
) -> requests.Response:
    """GET with exponential backoff on connection errors and 5xx responses.

    Raises on the final failure.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
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
