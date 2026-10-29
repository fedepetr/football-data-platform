"""Ingestion of the football-data.org API (v4) into the raw layer."""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import httpx
from dotenv import load_dotenv

from fdp.config import load_config
from fdp.ingest.common import DATA_DIR, connect, load_raw_json, raw_dir, save_raw

SOURCE = "fdorg"

# All parameters come from config/pipeline.yaml (read once, when the module is imported).
_CONFIG = load_config()
_SOURCE_CONFIG = _CONFIG["sources"][SOURCE]

# football-data.org codes of the competitions in scope, e.g. ("SA", "PL").
COMPETITIONS = tuple(competition[SOURCE] for competition in _CONFIG["competitions"])
# Endpoint name -> {"path": template, "items_key" and "min_items": arrival checks,
# "params": extra query parameters (optional)}.
ENDPOINTS: dict[str, dict[str, Any]] = _SOURCE_CONFIG["endpoints"]

BASE_URL: str = _SOURCE_CONFIG["base_url"]
TIMEOUT_SECONDS: float = _SOURCE_CONFIG["timeout_seconds"]
# Spread requests evenly: 10 requests per minute -> one every 6 seconds.
REQUEST_INTERVAL_SECONDS = 60 / _SOURCE_CONFIG["requests_per_minute"]
MAX_ATTEMPTS: int = _SOURCE_CONFIG["retry"]["max_attempts"]
BACKOFF_BASE_SECONDS: float = _SOURCE_CONFIG["retry"]["backoff_base_seconds"]


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
    return httpx.Client(base_url=BASE_URL, headers={"X-Auth-Token": token}, timeout=TIMEOUT_SECONDS)


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


def check_payload(payload: dict[str, Any], spec: dict[str, Any], label: str) -> None:
    """Arrival checks on one API response, before it is saved or loaded.

    Raises ValueError if the expected list is missing, has fewer items than
    `min_items`, or has as many items as the requested `limit` (truncated data).
    `label` (e.g. "SA scorers 2425") makes the error say which file failed.
    """
    items_key = spec["items_key"]
    # Required structure: the list we load must be there (e.g. "matches").
    items = payload.get(items_key)
    if not isinstance(items, list):
        raise ValueError(f"{label}: key {items_key!r} missing or not a list")

    # Minimum rows: an empty or partial response must not replace good data.
    count = len(items)
    if count < spec["min_items"]:
        raise ValueError(f"{label}: expected at least {spec['min_items']} items, got {count}")

    # Truncation: getting exactly `limit` items means the API probably stopped there.
    limit = spec.get("params", {}).get("limit")
    if limit is not None and count >= limit:
        raise ValueError(
            f"{label}: got {count} items = limit {limit}, data probably truncated; "
            "raise the limit in config/pipeline.yaml"
        )


def ingest_season(
    client: httpx.Client,
    con: duckdb.DuckDBPyConnection,
    competition: str,
    season: str,
    data_dir: Path = DATA_DIR,
) -> list[Path]:
    """Extract, save and load every endpoint of one competition and season.

    For each endpoint: call the API, run the arrival checks, save the response to
    data/raw/fdorg/<season>/<competition>_<endpoint>.json, then load it into
    raw.fdorg_<endpoint>. Returns the paths of the saved files.
    A failed check stops before saving, so the previous good file and rows stay.
    """
    # Validate the season and convert it once, before any API call.
    params = {"season": season_start_year(season)}
    folder = raw_dir(SOURCE, season, data_dir)
    saved = []
    for endpoint, spec in ENDPOINTS.items():
        # "/competitions/{competition}/matches" -> "/competitions/SA/matches".
        path = spec["path"].format(competition=competition)
        # Add endpoint-specific parameters (e.g. limit for scorers), if any.
        payload = get_json(client, path, params | spec.get("params", {}))
        check_payload(payload, spec, f"{competition} {endpoint} {season}")
        # ensure_ascii=False keeps accented names readable in the raw file instead of \u escapes.
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        file_path = save_raw(content, folder / f"{competition}_{endpoint}.json")
        load_raw_json(con, file_path, f"{SOURCE}_{endpoint}", season)
        saved.append(file_path)
    return saved


def main() -> None:
    """Command line entry point: `uv run python -m fdp.ingest.fdorg 2627`."""
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m fdp.ingest.fdorg <season>, e.g. 2627")
    season = sys.argv[1]

    # Read the token from .env; stop with a clear message instead of a KeyError.
    load_dotenv()
    token = os.getenv("FOOTBALL_DATA_ORG_TOKEN")
    if not token:
        raise SystemExit("FOOTBALL_DATA_ORG_TOKEN is missing: copy .env.example to .env and set it")

    # `with` closes the HTTP client and the database even if an error occurs.
    with make_client(token) as client, connect() as con:
        for competition in COMPETITIONS:
            for path in ingest_season(client, con, competition, season):
                print(f"loaded {path}")


# Runs only when the file is executed directly, not when it is imported (tests, Dagster).
if __name__ == "__main__":
    main()
