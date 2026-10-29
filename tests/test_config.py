from fdp.config import load_config
from fdp.ingest.fdorg import season_start_year


def test_seasons_are_valid_codes() -> None:
    """Seasons are strings (not numbers) and valid codes; the current one is in scope."""
    config = load_config()

    for season in config["seasons"]:
        assert isinstance(season, str)
        season_start_year(season)  # raises ValueError on a malformed code
    assert config["current_season"] in config["seasons"]


def test_competitions_have_a_code_for_each_source() -> None:
    """Every competition maps to its code in each source."""
    for competition in load_config()["competitions"]:
        assert competition["fdorg"]
        assert competition["couk"]
        assert competition["transfermarkt"]
