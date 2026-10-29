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


def save_raw(content: bytes, path: Path) -> Path:
    """Write a raw file exactly as received from the source and return its path.

    Works with bytes, so the same function saves JSON (football-data.org) and CSV
    (football-data.co.uk). Creates missing folders and overwrites an existing file,
    so re-running a season replaces its raw files.
    """
    # Same as `mkdir -p`: create data/raw/<source>/<season>/ if needed.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path
