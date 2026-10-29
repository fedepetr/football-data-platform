import httpx
import pytest

from fdp.ingest import fdorg
from fdp.ingest.fdorg import backoff_seconds, get_json, season_start_year


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


def test_get_json_retries_on_rate_limit(sleeps: list[float]) -> None:
    """A 429 is retried after a backoff wait, then the payload is returned."""
    assert get_json(fake_client([429, 200]), "/matches") == {"status": 200}
    assert sleeps == [6.0, 10.0, 6.0]


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
