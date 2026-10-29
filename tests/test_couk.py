from pathlib import Path

import duckdb
import httpx
import pytest

from fdp.ingest.couk import check_csv, csv_url, ingest_season

# Minimal valid file: BOM, the required columns and one match, as on the real site.
VALID_CSV = "﻿Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nI1,22/08/2026,Udinese,Como,1,1,D\n"


def test_csv_url() -> None:
    """The URL follows the site's <season>/<competition>.csv layout."""
    assert csv_url("I1", "2627") == "https://www.football-data.co.uk/mmz4281/2627/I1.csv"


def test_check_csv_accepts_valid_file() -> None:
    """A file with the required columns and at least one match passes."""
    check_csv(VALID_CSV.encode("utf-8"), "I1 2627")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "empty"),  # nothing downloaded
        ("Div,Date,HomeTeam\nI1,22/08/2026,Udinese\n", "missing columns"),  # columns gone
        ("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n", "at least 1"),  # header only
        ("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n,,,,,,\n", "at least 1"),  # padding line
    ],
)
def test_check_csv_rejects_bad_files(content: str, message: str) -> None:
    """Each rule raises ValueError naming the failed file and the reason."""
    with pytest.raises(ValueError, match=message) as error:
        check_csv(content.encode("utf-8"), "I1 2627")
    assert "I1 2627" in str(error.value)


def test_ingest_season_saves_exact_bytes_and_loads_rows(tmp_path: Path) -> None:
    """The file is saved byte for byte (BOM included) and its matches land in raw."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=VALID_CSV.encode("utf-8"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    con = duckdb.connect()

    path = ingest_season(client, con, "I1", "2627", data_dir=tmp_path)

    assert str(requests[0].url) == "https://www.football-data.co.uk/mmz4281/2627/I1.csv"
    assert path == tmp_path / "raw" / "couk" / "2627" / "I1.csv"
    assert path.read_bytes() == VALID_CSV.encode("utf-8")
    rows = con.execute("SELECT Div, HomeTeam, _season FROM raw.couk_matches").fetchall()
    assert rows == [("I1", "Udinese", "2627")]


def test_ingest_season_failed_check_saves_nothing(tmp_path: Path) -> None:
    """A broken download stops before saving: no file, no table."""
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    con = duckdb.connect()

    with pytest.raises(ValueError, match="empty"):
        ingest_season(client, con, "I1", "2627", data_dir=tmp_path)

    assert not (tmp_path / "raw").exists()
    assert con.execute("SELECT count(*) FROM information_schema.tables").fetchone() == (0,)
