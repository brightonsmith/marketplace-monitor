from marketplace_monitor.models import Listing, SearchConfig
from marketplace_monitor.suggestions import suggest_exact_phrases


def _listing(
    listing_id: str,
    title: str,
    *,
    price_cents: int = 5_000,
) -> Listing:
    return Listing(
        listing_id=listing_id,
        title=title,
        url=f"https://www.facebook.com/marketplace/item/{listing_id}",
        search_name="Away Carry-On Luggage",
        price_cents=price_cents,
    )


def test_suggestions_rank_recurring_normalized_phrases() -> None:
    search = SearchConfig(
        name="Away Carry-On Luggage",
        url=(
            "https://www.facebook.com/marketplace/denver/search"
            "?query=away%20carry%20on%20luggage"
        ),
        max_price_cents=10_000,
        include_any=("misspelled existing phrase",),
        exclude=("wanted",),
    )
    listings = [
        _listing("1", "Away Carry-On Flex suitcase"),
        _listing("2", "Away CarryOn Flex luggage"),
        _listing("3", "Away carry on bag"),
        _listing("4", "Samsonite Carry On luggage"),
        _listing("5", "Wanted Away Carry-On"),
        _listing("6", "Away Carry-On", price_cents=20_000),
    ]

    report = suggest_exact_phrases(listings, search)

    assert report.analyzed_listings == 4
    assert report.suggestions[0].phrase == "away carry on"
    assert report.suggestions[0].matching_listings == 3
    assert report.suggestions[0].example_titles == (
        "Away Carry-On Flex suitcase",
        "Away CarryOn Flex luggage",
    )


def test_suggestions_treat_written_and_numeric_forms_as_equivalent() -> None:
    search = SearchConfig(
        name="Flair 58",
        url="https://www.facebook.com/marketplace/search?query=flair%2058",
    )
    listings = [
        Listing("1", "Flair 58 espresso maker", "url-1", search.name),
        Listing("2", "Flair fifty-eight Plus", "url-2", search.name),
    ]

    report = suggest_exact_phrases(listings, search)

    assert report.suggestions[0].phrase == "flair 58"
    assert report.suggestions[0].matching_listings == 2
