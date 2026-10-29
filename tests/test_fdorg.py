import pytest

from fdp.ingest.fdorg import season_start_year


@pytest.mark.parametrize(
    ("season", "expected"),
    [("2425", 2024), ("2526", 2025), ("2627", 2026)],
)
def test_season_start_year(season: str, expected: int) -> None:
    assert season_start_year(season) == expected


@pytest.mark.parametrize("season", ["2026", "2025", "26-27", "262", " 2627"])
def test_season_start_year_rejects_invalid_codes(season: str) -> None:
    with pytest.raises(ValueError):
        season_start_year(season)
