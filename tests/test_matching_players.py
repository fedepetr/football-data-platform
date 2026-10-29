import pytest

from fdp.matching.players import best_candidate, classify, normalize_name, similarity


@pytest.mark.parametrize(
    ("name", "normalized"),
    [
        ("Lautaro Martínez", "lautaro martinez"),  # accents removed
        ("Martínez Lautaro", "lautaro martinez"),  # word order does not matter
        ("N'Golo Kanté", "kante ngolo"),  # apostrophes removed, not splitting words
        ("Abdoulaye N’Diaye", "abdoulaye ndiaye"),  # typographic apostrophe too
        ("Rak-Sakyi Samuel", "rak sakyi samuel"),  # other punctuation splits words
    ],
)
def test_normalize_name(name: str, normalized: str) -> None:
    """Lowercase, no accents or punctuation, words in alphabetical order."""
    assert normalize_name(name) == normalized


def test_similarity_counts_a_swap_as_one_edit() -> None:
    """Damerau-Levenshtein: two swapped letters are a single edit."""
    score, method, _ = similarity("Lautaro Martinez", "Lautaro Martniez")
    assert method == "damerau_levenshtein"
    assert score == pytest.approx(93.75)


def test_similarity_ignores_extra_words() -> None:
    """token_set_ratio: a middle name present in one source only does not count."""
    score, method, _ = similarity("Lautaro Martínez", "Lautaro Javier Martínez")
    assert (score, method) == (100.0, "token_set")


def test_similarity_identical_after_normalization() -> None:
    """Accents and word order removed: the names are equal."""
    assert similarity("Martínez Lautaro", "Lautaro Martinez")[0] == 100.0


@pytest.mark.parametrize(
    ("score", "band"),
    [(100, "auto"), (92.1, "auto"), (92, "review"), (80, "review"), (79.9, "rejected")],
)
def test_classify_uses_brief_thresholds(score: float, band: str) -> None:
    """Above 92 automatic, 80 to 92 review, below 80 rejected."""
    assert classify(score) == band


def test_best_candidate_picks_the_closest_name() -> None:
    """Among players born the same day, the closest name wins; LCS agrees."""
    candidates = {1: "Lautaro Martínez", 2: "Lautaro Acosta", 3: "Simone Inzaghi"}
    match = best_candidate("Martinez Lautaro", candidates)
    assert match is not None
    assert (match.tm_player_id, match.band, match.lcs_agrees) == (1, "auto", True)


def test_best_candidate_rejects_strangers() -> None:
    """A youth player missing from Transfermarkt does not match an unrelated name."""
    match = best_candidate("Simone Ascione", {7: "Weliton"})
    assert match is not None and match.band == "rejected"


def test_best_candidate_without_candidates() -> None:
    """No player with the same birth date (or team): nothing to match."""
    assert best_candidate("Simone Ascione", {}) is None


def test_contained_name_does_not_win_a_tie() -> None:
    """token_set_ratio gives 100 to "Federico" too: fewer edits break the tie,
    and the LCS tie with the contained name is not a disagreement."""
    match = best_candidate("Federico Dimarco", {1: "Federico Dimarco", 2: "Federico"})
    assert match is not None
    assert (match.tm_player_id, match.lcs_agrees) == (1, True)
