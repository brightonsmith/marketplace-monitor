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
    ]
    lines = [
        f"Marketplace report · {len(listings)} listings · top {limit} per search"
    ]
    for search in searches:
        discovered = [
            listing for listing in listings if listing.search_name == search.name
        ]
        candidates = [
            candidate
            for candidate in ranked
            if candidate.listing.search_name == search.name
        ][:limit]
        lines.extend(
            [
                "",
                f"{search.name} · {len(discovered)} found · showing {len(candidates)}",
            ]
        )
        if not candidates:
            lines.append("  No reportable listings found.")
            continue
        for index, candidate in enumerate(candidates, start=1):
            listing = candidate.listing
            kind = "exact" if candidate.exact else "candidate"
            location = f" · {listing.location}" if listing.location else ""
            distance = (
                f" · {listing.distance_miles:.1f} mi"
                if listing.distance_miles is not None
                else ""
            )
            lines.extend(
                [
                    (
                        f"  {index}. {100 * candidate.relevance:.1f}% match · "
                        f"{100 * candidate.score:.1f}% score · {kind}"
                    ),
                    (
                        f"     {format_price(listing.price_cents)} · "
                        f"{listing.title}{location}{distance}"
                    ),
                    f"     ID: {listing.listing_id}",
                    f"     {listing.url}",
                ]
            )
    return "\n".join(lines)
