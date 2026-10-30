"""Documentation checks on the dbt project, read from its manifest.

Every model and seed must state its grain and key, and every source table must be
described, so a reader of the dbt docs always knows what one row is and which
columns identify it. Needs dbt/target/manifest.json (`dbt parse`, run by the CI).
"""

import json
from pathlib import Path

import pytest

MANIFEST = Path("dbt/target/manifest.json")
pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="run `dbt parse` first")


def nodes(resource_types: tuple[str, ...]) -> list[dict]:
    """Nodes and sources of the manifest with the given resource types."""
    manifest = json.loads(MANIFEST.read_text())
    everything = [*manifest["nodes"].values(), *manifest["sources"].values()]
    return [node for node in everything if node["resource_type"] in resource_types]


def test_every_model_and_seed_states_grain_and_key() -> None:
    """Descriptions start with **Grain:** ... **Key:** ... (one row per what, unique by what)."""
    missing = [
        node["name"]
        for node in nodes(("model", "seed"))
        if "**Grain:**" not in node["description"] or "**Key:**" not in node["description"]
    ]
    assert missing == []


def test_every_source_table_is_described() -> None:
    """Raw tables are documented too, with their grain."""
    missing = [
        node["name"] for node in nodes(("source",)) if "**Grain:**" not in node["description"]
    ]
    assert missing == []
