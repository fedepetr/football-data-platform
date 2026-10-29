import pytest

from fdp.matching.teams import (
    best_match,
    extra_letters,
    find_collisions,
    find_not_mutual,
    lcs_length,
    match_names,
    name_score,
    normalize_words,
)

# A few real Serie A candidates (football-data.org id -> official name).
SERIE_A = {
    98: "AC Milan",
    99: "ACF Fiorentina",
    108: "FC Internazionale Milano",
    110: "SS Lazio",
}


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("Atlético Madrid", ["atletico", "madrid"]),  # accents removed
        ("Nott'm Forest", ["nott", "m", "forest"]),  # punctuation splits words
        ("Como 1907", ["como", "1907"]),  # digits kept
    ],
)
def test_normalize_words(name: str, words: list[str]) -> None:
    """Names become lowercase ASCII words."""
    assert normalize_words(name) == words


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("inter", "internazionale", 5),  # all letters, in order
        ("inter", "fiorentina", 3),  # same letters, wrong order: only 3 in sequence
        ("roma", "amor", 1),  # an anagram is not a match
        ("man", "manchester", 3),  # an abbreviation is fully contained
    ],
)
def test_lcs_length(a: str, b: str, expected: int) -> None:
    """Letters are counted only when they appear in the same order."""
    assert lcs_length(a, b) == expected


def test_name_score_handles_abbreviations() -> None:
    """Dividing by the short word: "man" in "manchester" counts in full."""
    assert name_score("Man City", "Manchester City FC") == 1.0
    assert name_score("Man City", "Manchester City FC") > name_score("Man City", "Hull City AFC")


def test_extra_letters() -> None:
    """The tie-break counts the letters the candidate adds to the name."""
    assert extra_letters("Milan", "AC Milan") == 2
    assert extra_letters("Milan", "FC Internazionale Milano") == 17


@pytest.mark.parametrize(
    ("name", "expected_id", "tie"),
    [
        ("Inter", 108, False),  # not Fiorentina, thanks to the letter order
        ("Lazio", 110, False),  # not Internazionale, same reason
        ("Milan", 98, True),  # tie with "Milano", decided by fewer extra letters
    ],
)
def test_best_match(name: str, expected_id: int, tie: bool) -> None:
    """The best candidate wins; a tie decided by the tie-break is reported."""
    match = best_match(name, SERIE_A)
    assert match.candidate_id == expected_id
    assert match.tie is tie


def test_find_collisions() -> None:
    """Two names on the same candidate are both flagged."""
    matches = match_names(["Inter", "Inter Milan"], SERIE_A)
    assert find_collisions(matches) == {"Inter", "Inter Milan"}


def test_find_not_mutual() -> None:
    """ "FC Internazionale Milano" scores Inter and Milan the same: Inter is not mutual."""
    matches = match_names(["Inter", "Milan", "Lazio"], SERIE_A)
    assert find_not_mutual(matches) == {"Inter"}
