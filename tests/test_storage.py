from pathlib import Path

from marketplace_monitor.models import Listing
from marketplace_monitor.storage import ListingStore
from marketplace_monitor.ranking import RankedListing


def test_store_reports_listing_as_new_only_once(tmp_path: Path) -> None:
    listing = Listing(
        listing_id="123",
        title="Flair 58 Plus",
        url="https://www.facebook.com/marketplace/item/123",
        search_name="Flair",
        price_cents=42_500,
    )
    with ListingStore(tmp_path / "listings.db") as store:
        assert not store.is_initialized()
        assert store.record(listing)
        assert store.needs_notification(listing.listing_id)
        assert store.pending_listings() == [listing]
        assert not store.record(listing)
        store.mark_notified(listing.listing_id)
        assert not store.needs_notification(listing.listing_id)
        assert store.pending_listings() == []
        store.mark_initialized()
        assert store.is_initialized()


def test_store_tracks_initialization_and_pending_items_per_search(tmp_path: Path) -> None:
    first = Listing("1", "First", "https://example/1", "Flair")
    second = Listing("2", "Second", "https://example/2", "Spider putter")
    with ListingStore(tmp_path / "listings.db") as store:
        store.prepare_search_baselines(())
        assert not store.is_search_initialized("Flair")
        store.mark_search_initialized("Flair")
        assert store.is_search_initialized("flair")
        assert store.record(first)
        assert store.record(second)
        assert store.cancel_pending_search("FLAIR") == 1
        assert store.pending_listings() == [second]


def test_store_persists_listing_disposition_and_cancels_dismissed_pending(
    tmp_path: Path,
) -> None:
    item = Listing("1", "First", "https://example/1", "Flair")
    path = tmp_path / "listings.db"
    with ListingStore(path) as store:
        store.record(item)
        store.set_disposition("1", "dismissed")
        assert store.pending_listings() == []
        assert store.dismissed_listing_ids() == {"1"}

    with ListingStore(path) as store:
        assert store.dismissed_listing_ids() == {"1"}
        store.set_disposition("1", None)
        assert store.dismissed_listing_ids() == set()


def test_store_accepts_feedback_for_candidate_not_in_notification_history(
    tmp_path: Path,
) -> None:
    with ListingStore(tmp_path / "listings.db") as store:
        store.set_disposition("candidate-only", "dismissed")
        assert store.dismissed_listing_ids() == {"candidate-only"}


def test_dashboard_snapshot_is_separate_and_keeps_images_and_feedback(
    tmp_path: Path,
) -> None:
    item = Listing(
        "candidate-only",
        "Flair 58 Plus",
        "https://example.test/1",
        "Flair",
        42_500,
        "Denver, CO",
        4.2,
        "https://example.test/image.jpg",
    )
    ranked = RankedListing(item, 0.91, 0.92, True, False)
    with ListingStore(tmp_path / "listings.db") as store:
        store.replace_dashboard_candidates(("Flair",), [ranked])
        assert not store.needs_notification(item.listing_id)
        active = store.dashboard_listings()
        assert active[0].listing.image_url == "https://example.test/image.jpg"
        assert active[0].relevance == 0.91

        store.set_disposition(item.listing_id, "interested")
        assert store.disposition_counts() == {
            "active": 1,
            "interested": 1,
            "dismissed": 0,
        }
        assert store.dashboard_listings("interested")[0].listing == item

        store.replace_dashboard_candidates(("Flair",), [])
        interested = store.dashboard_listings("interested")
        assert interested[0].listing == item
        assert not interested[0].is_current

        refreshed = Listing(
            "candidate-only",
            "Flair 58 Plus - reduced",
            "https://example.test/1",
            "Flair",
            40_000,
            "Denver, CO",
            4.2,
            "https://example.test/new-image.jpg",
        )
        store.replace_dashboard_candidates(
            ("Flair",),
            [RankedListing(refreshed, 0.93, 0.94, True, False)],
        )
        interested = store.dashboard_listings("interested")
        assert interested[0].listing == refreshed
        assert interested[0].is_current


def test_store_records_monitoring_diagnostics(tmp_path: Path) -> None:
    with ListingStore(tmp_path / "listings.db") as store:
        store.record_run(
            discovered=62,
            matched=1,
            new=1,
            notified=0,
            held=0,
            dismissed=2,
        )
        run = store.recent_runs()[0]
        assert (run.discovered, run.matched, run.new, run.dismissed) == (62, 1, 1, 2)
