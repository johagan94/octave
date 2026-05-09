"""Text normalisation and multi-strategy fuzzy matching."""

import logging
import re
import unicodedata
from typing import Callable, Optional

from rapidfuzz import fuzz

log = logging.getLogger(__name__)


_FEAT_RE = re.compile(
    r"\s*[\(\[](feat\.?|ft\.?|with|featuring)[^\)\]]*[\)\]]",
    flags=re.IGNORECASE,
)
_EDITION_RE = re.compile(
    r"\s*[\(\[](deluxe|explicit|clean|bonus|remaster(ed)?|anniversary"
    r"|expanded|special edition|re-?issue|re-?release|re-?master)[^\)\]]*[\)\]]",
    flags=re.IGNORECASE,
)
_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", flags=re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s\-]")
_WS_RE = re.compile(r"\s+")


def normalise(s: str) -> str:
    """Comprehensive text normalisation for matching.

    Steps: NFKD decomposition → ASCII fold → strip feat/edition tags →
    drop leading articles → strip punctuation → collapse whitespace → lower.
    """
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = _FEAT_RE.sub("", s)
    s = _EDITION_RE.sub("", s)
    s = _ARTICLE_RE.sub("", s.strip())
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip().lower()
    return s


def track_score(a: str, b: str) -> float:
    """Conservative score for track title / artist matching.

    Excludes partial_ratio, token_set_ratio, WRatio because they fire
    100 on substring overlaps (e.g. 'God' vs 'God Don't Make Mistakes').
    """
    an, bn = normalise(a), normalise(b)
    if an == bn:
        return 100.0
    return max(
        fuzz.ratio(an, bn),
        fuzz.token_sort_ratio(an, bn),
    )


class MatchResult:
    __slots__ = ("item", "score", "strategy")

    def __init__(self, item: dict, score: float, strategy: str):
        self.item = item
        self.score = score
        self.strategy = strategy

    def __repr__(self) -> str:
        return f"<MatchResult score={self.score:.1f} strategy={self.strategy}>"


def score_pair(a: str, b: str) -> tuple[float, str]:
    """Multi-strategy score between two strings; returns (best, strategy_name).

    Tries every rapidfuzz strategy on both normalised and raw lowercase
    forms so normalisation can never make a match worse.
    """
    an, bn = normalise(a), normalise(b)
    ar, br = a.lower().strip(), b.lower().strip()

    if an == bn or ar == br:
        return 100.0, "exact"

    candidates: dict[str, float] = {
        "ratio_norm":      fuzz.ratio(an, bn),
        "partial_norm":    fuzz.partial_ratio(an, bn),
        "token_sort_norm": fuzz.token_sort_ratio(an, bn),
        "token_set_norm":  fuzz.token_set_ratio(an, bn),
        "WRatio_norm":     fuzz.WRatio(an, bn),
        "ratio_raw":       fuzz.ratio(ar, br),
        "partial_raw":     fuzz.partial_ratio(ar, br),
        "token_sort_raw":  fuzz.token_sort_ratio(ar, br),
        "token_set_raw":   fuzz.token_set_ratio(ar, br),
        "WRatio_raw":      fuzz.WRatio(ar, br),
    }

    best_strategy = max(candidates, key=candidates.__getitem__)
    return candidates[best_strategy], best_strategy


def best_match(
    needle: str,
    candidates: list[dict],
    key_fn: Callable[[dict], str],
    threshold: float,
    log_tag: str = "",
) -> Optional[MatchResult]:
    """Score every candidate and return the best one above threshold.
    Logs the top-3 results at DEBUG level for diagnosability.
    """
    results: list[MatchResult] = []
    for c in candidates:
        val = key_fn(c)
        sc, st = score_pair(needle, val)
        results.append(MatchResult(c, sc, st))

    results.sort(key=lambda r: r.score, reverse=True)

    for i, r in enumerate(results[:3]):
        log.debug(
            "    %s candidate #%d: %r  score=%.1f  strategy=%s",
            log_tag, i + 1, key_fn(r.item), r.score, r.strategy,
        )

    if results and results[0].score >= threshold:
        return results[0]
    return None
