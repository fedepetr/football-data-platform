"""Player name matching rules from the brief (football-data.org -> Transfermarkt).

1. Normalise names: lowercase, no accents or punctuation, words in alphabetical
   order ("Martínez Lautaro" and "Lautaro Martinez" -> "lautaro martinez").
2. Compare only plausible candidates (same birth date, or same team and season):
   chosen by the caller, see player_map.py.
3. Score 0-100: normalised Damerau-Levenshtein, and token_set_ratio for names
   with extra or missing words; the higher of the two is kept.
4. Thresholds: above 92 automatic, 80-92 to review, below 80 rejected.

A second, independent opinion comes from the team method (ordered letters, LCS,
teams.py): when the two pick different players the match goes to review.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from rapidfuzz import fuzz
from rapidfuzz.distance import DamerauLevenshtein
from unidecode import unidecode

from fdp.config import load_config
from fdp.matching.teams import name_score, normalize_words

_THRESHOLDS = load_config()["matching"]["players"]
AUTO_THRESHOLD: float = _THRESHOLDS["auto_threshold"]
REVIEW_THRESHOLD: float = _THRESHOLDS["review_threshold"]


def normalize_name(name: str) -> str:
    """Words without accents or punctuation, lowercase, in alphabetical order.

    Sorting makes the word order irrelevant: "Martínez Lautaro" -> "lautaro martinez".
    Apostrophes are removed rather than splitting words, because sources disagree
    on them: "N'Diaye" and "Ndiaye" both become "ndiaye" (unidecode turns the
    typographic apostrophe into a plain one first).
    """
    return " ".join(sorted(normalize_words(unidecode(name).replace("'", ""))))


def similarity(name: str, other: str) -> tuple[float, str]:
    """Score 0-100 between two names and the measure that gave it.

    - Damerau-Levenshtein: edits (insert, delete, replace, swap two neighbours)
      needed to turn one name into the other, as a share of the length.
      "martinez" vs "martniez" is one swap, not two replacements.
    - token_set_ratio: compares the words in common and ignores extra words,
      so "lautaro martinez" vs "javier lautaro martinez" scores 100.
    Both run on the normalised names; the higher score is kept.
    """
    a, b = normalize_name(name), normalize_name(other)
    edits = DamerauLevenshtein.normalized_similarity(a, b) * 100
    words = fuzz.token_set_ratio(a, b)
    if edits >= words:
        return edits, "damerau_levenshtein"
    return words, "token_set"


def classify(score: float) -> str:
    """Brief thresholds: "auto" above 92, "review" from 80 to 92, "rejected" below 80."""
    if score > AUTO_THRESHOLD:
        return "auto"
    if score >= REVIEW_THRESHOLD:
        return "review"
    return "rejected"


@dataclass(frozen=True)
class PlayerMatch:
    """Best Transfermarkt candidate for one football-data.org player."""

    tm_player_id: int
    tm_player_name: str
    score: float
    method: str
    band: str
    # True if another candidate reached the same score (the pick is arbitrary).
    tie: bool
    # True if the team method (ordered letters, LCS) picks the same candidate.
    lcs_agrees: bool


def best_candidate(name: str, candidates: Mapping[int, str]) -> PlayerMatch | None:
    """Pick the best candidate by the brief's score; None if there are no candidates."""
    if not candidates:
        return None
    scored = sorted(
        ((*similarity(name, other), tm_id) for tm_id, other in candidates.items()),
        reverse=True,
    )
    score, method, tm_id = scored[0]
    tie = len(scored) > 1 and scored[1][0] == score
    # Second opinion: the LCS method, in the direction where the shorter name is
    # looked up in the longer one (a name can be shorter on either side).
    lcs_pick = max(
        candidates,
        key=lambda other_id: max(
            name_score(name, candidates[other_id]), name_score(candidates[other_id], name)
        ),
    )
    return PlayerMatch(
        tm_player_id=tm_id,
        tm_player_name=candidates[tm_id],
        score=round(score, 1),
        method=method,
        band=classify(score),
        tie=tie,
        lcs_agrees=lcs_pick == tm_id,
    )
