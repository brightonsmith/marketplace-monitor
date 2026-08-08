from pathlib import Path

from marketplace_monitor.models import Listing
from marketplace_monitor.storage import ListingStore


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
