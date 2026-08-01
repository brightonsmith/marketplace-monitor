from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from .models import Listing, SearchConfig

ITEM_ID_PATTERN = re.compile(r"/marketplace/item/(\d+)")
PRICE_PATTERN = re.compile(r"(?:US\$|\$)\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.IGNORECASE)


def canonicalize_listing_url(url: str) -> str:
    absolute = urljoin("https://www.facebook.com", url)
    parts = urlsplit(absolute)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def listing_id_from_url(url: str) -> str | None:
    match = ITEM_ID_PATTERN.search(url)
    return match.group(1) if match else None


def parse_price_cents(text: str) -> int | None:
    if re.search(r"\bfree\b", text, re.IGNORECASE):
        return 0
    match = PRICE_PATTERN.search(text)
    if not match:
        return None
    return round(float(match.group(1).replace(",", "")) * 100)


def listing_from_card(card: dict[str, str], search: SearchConfig) -> Listing | None:
    href = card.get("href", "")
    listing_id = listing_id_from_url(href)
    if not listing_id:
        return None

    lines = [line.strip() for line in card.get("text", "").splitlines() if line.strip()]
    if not lines:
        return None

    price_index = next(
        (index for index, line in enumerate(lines) if parse_price_cents(line) is not None),
        None,
    )
    price_cents = parse_price_cents(lines[price_index]) if price_index is not None else None
    title_index = (price_index + 1) if price_index is not None else 0
    title = lines[title_index] if title_index < len(lines) else lines[0]
    location = lines[title_index + 1] if title_index + 1 < len(lines) else None

    return Listing(
        listing_id=listing_id,
        title=title,
        url=canonicalize_listing_url(href),
        search_name=search.name,
        price_cents=price_cents,
        location=location,
    )


def matches_search(listing: Listing, search: SearchConfig) -> bool:
    title = listing.title.casefold()
    if search.include_any and not any(term in title for term in search.include_any):
        return False
    if any(term in title for term in search.exclude):
        return False
    has_price_limit = search.min_price_cents is not None or search.max_price_cents is not None
    if has_price_limit and listing.price_cents is None:
        return False
    if listing.price_cents is not None:
        if search.min_price_cents is not None and listing.price_cents < search.min_price_cents:
            return False
        if search.max_price_cents is not None and listing.price_cents > search.max_price_cents:
            return False
    return True
