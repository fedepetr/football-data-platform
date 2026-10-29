import csv
from pathlib import Path

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


def test_competition_seed_matches_config() -> None:
    """dbt's competition_map seed lists the same competitions and codes as the YAML config."""
    with Path("dbt/seeds/competition_map.csv").open(encoding="utf-8") as file:
        seed = {
            (row["competition_name"], row["fdorg_code"], row["couk_code"], row["tm_competition_id"])
            for row in csv.DictReader(file)
        }
    config = {
        (c["name"], c["fdorg"], c["couk"], c["transfermarkt"])
        for c in load_config()["competitions"]
    }
    assert seed == config
