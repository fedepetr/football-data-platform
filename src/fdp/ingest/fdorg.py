"""Ingestion of the football-data.org API (v4) into the raw layer."""


def season_start_year(season: str) -> int:
    """Convert a project season code into the year expected by the API.

    The project identifies seasons as "2627"; the API wants the start year (2026).
    Raises ValueError on malformed codes, so a typo fails before any API call.
    """
    # Exactly 4 digits: rejects "26-27", "262", " 2627".
    if len(season) != 4 or not season.isdigit():
        raise ValueError(f"Season must be 4 digits like '2627', got {season!r}")
    # The two halves must be consecutive years: rejects calendar years like "2026".
    start, end = int(season[:2]), int(season[2:])
    if end != start + 1:
        raise ValueError(f"Season years must be consecutive like '2627', got {season!r}")
    return 2000 + start
