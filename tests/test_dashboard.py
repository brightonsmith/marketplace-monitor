from pathlib import Path

from marketplace_monitor.dashboard import create_app
from marketplace_monitor.config import load_config
from marketplace_monitor.models import Listing
from marketplace_monitor.ranking import RankedListing
from marketplace_monitor.storage import ListingStore
from marketplace_monitor.suggestions import PhraseSuggestion, PhraseSuggestionReport


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
    assert b"Monitoring history" in page.data
    assert b"Monitoring active" in page.data
    assert b"cdn.jsdelivr.net" not in page.data
    assert b"unpkg.com" not in page.data

    response = client.post(
        "/listings/123/feedback",
        data={"disposition": "interested", "view": "active", "limit": "10"},
    )
    assert response.status_code == 303
    assert response.headers["Location"] == "https://example.test/listing"
    detail = client.get("/listings/123")
    assert detail.status_code == 200
    assert b"Flair 58 Plus" in detail.data
    assert b"Open on Facebook" in detail.data
    interested = client.get("/?view=interested&limit=10")
    assert b"Flair 58 Plus" in interested.data

    dismissed = client.post(
        "/listings/123/feedback",
        data={"disposition": "dismissed", "view": "active", "limit": "10"},
    )
    assert dismissed.status_code == 303
    assert dismissed.headers["Location"] == "/?view=active&limit=10"


def test_interested_button_uses_normal_form_without_popup_or_htmx(
    tmp_path: Path,
) -> None:
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
    assert b'target="_blank"' not in page.data
    assert b"hx-post" not in page.data
    assert b'action="/listings/123/feedback"' in page.data


def test_dashboard_status_and_installable_assets(tmp_path: Path) -> None:
    database = tmp_path / "marketplace.db"
    config = tmp_path / "config.yaml"
    _config(config, database)
    with ListingStore(database) as store:
        store.record_run(
            discovered=62,
            matched=1,
            new=1,
            notified=1,
            held=0,
            dismissed=2,
        )

    client = create_app(config).test_client()
    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json["latest_run"]["discovered"] == 62
    assert status.json["latest_run"]["matched"] == 1
    assert status.json["dashboard_updated_utc"] is None
    assert status.headers["Cache-Control"] == "no-store"

    health = client.get("/healthz")
    assert health.json["status"] == "ok"
    assert health.json["latest_run_utc"] is not None

    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.json["display"] == "standalone"

    worker = client.get("/service-worker.js")
    assert worker.status_code == 200
    assert worker.headers["Service-Worker-Allowed"] == "/"
    assert worker.headers["Cache-Control"] == "no-cache"


def test_dashboard_adds_private_app_security_headers(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    _config(config, tmp_path / "marketplace.db")

    response = create_app(config).test_client().get("/")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_dashboard_rejects_invalid_view_and_limit(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    _config(config, tmp_path / "marketplace.db")
    client = create_app(config).test_client()
    assert client.get("/?view=unknown").status_code == 404
    assert client.get("/?limit=11").status_code == 400
    assert client.get("/listings/does-not-exist").status_code == 404


def test_dashboard_lists_and_edits_search_configuration(tmp_path: Path) -> None:
    database = tmp_path / "marketplace.db"
    config = tmp_path / "config.yaml"
    _config(config, database)
    app = create_app(config)
    client = app.test_client()

    listing_page = client.get("/")
    assert b"Search configuration" in listing_page.data

    searches_page = client.get("/searches")
    assert searches_page.status_code == 200
    assert b"Flair 58 Plus" in searches_page.data
    assert b"flair 58" in searches_page.data
    assert b"Open Facebook search" in searches_page.data

    edit_page = client.get("/searches/Flair%2058%20Plus/edit")
    assert edit_page.status_code == 200
    assert b"Changes apply automatically on the next monitoring cycle" in edit_page.data

    response = client.post(
        "/searches/Flair%2058%20Plus/edit",
        data={
            "csrf_token": app.config["MARKETMON_CSRF_TOKEN"],
            "name": "Flair 58 and 58 Plus",
            "url": "https://www.facebook.com/marketplace/search/?query=flair%2058",
            "min_price": "100",
            "max_price": "500",
            "include_any": "Flair 58\nFlair58 Plus",
            "exclude": "wanted, broken",
            "minimum_relevance": "0.3",
            "max_distance_miles": "40",
        },
    )

    assert response.status_code == 303
    assert response.headers["Location"].startswith("/searches?saved=")
    updated = load_config(config).searches[0]
    assert updated.name == "Flair 58 and 58 Plus"
    assert updated.min_price_cents == 10_000
    assert updated.max_price_cents == 50_000
    assert updated.include_any == ("flair 58", "flair58 plus")
    assert updated.exclude == ("wanted", "broken")
    assert updated.minimum_relevance == 0.3
    assert updated.max_distance_miles == 40


def test_dashboard_rejects_invalid_or_forged_search_edits(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    _config(config, tmp_path / "marketplace.db")
    app = create_app(config)
    client = app.test_client()
    path = "/searches/Flair%2058%20Plus/edit"

    forged = client.post(path, data={"csrf_token": "wrong"})
    assert forged.status_code == 400

    invalid = client.post(
        path,
        data={
            "csrf_token": app.config["MARKETMON_CSRF_TOKEN"],
            "name": "Flair 58 Plus",
            "url": "https://www.facebook.com/marketplace/search/?query=flair",
            "min_price": "not-a-price",
            "max_price": "",
            "include_any": "flair 58",
            "exclude": "",
            "minimum_relevance": "0.2",
            "max_distance_miles": "",
        },
    )
    assert invalid.status_code == 400
    assert b"Minimum price must be a number or left blank" in invalid.data
    assert load_config(config).searches[0].min_price_cents is None


def test_dashboard_analyzes_live_titles_without_saving(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.yaml"
    _config(config, tmp_path / "marketplace.db")
    received = []

    async def fake_suggestions(app_config, search, *, limit=8):
        received.append((app_config, search, limit))
        return PhraseSuggestionReport(
            analyzed_listings=14,
            suggestions=(
                PhraseSuggestion(
                    "flair 58 plus",
                    6,
                    ("Flair58+ manual espresso maker",),
                ),
            ),
        )

    monkeypatch.setattr(
        "marketplace_monitor.dashboard.fetch_phrase_suggestions",
        fake_suggestions,
    )
    app = create_app(config)
    client = app.test_client()
    response = client.post(
        "/searches/Flair%2058%20Plus/edit",
        data={
            "csrf_token": app.config["MARKETMON_CSRF_TOKEN"],
            "action": "suggest",
            "name": "Flair 58 Plus",
            "url": "https://www.facebook.com/marketplace/search/?query=flair%2058",
            "min_price": "",
            "max_price": "",
            "include_any": "",
            "exclude": "wanted",
            "minimum_relevance": "0.2",
            "max_distance_miles": "",
        },
    )

    assert response.status_code == 200
    assert b"14</strong> listings analyzed" in response.data
    assert b"flair 58 plus" in response.data
    assert b"Flair58+ manual espresso maker" in response.data
    assert b"data-add-suggestions" in response.data
    assert received[0][1].include_any == ()
    assert load_config(config).searches[0].include_any == ("flair 58",)
