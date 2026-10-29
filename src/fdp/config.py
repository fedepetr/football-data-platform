"""Pipeline configuration, read from config/pipeline.yaml."""

from pathlib import Path
from typing import Any

import yaml

# Relative to the project root, like the data folder (commands are run from there).
CONFIG_PATH = Path("config/pipeline.yaml")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Read the YAML configuration file and return it as a dictionary."""
    with path.open(encoding="utf-8") as file:
        # safe_load only builds plain data (dicts, lists, strings, numbers):
        # unlike yaml.load, it cannot create arbitrary Python objects from the file.
        return yaml.safe_load(file)
