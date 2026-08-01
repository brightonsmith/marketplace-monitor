from __future__ import annotations

from .models import Listing, SearchConfig
from .notifier import format_price
from .ranking import rank_listings


def format_report(
    listings: list[Listing],
    searches: tuple[SearchConfig, ...],
    *,
    limit: int,
) -> str:
    search_by_name = {search.name: search for search in searches}
    ranked = [
        candidate
        for candidate in rank_listings(listings, search_by_name)
        if not candidate.excluded
    ][:limit]
    lines = [
        f"Marketplace report · {len(listings)} listings · showing {len(ranked)}"
    ]
    if not ranked:
        lines.append("No reportable listings found.")
        return "\n".join(lines)

    for index, candidate in enumerate(ranked, start=1):
        listing = candidate.listing
        kind = "exact" if candidate.exact else "candidate"
        location = f" · {listing.location}" if listing.location else ""
        lines.extend(
            [
                "",
                (
                    f"{index}. {100 * candidate.relevance:.1f}% match · "
                    f"{100 * candidate.score:.1f}% score · {kind}"
                ),
                f"   {format_price(listing.price_cents)} · {listing.title}{location}",
                f"   Search: {listing.search_name}",
                f"   {listing.url}",
            ]
        )
    return "\n".join(lines)
