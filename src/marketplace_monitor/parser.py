from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from .models import Listing, SearchConfig

ITEM_ID_PATTERN = re.compile(r"/marketplace/item/(\d+)")
PRICE_PATTERN = re.compile(r"(?:US\$|\$)\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.IGNORECASE)
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
GENERIC_PRODUCT_WORDS = {
    "coffee",
    "espresso",
    "machine",
    "maker",
    "manual",
}


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


def _normalized_words(text: str) -> tuple[str, tuple[str, ...]]:
    normalized = text.casefold().replace("+", " plus ")
    normalized = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", normalized)
    words = tuple(TOKEN_PATTERN.findall(normalized))
    return " ".join(words), words


def _word_weight(word: str) -> float:
    if word.isdigit() or word == "plus":
        return 2.5
    if word in GENERIC_PRODUCT_WORDS:
        return 0.5
    return 1.0


def listing_relevance_score(listing: Listing, search: SearchConfig) -> float:
    """Return deterministic title relevance in the range 0.0 to 1.0."""
    title, title_words = _normalized_words(listing.title)
    if not title:
        return 0.0
    title_word_set = set(title_words)

    query = parse_qs(urlsplit(search.url).query).get("query", [])
    targets = (search.name, *search.include_any, *query)
    best = 0.0
    for target_text in targets:
        target, target_words = _normalized_words(target_text)
        if not target_words:
            continue
        total_weight = sum(_word_weight(word) for word in target_words)
        matched_weight = sum(
            _word_weight(word) for word in target_words if word in title_word_set
        )
        word_coverage = matched_weight / total_weight
        string_similarity = SequenceMatcher(None, target, title).ratio()
        exact_phrase = float(target in title)
        score = 0.60 * word_coverage + 0.25 * string_similarity + 0.15 * exact_phrase
        best = max(best, score)

    _, search_name_words = _normalized_words(search.name)
    anchor_words = tuple(
        word
        for word in search_name_words
        if not word.isdigit()
        and word != "plus"
        and word not in GENERIC_PRODUCT_WORDS
    )[:1]
    if anchor_words:
        anchor_coverage = sum(word in title_word_set for word in anchor_words) / len(
            anchor_words
        )
        if anchor_coverage == 0:
            best *= 0.10
        else:
            best = 0.80 * best + 0.20 * anchor_coverage
    return min(best, 1.0)


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
    # Discounted Marketplace cards can contain both the current and original
    # price on consecutive lines. The title is the first non-price line after
    # the first displayed price, not necessarily the immediately following line.
    title_index = next(
        (
            index
            for index in range(
                (price_index + 1) if price_index is not None else 0,
                len(lines),
            )
            if parse_price_cents(lines[index]) is None
        ),
        0,
    )
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
