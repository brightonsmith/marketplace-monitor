from pathlib import Path

from marketplace_monitor.dashboard import create_app
from marketplace_monitor.models import Listing
from marketplace_monitor.ranking import RankedListing
from marketplace_monitor.storage import ListingStore


def _config(path: Path, database: Path) -> None:
    path.write_text(
        f"""
database_path: {database}
searches:
  - name: Flair 58 Plus
    url: https://www.facebook.com/marketplace/search/?query=flair%2058
    include_any: [flair 58]
""",
        encoding="utf-8",
    )


def test_dashboard_renders_rich_listing_and_updates_feedback(tmp_path: Path) -> None:
    database = tmp_path / "marketplace.db"
    config = tmp_path / "config.yaml"
    _config(config, database)
    item = Listing(
        "123",
        "Flair 58 Plus",
        "https://example.test/listing",
        "Flair 58 Plus",
        42_500,
        "Denver, CO",
        3.4,
        "https://example.test/image.jpg",
    )
    with ListingStore(database) as store:
        store.replace_dashboard_candidates(
            ("Flair 58 Plus",),
            [RankedListing(item, 0.95, 0.96, True, False)],
        )
        store.record_run(
            discovered=62,
            matched=1,
            new=1,
            notified=0,
            held=0,
            dismissed=0,
        )

    client = create_app(config).test_client()
    page = client.get("/")
    assert page.status_code == 200
    assert b"Flair 58 Plus" in page.data
    assert b"https://example.test/image.jpg" in page.data
    assert b"Recent monitoring runs" in page.data

    response = client.post(
        "/listings/123/feedback",
        data={"disposition": "interested", "view": "active", "limit": "10"},
    )
    assert response.status_code == 303
    assert response.headers["Location"] == "/listings/123"
    detail = client.get("/listings/123")
    assert detail.status_code == 200
    assert b"Flair 58 Plus" in detail.data
    assert b"Open Facebook" in detail.data
    assert b"https://example.test/listing" in detail.data
    interested = client.get("/?view=interested&limit=10")
    assert b"Flair 58 Plus" in interested.data

    dismissed = client.post(
        "/listings/123/feedback",
        data={"disposition": "dismissed", "view": "active", "limit": "10"},
        headers={"HX-Request": "true"},
    )
    assert dismissed.status_code == 204
    assert dismissed.headers["HX-Refresh"] == "true"
    assert b"<!doctype html>" not in dismissed.data


def test_interested_button_opens_listing_without_htmx(tmp_path: Path) -> None:
    database = tmp_path / "marketplace.db"
    config = tmp_path / "config.yaml"
    _config(config, database)
    item = Listing("123", "Flair", "https://example.test/123", "Flair 58 Plus")
    with ListingStore(database) as store:
        store.replace_dashboard_candidates(
            ("Flair 58 Plus",),
            [RankedListing(item, 0.9, 0.9, True, False)],
        )

    page = create_app(config).test_client().get("/")
    assert b'target="_blank"' in page.data
    assert b'hx-post="/listings/123/feedback"' in page.data


def test_dashboard_rejects_invalid_view_and_limit(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    _config(config, tmp_path / "marketplace.db")
    client = create_app(config).test_client()
    assert client.get("/?view=unknown").status_code == 404
    assert client.get("/?limit=11").status_code == 400
    assert client.get("/listings/does-not-exist").status_code == 404
