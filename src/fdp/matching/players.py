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

import math
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


def similarity(name: str, other: str) -> tuple[float, str, float]:
    """Score 0-100 between two names, the measure that gave it, and the edit score.

    - Damerau-Levenshtein: edits (insert, delete, replace, swap two neighbours)
      needed to turn one name into the other, as a share of the length.
      "martinez" vs "martniez" is one swap, not two replacements.
    - token_set_ratio: compares the words in common and ignores extra words,
      so "lautaro martinez" vs "javier lautaro martinez" scores 100.
    Both run on the normalised names; the higher score is kept. The edit score is
    also returned: it breaks ties, because token_set_ratio gives 100 to any name
    contained in the other ("federico" vs "dimarco federico").
    """
    a, b = normalize_name(name), normalize_name(other)
    edits = DamerauLevenshtein.normalized_similarity(a, b) * 100
    words = fuzz.token_set_ratio(a, b)
    if edits >= words:
        return edits, "damerau_levenshtein", edits
    return words, "token_set", edits


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
    # True if another candidate is equal on score and edits (the pick is arbitrary).
    tie: bool
    # True if the team method (ordered letters, LCS) rates this candidate best too.
    lcs_agrees: bool


def best_candidate(name: str, candidates: Mapping[int, str]) -> PlayerMatch | None:
    """Pick the best candidate by the brief's score; None if there are no candidates."""
    if not candidates:
        return None
    # Best score first; on equal scores, fewer edits (same idea as the team
    # tie-break: "Federico Dimarco" beats "Federico" for "Federico Dimarco").
    scored = sorted(
        ((score, edits, method, tm_id) for tm_id, other in candidates.items()
         for score, method, edits in [similarity(name, other)]),
        reverse=True,
    )  # fmt: skip
    score, edits, method, tm_id = scored[0]
    # A real tie: the runner-up is equal on both the score and the edits.
    tie = len(scored) > 1 and scored[1][:2] == (score, edits)
    # Second opinion: the LCS method, in the direction where the shorter name is
    # looked up in the longer one (a name can be shorter on either side).
    # It agrees if our pick has its best score; a one-word name contained in the
    # pick (e.g. "Federico") can tie with it, and a tie is not a disagreement.
    lcs = {
        other_id: max(name_score(name, other), name_score(other, name))
        for other_id, other in candidates.items()
    }
    return PlayerMatch(
        tm_player_id=tm_id,
        tm_player_name=candidates[tm_id],
        score=round(score, 1),
        method=method,
        band=classify(score),
        tie=tie,
        lcs_agrees=math.isclose(lcs[tm_id], max(lcs.values())),
    )
