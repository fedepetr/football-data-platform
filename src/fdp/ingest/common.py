"""Helpers shared by all ingestion pipelines."""

from pathlib import Path

# Root of the local data folder, relative to the project root (where commands are run).
DATA_DIR = Path("data")


def raw_dir(source: str, season: str, data_dir: Path = DATA_DIR) -> Path:
    """Return the folder for raw files of one source and season.

    Example: raw_dir("fdorg", "2627") -> data/raw/fdorg/2627.
    The folder is not created here: the writer creates it when saving a file.
    `data_dir` can be overridden in tests to avoid touching the real data folder.
    """
    return data_dir / "raw" / source / season
