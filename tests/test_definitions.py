import dagster as dg

from fdp.definitions import SEASONS, couk_raw, defs, fdorg_raw, transfermarkt_raw


def test_definitions_load() -> None:
    """Dagster can load the definitions (the same check `dagster dev` runs at startup)."""
    dg.Definitions.validate_loadable(defs)


def test_asset_keys_match_raw_tables() -> None:
    """Asset keys are ["raw", "<table>"], the DuckDB names dbt sources will use."""
    keys = {
        key.to_user_string()
        for assets in (fdorg_raw, couk_raw, transfermarkt_raw)
        for key in assets.keys
    }
    assert {"raw/fdorg_matches", "raw/couk_matches", "raw/transfermarkt_games"} <= keys
    assert len(keys) == 3 + 1 + 7


def test_season_partitions_come_from_config() -> None:
    """Partitioned assets use the seasons of config/pipeline.yaml; Transfermarkt has none."""
    assert SEASONS.get_partition_keys() == ["2425", "2526", "2627"]
    assert fdorg_raw.partitions_def == SEASONS
    assert couk_raw.partitions_def == SEASONS
    assert transfermarkt_raw.partitions_def is None
