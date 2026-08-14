from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from math import log1p
from urllib.parse import parse_qs, urlsplit

from .browser import fetch_listings
from .geocoding import DistanceFilter
from .models import AppConfig, Listing, SearchConfig
from .parser import matches_search, normalize_match_text, normalized_words

GENERIC_WORDS = {
    "a",
    "an",
    "and",
    "condition",
    "for",
    "great",
    "in",
    "like",
    "new",
    "sale",
    "the",
    "used",
    "with",
}


@dataclass(frozen=True)
class PhraseSuggestion:
    phrase: str
    matching_listings: int
    example_titles: tuple[str, ...]


@dataclass(frozen=True)
class PhraseSuggestionReport:
    analyzed_listings: int
    suggestions: tuple[PhraseSuggestion, ...]


@dataclass(frozen=True)
class _RankedCandidate:
    phrase: str
    matching_ids: frozenset[str]
    score: float


async def fetch_phrase_suggestions(
    config: AppConfig,
    search: SearchConfig,
    *,
    limit: int = 8,
) -> PhraseSuggestionReport:
    """Fetch current listings and analyze them for exact-title phrases."""
    distance_filter = (
        DistanceFilter(config.database_path, min_delay_seconds=1.0)
        if search.max_distance_miles is not None
        else None
    )
    try:
        listings = await fetch_listings(
            config.browser,
            (search,),
            distance_filter=distance_filter,
        )
    finally:
        if distance_filter is not None:
            distance_filter.close()
    return suggest_exact_phrases(listings, search, limit=limit)


def _query_words(search: SearchConfig) -> tuple[str, ...]:
    values = parse_qs(urlsplit(search.url).query).get("query", [])
    query = values[0] if values else search.name
    words = normalized_words(query)
    return words or normalized_words(search.name)


def _ngrams(words: tuple[str, ...], minimum: int, maximum: int):
    for width in range(minimum, min(maximum, len(words)) + 1):
        for start in range(len(words) - width + 1):
            yield words[start : start + width]


def suggest_exact_phrases(
    listings: list[Listing],
    search: SearchConfig,
    *,
    limit: int = 8,
) -> PhraseSuggestionReport:
    """Rank exact-title phrases grounded in the current Marketplace results."""
    if limit < 1:
        raise ValueError("suggestion limit must be positive")

    broad_search = replace(search, include_any=())
    eligible = [
        listing for listing in listings if matches_search(listing, broad_search)
    ]
    query_words = _query_words(search)
    query_set = set(query_words)
    if not eligible or not query_words:
        return PhraseSuggestionReport(len(eligible), ())

    title_words = {
        listing.listing_id: normalized_words(listing.title)
        for listing in eligible
    }
    document_frequency = Counter(
        word
        for words in title_words.values()
        for word in set(words) & query_set
    )
    anchor_candidates = [
        word
        for word in dict.fromkeys(query_words)
        if word not in GENERIC_WORDS and document_frequency[word] > 0
    ]
    anchor_candidates.sort(
        key=lambda word: (document_frequency[word], query_words.index(word))
    )
    anchors = set(anchor_candidates[:2]) or query_set
    required_overlap = min(2, len(query_set))

    candidates: set[tuple[str, ...]] = set()
    minimum_width = 1 if len(query_words) == 1 else 2
    candidates.update(_ngrams(query_words, minimum_width, 5))
    for words in title_words.values():
        candidates.update(_ngrams(words, minimum_width, 5))

    compact_titles = {
        listing.listing_id: normalize_match_text(listing.title)
        for listing in eligible
    }
    ranked: list[_RankedCandidate] = []
    compact_query = "".join(query_words)
    for candidate in candidates:
        candidate_set = set(candidate)
        overlap = len(candidate_set & query_set)
        if overlap < required_overlap or not candidate_set & anchors:
            continue
        compact_candidate = "".join(candidate)
        matching_ids = frozenset(
            listing_id
            for listing_id, title in compact_titles.items()
            if compact_candidate in title
        )
        if not matching_ids:
            continue
        query_sequence_bonus = 1.5 if compact_candidate in compact_query else 0.0
        coverage = overlap / len(query_set)
        score = (
            4.0 * log1p(len(matching_ids))
            + 2.0 * overlap
            + 2.0 * coverage
            + 0.4 * min(len(candidate), 5)
            + query_sequence_bonus
        )
        ranked.append(
            _RankedCandidate(
                phrase=" ".join(candidate),
                matching_ids=matching_ids,
                score=score,
            )
        )

    ranked.sort(
        key=lambda candidate: (
            candidate.score,
            len(candidate.matching_ids),
            len(candidate.phrase),
            candidate.phrase,
        ),
        reverse=True,
    )
    selected: list[_RankedCandidate] = []
    for candidate in ranked:
        if any(
            candidate.matching_ids == existing.matching_ids
            and (
                normalize_match_text(candidate.phrase)
                in normalize_match_text(existing.phrase)
                or normalize_match_text(existing.phrase)
                in normalize_match_text(candidate.phrase)
            )
            for existing in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break

    suggestions = []
    for candidate in selected:
        examples: list[str] = []
        for listing in eligible:
            if listing.listing_id not in candidate.matching_ids:
                continue
            title = listing.title
            if title not in examples:
                examples.append(title)
            if len(examples) == 2:
                break
        suggestions.append(
            PhraseSuggestion(
                phrase=candidate.phrase,
                matching_listings=len(candidate.matching_ids),
                example_titles=tuple(examples),
            )
        )
    return PhraseSuggestionReport(len(eligible), tuple(suggestions))
