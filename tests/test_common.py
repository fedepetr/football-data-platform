from pathlib import Path

from fdp.ingest.common import raw_dir


def test_raw_dir_default_data_folder() -> None:
    """By default raw files live under data/raw/<source>/<season>."""
    assert raw_dir("fdorg", "2627") == Path("data/raw/fdorg/2627")


def test_raw_dir_custom_data_folder() -> None:
    """A custom data folder is used as the root of the path."""
    assert raw_dir("couk", "2425", data_dir=Path("/tmp/x")) == Path("/tmp/x/raw/couk/2425")
