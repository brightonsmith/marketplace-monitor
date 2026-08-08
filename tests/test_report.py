from marketplace_monitor.models import Listing, SearchConfig
from marketplace_monitor.report import format_report


def test_report_formats_ranked_matches_and_limits_results() -> None:
    search = SearchConfig(
        name="Flair 58 Plus",
        url="https://www.facebook.com/marketplace/search/?query=flair%2058",
        max_price_cents=50_000,
        include_any=("flair 58 plus", "flair 58+"),
    )
    listings = [
        Listing(
            "1",
            "Flair 58 Plus Espresso Maker",
            "https://example.com/1",
            search.name,
            45_000,
            "Denver, CO",
        ),
        Listing(
            "2",
            "Breville Bambino Plus",
            "https://example.com/2",
            search.name,
            30_000,
            "Aurora, CO",
        ),
    ]

    report = format_report(listings, (search,), limit=1)

    assert "2 listings · top 1 per search" in report
    assert "Flair 58 Plus · 2 found · showing 1" in report
    assert "% match" in report
    assert "% score" in report
    assert "exact" in report
    assert "Flair 58 Plus Espresso Maker" in report
    assert "https://example.com/1" in report
    assert "Breville" not in report


def test_report_applies_limit_separately_to_each_search() -> None:
    first = SearchConfig("First", "https://facebook.com/marketplace/first")
    second = SearchConfig("Second", "https://facebook.com/marketplace/second")
    listings = [
        Listing("1", "First", "https://example/1", "First"),
        Listing("2", "Another First", "https://example/2", "First"),
        Listing("3", "Second", "https://example/3", "Second"),
        Listing("4", "Another Second", "https://example/4", "Second"),
    ]

    report = format_report(listings, (first, second), limit=1)

    assert "First · 2 found · showing 1" in report
    assert "Second · 2 found · showing 1" in report
    assert report.count("ID:") == 2


def test_report_omits_excluded_listings() -> None:
    search = SearchConfig(
        name="Camera",
        url="https://www.facebook.com/marketplace/search/?query=camera",
        exclude=("wanted",),
    )
    listing = Listing(
        "1",
        "Wanted camera",
        "https://example.com/1",
        search.name,
    )

    report = format_report([listing], (search,), limit=10)
    assert "showing 0" in report
    assert "No reportable listings found" in report
