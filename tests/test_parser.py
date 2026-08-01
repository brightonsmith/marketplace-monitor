from marketplace_monitor.models import Listing, SearchConfig
from marketplace_monitor.parser import (
    canonicalize_listing_url,
    listing_from_card,
    matches_search,
    parse_price_cents,
)


def test_parse_price() -> None:
    assert parse_price_cents("$1,250") == 125_000
    assert parse_price_cents("US$ 99.50") == 9_950
    assert parse_price_cents("Free") == 0
    assert parse_price_cents("No price shown") is None


def test_listing_from_card() -> None:
    search = SearchConfig(name="Flair", url="https://www.facebook.com/marketplace/search/")
    listing = listing_from_card(
        {
            "href": "https://www.facebook.com/marketplace/item/123456/?tracking=abc",
            "text": "$425\nFlair 58 Plus\nDenver, Colorado",
        },
        search,
    )
    assert listing is not None
    assert listing.listing_id == "123456"
    assert listing.title == "Flair 58 Plus"
    assert listing.price_cents == 42_500
    assert listing.location == "Denver, Colorado"
    assert listing.url == "https://www.facebook.com/marketplace/item/123456"


def test_matches_search_terms_and_price() -> None:
    search = SearchConfig(
        name="Flair",
        url="https://www.facebook.com/marketplace/search/",
        min_price_cents=20_000,
        max_price_cents=60_000,
        include_any=("flair 58",),
        exclude=("wanted",),
    )
    listing = Listing("1", "Flair 58 Plus", "https://example.test/1", "Flair", 42_500)
    assert matches_search(listing, search)
    assert not matches_search(
        Listing("2", "Wanted: Flair 58", "https://example.test/2", "Flair", 42_500),
        search,
    )


def test_canonicalize_relative_url() -> None:
    assert canonicalize_listing_url("/marketplace/item/123/?ref=browse") == (
        "https://www.facebook.com/marketplace/item/123"
    )

