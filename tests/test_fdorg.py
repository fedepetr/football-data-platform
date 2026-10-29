from pathlib import Path

import duckdb
import httpx
import pytest

from fdp.ingest import fdorg
from fdp.ingest.fdorg import (
    backoff_seconds,
    check_payload,
    get_json,
    ingest_season,
    season_start_year,
)


@pytest.mark.parametrize(
    ("season", "expected"),
    [("2425", 2024), ("2526", 2025), ("2627", 2026)],
)
def test_season_start_year(season: str, expected: int) -> None:
    """Valid season codes are converted to their start year."""
    assert season_start_year(season) == expected


@pytest.mark.parametrize("season", ["2026", "2025", "26-27", "262", " 2627"])
def test_season_start_year_rejects_invalid_codes(season: str) -> None:
    """Malformed codes raise ValueError instead of returning a wrong year."""
    with pytest.raises(ValueError):
        season_start_year(season)


def test_backoff_seconds_doubles() -> None:
    """Waits between retries double at each attempt."""
    assert [backoff_seconds(attempt) for attempt in (1, 2, 3)] == [10.0, 20.0, 40.0]


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace time.sleep with a recorder, so tests run instantly and can check the waits."""
    recorded: list[float] = []
    monkeypatch.setattr(fdorg.time, "sleep", recorded.append)
    return recorded


def fake_client(statuses: list[int]) -> httpx.Client:
    """Client that answers each request with the next status code, without network."""
    remaining = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(remaining)
        return httpx.Response(status, json={"status": status})

    return httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler))


def test_get_json_returns_payload(sleeps: list[float]) -> None:
    """A successful call returns the JSON body and pauses once for the rate limit."""
    assert get_json(fake_client([200]), "/matches") == {"status": 200}
    assert sleeps == [6.0]


def test_get_json_retries_on_rate_limit(
    sleeps: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    """A 429 is retried after a backoff wait (and logged), then the payload is returned."""
    assert get_json(fake_client([429, 200]), "/matches") == {"status": 200}
    assert sleeps == [6.0, 10.0, 6.0]
    assert "/matches returned 429, retrying in 10s (attempt 1/4)" in caplog.text


def test_get_json_gives_up_after_max_attempts(sleeps: list[float]) -> None:
    """Persistent server errors raise after 4 attempts with growing waits."""
    with pytest.raises(httpx.HTTPStatusError):
        get_json(fake_client([503, 503, 503, 503]), "/matches")
    assert sleeps == [6.0, 10.0, 6.0, 20.0, 6.0, 40.0, 6.0]


def test_get_json_does_not_retry_client_errors(sleeps: list[float]) -> None:
    """A 403 (e.g. bad token) fails at the first attempt: retrying would not help."""
    with pytest.raises(httpx.HTTPStatusError):
        get_json(fake_client([403]), "/matches")
    assert sleeps == [6.0]


def test_ingest_season_saves_and_loads_every_endpoint(sleeps: list[float], tmp_path: Path) -> None:
    """One call per endpoint: each response is saved to a raw file and loaded as one row."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        # Answer with a payload that passes the arrival checks of the endpoint.
        endpoint = request.url.path.rsplit("/", 1)[-1]
        items = [{"name": "Rúben"}] * fdorg.ENDPOINTS[endpoint]["min_items"]
        return httpx.Response(200, json={endpoint: items})

    client = httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler))
    con = duckdb.connect()

    saved = ingest_season(client, con, "SA", "2627", data_dir=tmp_path)

    folder = tmp_path / "raw" / "fdorg" / "2627"
    assert saved == [folder / f"SA_{name}.json" for name in ("matches", "teams", "scorers")]
    # The API gets the start year, and scorers get the limit set in the YAML config.
    limit = fdorg.ENDPOINTS["scorers"]["params"]["limit"]
    assert [str(r.url) for r in requests] == [
        "https://test/competitions/SA/matches?season=2026",
        "https://test/competitions/SA/teams?season=2026",
        f"https://test/competitions/SA/scorers?season=2026&limit={limit}",
    ]
    # Accents are written as UTF-8, not as \u escapes.
    assert "Rúben" in saved[0].read_text(encoding="utf-8")
    for table in ("fdorg_matches", "fdorg_teams", "fdorg_scorers"):
        assert con.execute(f"SELECT count(*) FROM raw.{table}").fetchone() == (1,)


# Arrival check spec used below: like the scorers endpoint, with a small limit.
SPEC = {"items_key": "scorers", "min_items": 2, "params": {"limit": 5}}


def test_check_payload_accepts_valid_response() -> None:
    """A list between min_items and the limit passes without errors."""
    check_payload({"scorers": [1, 2, 3]}, SPEC, "SA scorers 2627")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"message": "error"}, "missing"),  # API answered something else
        ({"scorers": None}, "missing"),  # key present but not a list
        ({"scorers": [1]}, "at least 2"),  # fewer rows than min_items
        ({"scorers": [1, 2, 3, 4, 5]}, "truncated"),  # count equal to the limit
    ],
)
def test_check_payload_rejects_bad_responses(payload: dict, message: str) -> None:
    """Each rule raises ValueError naming the failed file and the reason."""
    with pytest.raises(ValueError, match=message) as error:
        check_payload(payload, SPEC, "SA scorers 2627")
    assert "SA scorers 2627" in str(error.value)


def test_ingest_season_failed_check_keeps_previous_data(
    sleeps: list[float], tmp_path: Path
) -> None:
    """An empty response stops the run before saving: no file written, no table touched."""
    client = fake_client([200])  # answers {"status": 200}: no "matches" list
    con = duckdb.connect()

    with pytest.raises(ValueError, match="matches"):
        ingest_season(client, con, "SA", "2627", data_dir=tmp_path)

    assert not (tmp_path / "raw").exists()
    tables = con.execute("SELECT count(*) FROM information_schema.tables").fetchone()
    assert tables == (0,)
