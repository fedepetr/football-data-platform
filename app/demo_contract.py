"""The data contract between the platform and the demo app.

Every table the app reads, its schema in data/fdp.duckdb, and the columns the
pages use. The pipeline must keep these columns; tests/test_app.py checks them
against the real database after every build, and demo_data.py checks them when
the app starts, so a renamed column fails loudly instead of breaking a page.
No Streamlit import here: tests can read the contract without the app.
"""

# Table -> (schema in data/fdp.duckdb, columns used by the app).
CONTRACT: dict[str, tuple[str, list[str]]] = {
    "mart_player_profile": (
        "marts",
        [
            "player_key", "season_key", "season_label", "full_name", "age", "nationality",
            "position", "team_name", "competition_key", "appearances", "minutes_played",
            "goals", "assists", "yellow_cards", "red_cards", "market_value_eur", "stats_source",
        ],
    ),
    "mart_similar_players": (
        "marts",
        [
            "player_key", "player_name", "season_key", "position", "comparison_scope",
            "similarity_rank", "similar_player_key", "similar_player_name",
            "similar_competition_key", "distance", "similarity",
        ],
    ),
    "dim_player": ("core", ["player_key", "full_name"]),
    "dim_team": ("core", ["team_key"]),
    "fct_match": (
        "core",
        ["season_key", "competition_key", "match_date", "status", "in_couk", "in_transfermarkt"],
    ),
    "map_player": (
        "core",
        ["player_key", "fdorg_person_id", "tm_player_id", "match_method", "match_score"],
    ),
    "fct_player_valuation": ("core", ["player_key", "valuation_date", "market_value_eur"]),
    "fct_player_appearance": (
        "core",
        [
            "player_key", "season_key", "competition_key", "minutes_played", "goals",
            "assists", "yellow_cards", "red_cards",
        ],
    ),
    "fct_player_season_scoring": (
        "core",
        ["player_key", "season_key", "competition_key", "goals"],
    ),
    "player_matches": (
        "matching",
        ["band", "match_score", "fdorg_player_name", "tm_player_name", "flags"],
    ),
}  # fmt: skip


def missing_columns(available: dict[str, set[str]]) -> dict[str, list[str]]:
    """Contract columns missing from the available tables: {table: [missing columns]}.

    `available` maps each table to the columns it actually has; a missing table
    counts as missing all its columns. An empty result means the contract holds.
    """
    missing = {}
    for table, (_, columns) in CONTRACT.items():
        absent = [column for column in columns if column not in available.get(table, set())]
        if absent:
            missing[table] = absent
    return missing
