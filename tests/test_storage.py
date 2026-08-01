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
        assert not store.record(listing)
        store.mark_notified(listing.listing_id)
        assert not store.needs_notification(listing.listing_id)
        store.mark_initialized()
        assert store.is_initialized()
