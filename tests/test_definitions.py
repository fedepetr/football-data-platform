import dagster as dg

from fdp.definitions import (
    SEASONS,
    couk_raw,
    defs,
    fdorg_raw,
    transfermarkt_raw,
    weekly_current_season,
)


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


def test_dbt_staging_models_depend_on_raw_assets() -> None:
    """dagster-dbt links each dbt source to the raw asset with the same name."""
    graph = defs.resolve_asset_graph()
    parents = graph.get(dg.AssetKey(["staging", "stg_couk__matches"])).parent_keys
    assert parents == {dg.AssetKey(["raw", "couk_matches"])}


def test_player_matching_sits_between_staging_and_core() -> None:
    """The Python reconciliation reads staging and map_team; dbt map_player reads it."""
    graph = defs.resolve_asset_graph()
    matches = dg.AssetKey(["matching", "player_matches"])
    assert dg.AssetKey(["core", "map_team"]) in graph.get(matches).parent_keys
    assert graph.get(dg.AssetKey(["core", "map_player"])).parent_keys == {matches}


def test_player_match_rate_check_is_blocking() -> None:
    """The match-rate check exists on the matches and blocks downstream on failure."""
    check = dg.AssetCheckKey(dg.AssetKey(["matching", "player_matches"]), "player_match_rate")
    assert check in defs.resolve_asset_graph().asset_check_keys
    assert defs.resolve_asset_graph().get_check_spec(check).blocking


def test_weekly_job_refreshes_seasonal_sources_and_downstream() -> None:
    """The weekly job covers fdorg, couk and everything after them, not Transfermarkt."""
    job = defs.resolve_job_def("weekly_refresh")
    keys = {key.to_user_string() for key in job.asset_layer.selected_asset_keys}
    assert {"raw/fdorg_matches", "raw/couk_matches", "matching/player_matches"} <= keys
    assert "marts/mart_player_profile" in keys
    assert not any(key.startswith("raw/transfermarkt") for key in keys)


def test_weekly_schedule_targets_the_current_season() -> None:
    """The schedule requests only the partition of the season in progress."""
    schedule = defs.resolve_schedule_def("weekly_current_season")
    # A decorated schedule can be called directly: it returns its run request.
    assert weekly_current_season().partition_key == "2627"
    assert schedule.cron_schedule == "0 6 * * 2"
    assert schedule.job_name == "weekly_refresh"
