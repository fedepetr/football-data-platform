from pathlib import Path

from fdp.ingest.common import raw_dir, save_raw


def test_raw_dir_default_data_folder() -> None:
    """By default raw files live under data/raw/<source>/<season>."""
    assert raw_dir("fdorg", "2627") == Path("data/raw/fdorg/2627")


def test_raw_dir_custom_data_folder() -> None:
    """A custom data folder is used as the root of the path."""
    assert raw_dir("couk", "2425", data_dir=Path("/tmp/x")) == Path("/tmp/x/raw/couk/2425")


def test_save_raw_creates_missing_folders(tmp_path: Path) -> None:
    """Saving into a folder that does not exist creates it and writes the content."""
    path = raw_dir("fdorg", "2627", data_dir=tmp_path) / "SA_matches.json"

    written = save_raw(b'{"matches": []}', path)

    assert written == path
    assert path.read_bytes() == b'{"matches": []}'


def test_save_raw_overwrites_existing_file(tmp_path: Path) -> None:
    """Saving twice to the same path keeps only the latest content."""
    path = tmp_path / "SA_matches.json"

    save_raw(b"old", path)
    save_raw(b"new", path)

    assert path.read_bytes() == b"new"
