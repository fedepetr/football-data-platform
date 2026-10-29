"""Team name matching across sources: letters of each word, compared in order (LCS).

Method (designed step by step on the real data, see docs/DEVELOPMENT.md):
1. Split names into normalised words ("Nott'm Forest" -> ["nott", "m", "forest"]).
2. For each word of the name, find how many of its letters appear in the same
   order in a word of the candidate (longest common subsequence), divided by the
   length of the word: "man" in "manchester" = 3/3. The name score is the average.
3. The best candidate wins; on equal scores, the one with fewer extra letters
   ("Milan": "AC Milan" beats "FC Internazionale Milano").
4. Consistency checks flag doubtful matches for manual review: two names on the
   same candidate (collision) and candidates that would not pick the name back.

This module only proposes matches: the final team map is a reviewed dbt seed.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from unidecode import unidecode

# Candidate ids are football-data.org / Transfermarkt ids (int) or names (str).
CandidateId = int | str


def normalize_words(name: str) -> list[str]:
    """Lowercase ASCII words of a name: accents removed, punctuation splits words.

    "Atlético Madrid" -> ["atletico", "madrid"]; "Nott'm Forest" -> ["nott", "m", "forest"].
    """
    return re.findall(r"[a-z0-9]+", unidecode(name).lower())


def lcs_length(a: str, b: str) -> int:
    """Length of the longest common subsequence: letters of `a` found in `b` in the
    same order, gaps allowed. "inter" in "internazionale" = 5, in "fiorentina" = 3.

    Dynamic programming, one row at a time: previous[j] is the answer for the
    letters of `a` seen so far against the first j letters of `b`.
    """
    previous = [0] * (len(b) + 1)
    for letter_a in a:
        current = [0]
        for j, letter_b in enumerate(b):
            if letter_a == letter_b:
                # Same letter: extend the subsequence found without both letters.
                current.append(previous[j] + 1)
            else:
                # Different letters: keep the best result skipping one of them.
                current.append(max(previous[j + 1], current[j]))
        previous = current
    return previous[-1]


def name_score(name: str, candidate: str) -> float:
    """Similarity of `name` to `candidate` between 0 and 1.

    Each word of `name` takes its best word of `candidate` (share of its letters
    found in order); the score is the average over the words of `name`.
    "Man City" vs "Manchester City FC" = 1.0, vs "Hull City AFC" = 0.67.
    """
    words, candidate_words = normalize_words(name), normalize_words(candidate)
    if not words or not candidate_words:
        return 0.0
    best_per_word = [
        max(lcs_length(word, other) / len(word) for other in candidate_words) for word in words
    ]
    return sum(best_per_word) / len(best_per_word)


def extra_letters(name: str, candidate: str) -> int:
    """Letters of `candidate` beyond those of `name`: the tie-break, fewer is better.

    "Milan" vs "AC Milan" = 2, vs "FC Internazionale Milano" = 17.
    """
    return len("".join(normalize_words(candidate))) - len("".join(normalize_words(name)))


def _rank(name: str, candidate: str) -> tuple[float, int]:
    """Sort key: higher score first, then fewer extra letters."""
    return (-name_score(name, candidate), extra_letters(name, candidate))


@dataclass(frozen=True)
class NameMatch:
    """Best candidate for one name, with how sure the choice is."""

    name: str
    candidate_id: CandidateId
    candidate_name: str
    score: float
    # True if another candidate had the same score and the tie-break decided.
    tie: bool


def best_match(name: str, candidates: Mapping[CandidateId, str]) -> NameMatch:
    """Pick the candidate whose name is closest to `name` (score, then tie-break)."""
    ranked = sorted(candidates.items(), key=lambda item: _rank(name, item[1]))
    best_id, best_name = ranked[0]
    score = name_score(name, best_name)
    tie = len(ranked) > 1 and math.isclose(score, name_score(name, ranked[1][1]))
    return NameMatch(name, best_id, best_name, score, tie)


def match_names(
    names: Iterable[str], candidates: Mapping[CandidateId, str]
) -> dict[str, NameMatch]:
    """Best match for every name (candidates should be in the same league)."""
    return {name: best_match(name, candidates) for name in names}


def find_collisions(matches: Mapping[str, NameMatch]) -> set[str]:
    """Names whose candidate was also picked by another name: at least one is wrong."""
    picks = Counter(match.candidate_id for match in matches.values())
    return {name for name, match in matches.items() if picks[match.candidate_id] > 1}


def find_not_mutual(matches: Mapping[str, NameMatch]) -> set[str]:
    """Names whose candidate would not clearly pick them back.

    Reverse check: for the chosen candidate, rank all names. The match is mutual
    only if this name comes first with no other name on the same score.
    """
    names = list(matches)
    not_mutual = set()
    for name, match in matches.items():
        ranked = sorted(names, key=lambda other: _rank(other, match.candidate_name))
        top_score = name_score(ranked[0], match.candidate_name)
        tied = len(ranked) > 1 and math.isclose(
            top_score, name_score(ranked[1], match.candidate_name)
        )
        if ranked[0] != name or tied:
            not_mutual.add(name)
    return not_mutual
