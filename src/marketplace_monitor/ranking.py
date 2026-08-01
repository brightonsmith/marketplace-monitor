from __future__ import annotations

from dataclasses import dataclass

from .models import Listing, SearchConfig
from .parser import listing_relevance_scores, matches_search


@dataclass(frozen=True)
class RankedListing:
    listing: Listing
    relevance: float
    score: float
    exact: bool
    excluded: bool


def _price_distance(listing: Listing, search: SearchConfig) -> int:
    if listing.price_cents is None:
        return 10**15
    if search.min_price_cents is not None and listing.price_cents < search.min_price_cents:
        return search.min_price_cents - listing.price_cents
    if search.max_price_cents is not None and listing.price_cents > search.max_price_cents:
        return listing.price_cents - search.max_price_cents
    return 0


def price_compliance(listing: Listing, search: SearchConfig) -> float:
    if listing.price_cents is None:
        return 0.0
    distance = _price_distance(listing, search)
    if distance == 0:
        return 1.0
    reference = max(
        search.min_price_cents or 0,
        search.max_price_cents or 0,
        listing.price_cents,
        1,
    )
    return max(0.0, 1.0 - distance / reference)


def rank_listings(
    listings: list[Listing],
    searches: dict[str, SearchConfig],
) -> list[RankedListing]:
    corpora = {
        search_name: [
            listing for listing in listings if listing.search_name == search_name
        ]
        for search_name in searches
    }
    relevance_scores = {
        listing_id: score
        for search_name, corpus in corpora.items()
        for listing_id, score in listing_relevance_scores(
            corpus,
            searches[search_name],
        ).items()
    }

    ranked: list[RankedListing] = []
    for listing in listings:
        search = searches[listing.search_name]
        relevance = relevance_scores[listing.listing_id]
        title = listing.title.casefold()
        excluded = any(term in title for term in search.exclude)
        score = (
            0.90 * relevance
            + 0.10 * price_compliance(listing, search)
            - float(excluded)
        )
        ranked.append(
            RankedListing(
                listing=listing,
                relevance=relevance,
                score=score,
                exact=matches_search(listing, search),
                excluded=excluded,
            )
        )

    return sorted(
        ranked,
        key=lambda candidate: (
            candidate.score,
            candidate.relevance,
            -(
                candidate.listing.price_cents
                if candidate.listing.price_cents is not None
                else 10**15
            ),
            candidate.listing.title.casefold(),
        ),
        reverse=True,
    )
