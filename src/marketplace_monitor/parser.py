from __future__ import annotations

import re
import unicodedata
from collections import Counter
from math import log, sqrt
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from .models import Listing, SearchConfig

ITEM_ID_PATTERN = re.compile(r"/marketplace/item/(\d+)")
PRICE_PATTERN = re.compile(r"(?:US\$|\$)\s*([0-9][0-9,]*(?:\.\d{1,2})?)", re.IGNORECASE)
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
NUMBER_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
NUMBER_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
NUMBER_SCALES = {"hundred": 100, "thousand": 1_000}
NUMBER_WORDS = set(NUMBER_UNITS) | set(NUMBER_TENS) | set(NUMBER_SCALES)


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


def _number_value(words: list[str]) -> int:
    total = 0
    current = 0
    for word in words:
        if word in NUMBER_UNITS:
            current += NUMBER_UNITS[word]
        elif word in NUMBER_TENS:
            current += NUMBER_TENS[word]
        elif word == "hundred":
            current = max(current, 1) * NUMBER_SCALES[word]
        elif word == "thousand":
            total += max(current, 1) * NUMBER_SCALES[word]
            current = 0
    return total + current


def _normalize_number_words(words: tuple[str, ...]) -> tuple[str, ...]:
    def kind(word: str) -> str:
        if word in NUMBER_UNITS:
            return "unit"
        if word in NUMBER_TENS:
            return "tens"
        return word

    def can_follow(previous: str, word: str) -> bool:
        current = kind(word)
        if current == "unit":
            return previous in {"tens", "hundred", "thousand"}
        if current == "tens":
            return previous in {"hundred", "thousand"}
        if current == "hundred":
            return previous == "unit"
        if current == "thousand":
            return previous in {"unit", "tens", "hundred"}
        return False

    normalized: list[str] = []
    index = 0
    while index < len(words):
        if words[index] not in NUMBER_WORDS:
            normalized.append(words[index])
            index += 1
            continue
        end = index + 1
        previous = kind(words[index])
        while end < len(words):
            if words[end] == "and":
                if (
                    previous not in {"hundred", "thousand"}
                    or end + 1 >= len(words)
                    or words[end + 1] not in NUMBER_WORDS
                ):
                    break
                end += 1
                continue
            if words[end] not in NUMBER_WORDS or not can_follow(
                previous, words[end]
            ):
                break
            previous = kind(words[end])
            end += 1
        number_words = [word for word in words[index:end] if word != "and"]
        normalized.append(str(_number_value(number_words)))
        index = end
    return tuple(normalized)


def _normalized_words(text: str) -> tuple[str, tuple[str, ...]]:
    normalized = unicodedata.normalize("NFKD", text.casefold()).replace(
        "+", " plus "
    )
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", normalized)
    words = _normalize_number_words(tuple(TOKEN_PATTERN.findall(normalized)))
    return " ".join(words), words


def normalize_match_text(text: str) -> str:
    """Return a compact key for punctuation- and spacing-invariant matching."""
    _, words = _normalized_words(text)
    return "".join(words)


def _word_features(text: str) -> tuple[str, ...]:
    _, words = _normalized_words(text)
    unigrams = tuple(words)
    bigrams = tuple(f"{first} {second}" for first, second in zip(words, words[1:]))
    compounds = tuple(f"{first}{second}" for first, second in zip(words, words[1:]))
    return unigrams + bigrams + compounds


def _character_features(text: str) -> tuple[str, ...]:
    normalized = normalize_match_text(text)
    bounded = f"^{normalized}$"
    return tuple(
        bounded[index : index + width]
        for width in (3, 4, 5)
        for index in range(max(0, len(bounded) - width + 1))
    )


def _tfidf_cosine_scores(
    query_features: set[str],
    documents: dict[str, Counter[str]],
) -> dict[str, float]:
    if not query_features:
        return {listing_id: 0.0 for listing_id in documents}

    document_frequency: Counter[str] = Counter()
    for features in documents.values():
        document_frequency.update(features.keys())
    document_count = len(documents)

    def idf(feature: str) -> float:
        return log((document_count + 1) / (document_frequency[feature] + 1)) + 1

    query_vector = {feature: idf(feature) for feature in query_features}
    query_norm = sqrt(sum(weight * weight for weight in query_vector.values()))

    scores: dict[str, float] = {}
    for listing_id, features in documents.items():
        document_vector = {
            feature: (1 + log(count)) * idf(feature)
            for feature, count in features.items()
        }
        document_norm = sqrt(
            sum(weight * weight for weight in document_vector.values())
        )
        if document_norm == 0:
            scores[listing_id] = 0.0
            continue
        dot_product = sum(
            query_vector.get(feature, 0.0) * weight
            for feature, weight in document_vector.items()
        )
        scores[listing_id] = dot_product / (query_norm * document_norm)
    return scores


def listing_relevance_scores(
    listings: list[Listing],
    search: SearchConfig,
) -> dict[str, float]:
    """Return corpus-derived TF-IDF cosine similarity for each listing title."""
    if not listings:
        return {}

    query_values = parse_qs(urlsplit(search.url).query).get("query", [])
    query_texts = (search.name, *search.include_any, *query_values)
    query_word_features = {
        feature for text in query_texts for feature in _word_features(text)
    }
    query_character_features = {
        feature for text in query_texts for feature in _character_features(text)
    }
    word_documents = {
        listing.listing_id: Counter(_word_features(listing.title))
        for listing in listings
    }
    character_documents = {
        listing.listing_id: Counter(_character_features(listing.title))
        for listing in listings
    }
    word_scores = _tfidf_cosine_scores(query_word_features, word_documents)
    character_scores = _tfidf_cosine_scores(
        query_character_features,
        character_documents,
    )
    return {
        listing.listing_id: (
            0.40 * word_scores[listing.listing_id]
            + 0.60 * character_scores[listing.listing_id]
        )
        for listing in listings
    }


def listing_relevance_score(
    listing: Listing,
    search: SearchConfig,
    corpus: list[Listing] | None = None,
) -> float:
    listings = corpus or [listing]
    return listing_relevance_scores(listings, search).get(listing.listing_id, 0.0)


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
        image_url=card.get("image_url") or None,
    )


def matches_search(listing: Listing, search: SearchConfig) -> bool:
    title = normalize_match_text(listing.title)
    if search.include_any and not any(
        normalize_match_text(term) in title for term in search.include_any
    ):
        return False
    if any(normalize_match_text(term) in title for term in search.exclude):
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
