"""Ingestion of the football-data.org API (v4) into the raw layer."""

import time
from typing import Any

import httpx

BASE_URL = "https://api.football-data.org/v4"
# Free tier allows 10 requests per minute: one request every 6 seconds stays under it.
REQUEST_INTERVAL_SECONDS = 6.0
# Retry policy from the brief: at most 4 attempts, waiting 10, 20, 40 seconds between them.
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 10.0


def season_start_year(season: str) -> int:
    """Convert a project season code into the year expected by the API.

    The project identifies seasons as "2627"; the API wants the start year (2026).
    Raises ValueError on malformed codes, so a typo fails before any API call.
    """
    # Exactly 4 digits: rejects "26-27", "262", " 2627".
    if len(season) != 4 or not season.isdigit():
        raise ValueError(f"Season must be 4 digits like '2627', got {season!r}")
    # The two halves must be consecutive years: rejects calendar years like "2026".
    start, end = int(season[:2]), int(season[2:])
    if end != start + 1:
        raise ValueError(f"Season years must be consecutive like '2627', got {season!r}")
    return 2000 + start


def make_client(token: str) -> httpx.Client:
    """Create an HTTP client with the API base URL, auth header and timeout set once."""
    return httpx.Client(base_url=BASE_URL, headers={"X-Auth-Token": token}, timeout=30.0)


def backoff_seconds(attempt: int) -> float:
    """Return how long to wait after a failed attempt: 10s, 20s, 40s (doubles each time)."""
    return BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)


def get_json(
    client: httpx.Client, path: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call one API endpoint and return the parsed JSON body.

    Respects the rate limit with a fixed pause after every request.
    Retries on 429 (rate limit) and 5xx (server errors) with exponential backoff;
    any other error (e.g. 403 bad token, 404 wrong path) fails immediately.
    Raises httpx.HTTPStatusError when the request does not succeed.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = client.get(path, params=params)
        time.sleep(REQUEST_INTERVAL_SECONDS)

        # Temporary errors are worth retrying, as long as attempts are left.
        retryable = response.status_code == 429 or response.status_code >= 500
        if retryable and attempt < MAX_ATTEMPTS:
            time.sleep(backoff_seconds(attempt))
            continue

        # Success returns the body; a non-retryable error or the last failed
        # attempt raises HTTPStatusError with the status code and URL.
        response.raise_for_status()
        return response.json()
