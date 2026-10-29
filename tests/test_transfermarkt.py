import gzip
from pathlib import Path

import duckdb
import httpx
import pytest

from fdp.ingest.common import save_raw
from fdp.ingest.transfermarkt import TABLES, ingest, load_table, table_url

# Small gzip CSV files, one per table, with the required columns of the config.
# Games: one in scope (IT1 2024), one out of competition (ES1), one out of season (2019).
FILES = {
    "competitions": "competition_id\nIT1\n",
    "clubs": "club_id\n5\n",
    "players": "player_id,name,date_of_birth\n1,Rúben Dias,1997-05-14\n",
    "games": "game_id,competition_id,season\n10,IT1,2024\n11,ES1,2024\n12,IT1,2019\n",
    "appearances": "appearance_id,game_id,player_id\na,10,1\nb,11,1\nc,12,1\n",
    "player_valuations": "player_id,date,market_value_in_eur\n1,2026-06-01,1000\n",
    "transfers": "player_id,transfer_date\n1,2025-07-01\n",
}


def save_table(tmp_path: Path, table: str) -> Path:
    """Write one test table as a .csv.gz file, like the downloaded ones."""
    path = tmp_path / f"{table}.csv.gz"
    return save_raw(gzip.compress(FILES[table].encode("utf-8")), path)


def test_table_url() -> None:
    """The URL points to the compressed file of the table."""
    assert table_url("games").endswith("/data/games.csv.gz")


def test_load_table_keeps_only_games_in_scope(tmp_path: Path) -> None:
    """Games outside the competitions or seasons in scope are not loaded."""
    con = duckdb.connect()

    load_table(con, "games", save_table(tmp_path, "games"))

    rows = con.execute("SELECT game_id, _season FROM raw.transfermarkt_games").fetchall()
    assert rows == [("10", "2425")]


def test_load_table_keeps_appearances_of_loaded_games(tmp_path: Path) -> None:
    """Appearances follow the games filter and take the season of their game."""
    con = duckdb.connect()
    load_table(con, "games", save_table(tmp_path, "games"))

    load_table(con, "appearances", save_table(tmp_path, "appearances"))

    rows = con.execute(
        "SELECT appearance_id, _season FROM raw.transfermarkt_appearances"
    ).fetchall()
    assert rows == [("a", "2425")]


def test_load_table_loads_other_tables_in_full(tmp_path: Path) -> None:
    """Tables without a scope keep every row, with an empty season and the source file."""
    con = duckdb.connect()
    path = save_table(tmp_path, "players")

    load_table(con, "players", path)

    rows = con.execute(
        "SELECT name, _season, _source_file FROM raw.transfermarkt_players"
    ).fetchall()
    assert rows == [("Rúben Dias", None, str(path))]


def test_ingest_saves_and_loads_every_table(tmp_path: Path) -> None:
    """Every table of the config is downloaded, saved as received and loaded."""

    def handler(request: httpx.Request) -> httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1].removesuffix(".csv.gz")
        return httpx.Response(200, content=gzip.compress(FILES[table].encode("utf-8")))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    con = duckdb.connect()

    saved = ingest(client, con, data_dir=tmp_path)

    folder = tmp_path / "raw" / "transfermarkt"
    assert saved == [folder / f"{table}.csv.gz" for table in TABLES]
    for table in TABLES:
        assert con.execute(f"SELECT count(*) FROM raw.transfermarkt_{table}").fetchone()[0] >= 1


def test_ingest_failed_check_saves_nothing(tmp_path: Path) -> None:
    """A file without the required columns stops before saving: no file, no table."""
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"wrong\n1\n"))
    )
    con = duckdb.connect()

    with pytest.raises(ValueError, match="missing columns"):
        ingest(client, con, data_dir=tmp_path)

    assert not (tmp_path / "raw").exists()
    assert con.execute("SELECT count(*) FROM information_schema.tables").fetchone() == (0,)
