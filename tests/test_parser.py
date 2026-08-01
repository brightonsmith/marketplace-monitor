from marketplace_monitor.models import Listing, SearchConfig
from marketplace_monitor.parser import (
    canonicalize_listing_url,
    listing_relevance_score,
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


def test_listing_from_discounted_card_skips_original_price() -> None:
    search = SearchConfig(name="Flair", url="https://www.facebook.com/marketplace/search/")
    listing = listing_from_card(
        {
            "href": "https://www.facebook.com/marketplace/item/123456/",
            "text": "$100\n$200\nDeLonghi Magnifica Coffee Machine\nDenver, Colorado",
        },
        search,
    )
    assert listing is not None
    assert listing.price_cents == 10_000
    assert listing.title == "DeLonghi Magnifica Coffee Machine"
    assert listing.location == "Denver, Colorado"


def test_relevance_prioritizes_product_identity_over_unrelated_brand() -> None:
    search = SearchConfig(
        name="Flair 58 Plus",
        url="https://www.facebook.com/marketplace/search/?query=flair%2058%20plus",
        include_any=("flair 58", "flair58+"),
    )
    flair = Listing("1", "Flair58+ Manual Espresso Maker", "https://example/1", "Flair")
    delonghi = Listing(
        "2",
        "DeLonghi Magnifica Coffee Machine",
        "https://example/2",
        "Flair",
    )

    assert listing_relevance_score(flair, search) > 0.85
    assert listing_relevance_score(delonghi, search) < 0.2


def test_relevance_requires_distinctive_brand_anchor() -> None:
    search = SearchConfig(
        name="Flair 58 Plus",
        url="https://www.facebook.com/marketplace/search/?query=flair%2058",
    )
    flair_signature = Listing(
        "1",
        "Flair Signature Espresso Maker",
        "https://example/1",
        search.name,
    )
    leather_belt = Listing(
        "2",
        "Men's Genuine Leather Belt (58)",
        "https://example/2",
        search.name,
    )
    espresso_tools = Listing(
        "3",
        "Normcore Espresso Tools for 58mm Group Heads",
        "https://example/3",
        search.name,
    )

    flair_score = listing_relevance_score(flair_signature, search)
    assert flair_score > listing_relevance_score(leather_belt, search)
    assert flair_score > listing_relevance_score(espresso_tools, search)
    assert listing_relevance_score(leather_belt, search) < 0.1


def test_relevance_anchor_is_derived_for_unrelated_product_category() -> None:
    search = SearchConfig(
        name="Odyssey Spider Putter",
        url="https://www.facebook.com/marketplace/search/?query=odyssey%20spider%20putter",
    )
    odyssey = Listing(
        "1",
        "Odyssey Spider Putter 35 inch",
        "https://example/1",
        search.name,
    )
    wrong_brand = Listing(
        "2",
        "TaylorMade Spider Putter",
        "https://example/2",
        search.name,
    )
    unrelated = Listing(
        "3",
        "Spider Plant Ceramic Pot",
        "https://example/3",
        search.name,
    )

    odyssey_score = listing_relevance_score(odyssey, search)
    assert odyssey_score > listing_relevance_score(wrong_brand, search)
    assert odyssey_score > listing_relevance_score(unrelated, search)
    assert listing_relevance_score(wrong_brand, search) < 0.1


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
