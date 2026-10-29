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

from fdp.ingest.common import DATA_DIR, connect, load_raw_json, raw_dir, save_raw

SOURCE = "fdorg"
# Competitions in scope: Serie A and Premier League (football-data.org codes).
COMPETITIONS = ("SA", "PL")
# Endpoint name -> (path template, extra query parameters).
# The name is used for the raw file (SA_matches.json) and table (raw.fdorg_matches).
ENDPOINTS: dict[str, tuple[str, dict[str, Any]]] = {
    "matches": ("/competitions/{competition}/matches", {}),
    "teams": ("/competitions/{competition}/teams", {}),
    # Default limit is 10: raise it to get every player with at least one goal.
    "scorers": ("/competitions/{competition}/scorers", {"limit": 200}),
}

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


def ingest_season(
    client: httpx.Client,
    con: duckdb.DuckDBPyConnection,
    competition: str,
    season: str,
    data_dir: Path = DATA_DIR,
) -> list[Path]:
    """Extract, save and load every endpoint of one competition and season.

    For each endpoint: call the API, save the response to
    data/raw/fdorg/<season>/<competition>_<endpoint>.json, then load it into
    raw.fdorg_<endpoint>. Returns the paths of the saved files.
    """
    # Validate the season and convert it once, before any API call.
    params = {"season": season_start_year(season)}
    folder = raw_dir(SOURCE, season, data_dir)
    saved = []
    for endpoint, (path_template, extra_params) in ENDPOINTS.items():
        payload = get_json(
            client, path_template.format(competition=competition), params | extra_params
        )
        # ensure_ascii=False keeps accented names readable in the raw file instead of \u escapes.
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        path = save_raw(content, folder / f"{competition}_{endpoint}.json")
        load_raw_json(con, path, f"{SOURCE}_{endpoint}", season)
        saved.append(path)
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
