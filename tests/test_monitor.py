import asyncio
from pathlib import Path

import pytest

import marketplace_monitor.monitor as monitor_module
from marketplace_monitor.models import (
    AppConfig,
    BrowserConfig,
    Listing,
    NotificationConfig,
    SearchConfig,
)
from marketplace_monitor.notifier import Notifier


class RecordingNotifier(Notifier):
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[str] = []

    def send(self, listing: Listing) -> None:
        if self.fail:
            raise RuntimeError("delivery failed")
        self.sent.append(listing.listing_id)


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        browser=BrowserConfig(profile_dir=tmp_path / "profile"),
        database_path=tmp_path / "listings.db",
        check_interval_minutes=10,
        notify_on_first_run=False,
        notifications=NotificationConfig(),
        searches=(
            SearchConfig(
                name="Flair",
                url="https://www.facebook.com/marketplace/search/?query=flair",
            ),
        ),
    )


def listing(listing_id: str) -> Listing:
    return Listing(
        listing_id=listing_id,
        title="Flair 58 Plus",
        url=f"https://www.facebook.com/marketplace/item/{listing_id}",
        search_name="Flair",
        price_cents=42_500,
    )


def test_baseline_then_notify_and_retry_failed_delivery(tmp_path: Path, monkeypatch) -> None:
    current = [listing("1")]

    async def fake_fetch(*_args):
        return current

    monkeypatch.setattr(monitor_module, "fetch_listings", fake_fetch)
    config = make_config(tmp_path)
    notifier = RecordingNotifier()

    baseline = asyncio.run(monitor_module.run_once(config, notifier))
    assert baseline.new == 1
    assert baseline.notified == 0

    current.append(listing("2"))
    second = asyncio.run(monitor_module.run_once(config, notifier))
    assert second.new == 1
    assert second.notified == 1
    assert notifier.sent == ["2"]

    current.append(listing("3"))
    with pytest.raises(RuntimeError, match="delivery failed"):
        asyncio.run(monitor_module.run_once(config, RecordingNotifier(fail=True)))

    retry = asyncio.run(monitor_module.run_once(config, notifier))
    assert retry.new == 0
    assert retry.notified == 1
    assert notifier.sent == ["2", "3"]

