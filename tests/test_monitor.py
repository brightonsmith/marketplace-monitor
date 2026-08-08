import asyncio
from datetime import datetime
from pathlib import Path

import pytest

import marketplace_monitor.monitor as monitor_module
import marketplace_monitor.ranking as ranking_module
from marketplace_monitor.browser import BrowserSessionError
from marketplace_monitor.models import (
    AppConfig,
    BrowserConfig,
    Listing,
    NotificationConfig,
    QuietHoursConfig,
    SearchConfig,
    StatusUpdate,
)
from marketplace_monitor.notifier import Notifier
from marketplace_monitor.storage import ListingStore


class RecordingNotifier(Notifier):
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[str] = []
        self.statuses: list[tuple[StatusUpdate, bool]] = []
        self.errors: list[tuple[str, str]] = []

    def send(self, listing: Listing) -> None:
        if self.fail:
            raise RuntimeError("delivery failed")
        self.sent.append(listing.listing_id)

    def send_status(self, status: StatusUpdate, *, startup: bool = False) -> None:
        if self.fail:
            raise RuntimeError("delivery failed")
        self.statuses.append((status, startup))

    def send_error(self, title: str, message: str) -> None:
        if self.fail:
            raise RuntimeError("delivery failed")
        self.errors.append((title, message))


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


def test_newly_added_search_establishes_its_own_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    current = [listing("1")]

    async def fake_fetch(*_args):
        return current

    monkeypatch.setattr(monitor_module, "fetch_listings", fake_fetch)
    base = make_config(tmp_path)
    notifier = RecordingNotifier()
    asyncio.run(monitor_module.run_once(base, notifier))

    current[:] = [
        Listing("2", "Spider Putter", "https://example.com/2", "Spider", 20_000)
    ]
    expanded = AppConfig(
        browser=base.browser,
        database_path=base.database_path,
        check_interval_minutes=base.check_interval_minutes,
        notify_on_first_run=False,
        notifications=base.notifications,
        searches=(
            *base.searches,
            SearchConfig(
                name="Spider",
                url="https://www.facebook.com/marketplace/search/?query=spider",
                include_any=("spider",),
            ),
        ),
    )

    added = asyncio.run(monitor_module.run_once(expanded, notifier))
    assert added.new == 1
    assert added.notified == 0
    assert notifier.sent == []

    current.append(
        Listing("3", "Spider Putter", "https://example.com/3", "Spider", 19_000)
    )
    subsequent = asyncio.run(monitor_module.run_once(expanded, notifier))
    assert subsequent.notified == 1
    assert notifier.sent == ["3"]


def test_status_is_sent_after_quiet_interval_and_resets_timer(tmp_path: Path, monkeypatch) -> None:
    current = [listing("1")]

    async def fake_fetch(*_args):
        return current

    monkeypatch.setattr(monitor_module, "fetch_listings", fake_fetch)
    config = make_config(tmp_path)
    notifier = RecordingNotifier()
    summary = asyncio.run(monitor_module.run_once(config, notifier))

    unchanged = monitor_module.maybe_send_status(
        config,
        notifier,
        summary,
        last_notification_at=100.0,
        now=3_699.0,
    )
    assert unchanged == 100.0
    assert notifier.statuses == []

    reset = monitor_module.maybe_send_status(
        config,
        notifier,
        summary,
        last_notification_at=100.0,
        now=3_700.0,
    )
    assert reset == 3_700.0
    assert notifier.statuses == [(summary.status, False)]
    assert summary.status.listing == current[0]
    assert summary.status.is_exact_match


def test_status_chooses_closest_candidate_when_nothing_matches(
    tmp_path: Path, monkeypatch
) -> None:
    current = [
        Listing("1", "Unrelated grinder", "https://example.com/1", "Flair", 10_000),
        Listing("2", "Flair 58 Plus", "https://example.com/2", "Flair", 55_000),
    ]

    async def fake_fetch(*_args):
        return current

    monkeypatch.setattr(monitor_module, "fetch_listings", fake_fetch)
    base = make_config(tmp_path)
    config = AppConfig(
        browser=base.browser,
        database_path=base.database_path,
        check_interval_minutes=base.check_interval_minutes,
        notify_on_first_run=base.notify_on_first_run,
        notifications=base.notifications,
        searches=(
            SearchConfig(
                name="Flair",
                url="https://www.facebook.com/marketplace/search/?query=flair",
                max_price_cents=50_000,
                include_any=("flair 58",),
            ),
        ),
    )

    summary = asyncio.run(monitor_module.run_once(config, RecordingNotifier()))

    assert summary.matched == 0
    assert summary.status.listing == current[1]
    assert not summary.status.is_exact_match


def test_status_prioritizes_relevance_over_a_cheaper_unrelated_listing(
    tmp_path: Path, monkeypatch
) -> None:
    current = [
        Listing(
            "1",
            "DeLonghi Magnifica Coffee Machine",
            "https://example.com/1",
            "Flair 58 Plus",
            10_000,
        ),
        Listing(
            "2",
            "Flair58+ Manual Espresso Maker",
            "https://example.com/2",
            "Flair 58 Plus",
            55_000,
        ),
    ]

    async def fake_fetch(*_args):
        return current

    monkeypatch.setattr(monitor_module, "fetch_listings", fake_fetch)
    base = make_config(tmp_path)
    config = AppConfig(
        browser=base.browser,
        database_path=base.database_path,
        check_interval_minutes=base.check_interval_minutes,
        notify_on_first_run=base.notify_on_first_run,
        notifications=base.notifications,
        searches=(
            SearchConfig(
                name="Flair 58 Plus",
                url="https://www.facebook.com/marketplace/search/?query=flair%2058%20plus",
                max_price_cents=50_000,
                include_any=("flair 58 plus",),
            ),
        ),
    )

    summary = asyncio.run(monitor_module.run_once(config, RecordingNotifier()))

    assert summary.matched == 0
    assert summary.status.listing == current[1]


def test_status_omits_unrelated_candidate_when_relevance_is_too_low(
    tmp_path: Path, monkeypatch
) -> None:
    current = [
        Listing(
            "1",
            "Men's Genuine Leather Belt (58)",
            "https://example.com/1",
            "Flair 58 Plus",
            11_000,
        )
    ]

    async def fake_fetch(*_args):
        return current

    monkeypatch.setattr(monitor_module, "fetch_listings", fake_fetch)
    base = make_config(tmp_path)
    config = AppConfig(
        browser=base.browser,
        database_path=base.database_path,
        check_interval_minutes=base.check_interval_minutes,
        notify_on_first_run=base.notify_on_first_run,
        notifications=base.notifications,
        searches=(
            SearchConfig(
                name="Flair 58 Plus",
                url="https://www.facebook.com/marketplace/search/?query=flair%2058",
                max_price_cents=50_000,
                include_any=("flair 58 plus", "flair 58+"),
            ),
        ),
    )

    summary = asyncio.run(monitor_module.run_once(config, RecordingNotifier()))

    assert summary.matched == 0
    assert summary.status.listing is None


def test_status_uses_each_search_minimum_relevance(monkeypatch) -> None:
    candidate = Listing(
        "1",
        "Related but inexact product",
        "https://example.com/1",
        "Example",
        25_000,
    )
    monkeypatch.setattr(
        ranking_module,
        "listing_relevance_scores",
        lambda _listings, _search: {candidate.listing_id: 0.19},
    )

    strict = SearchConfig(
        name="Example",
        url="https://www.facebook.com/marketplace/search/?query=example",
    )
    permissive = SearchConfig(
        name="Example",
        url="https://www.facebook.com/marketplace/search/?query=example",
        minimum_relevance=0.15,
    )

    assert monitor_module._best_status_listing(
        [candidate], [], {strict.name: strict}
    ) == (None, False)
    assert monitor_module._best_status_listing(
        [candidate], [], {permissive.name: permissive}
    ) == (candidate, False)


def test_quiet_hours_hold_listing_until_window_ends(tmp_path: Path, monkeypatch) -> None:
    current = [listing("1")]

    async def fake_fetch(*_args):
        return current

    monkeypatch.setattr(monitor_module, "fetch_listings", fake_fetch)
    base = make_config(tmp_path)
    config = AppConfig(
        browser=base.browser,
        database_path=base.database_path,
        check_interval_minutes=base.check_interval_minutes,
        notify_on_first_run=True,
        notifications=base.notifications,
        searches=base.searches,
        quiet_hours=QuietHoursConfig(start_minutes=22 * 60, end_minutes=7 * 60),
    )
    notifier = RecordingNotifier()

    overnight = asyncio.run(
        monitor_module.run_once(config, notifier, now=datetime(2026, 8, 1, 23, 0))
    )
    assert overnight.notified == 0
    assert overnight.held == 1
    assert notifier.sent == []

    # A held listing is a real queue item, even if Marketplace stops returning it.
    current.clear()

    morning = asyncio.run(
        monitor_module.run_once(config, notifier, now=datetime(2026, 8, 2, 7, 0))
    )
    assert morning.new == 0
    assert morning.held == 0
    assert morning.notified == 1
    assert notifier.sent == ["1"]


def test_status_waits_during_quiet_hours(tmp_path: Path) -> None:
    base = make_config(tmp_path)
    config = AppConfig(
        browser=base.browser,
        database_path=base.database_path,
        check_interval_minutes=base.check_interval_minutes,
        notify_on_first_run=base.notify_on_first_run,
        notifications=base.notifications,
        searches=base.searches,
        quiet_hours=QuietHoursConfig(start_minutes=22 * 60, end_minutes=7 * 60),
    )
    notifier = RecordingNotifier()
    summary = monitor_module.RunSummary(
        discovered=0,
        matched=0,
        new=0,
        notified=0,
        held=0,
        status=StatusUpdate(0, 0, None, False),
    )

    last = monitor_module.maybe_send_status(
        config,
        notifier,
        summary,
        last_notification_at=0.0,
        now=3_600.0,
        wall_time=datetime(2026, 8, 1, 23, 0),
    )
    assert last == 0.0
    assert notifier.statuses == []


def test_startup_status_bypasses_quiet_hours(tmp_path: Path) -> None:
    base = make_config(tmp_path)
    config = AppConfig(
        browser=base.browser,
        database_path=base.database_path,
        check_interval_minutes=base.check_interval_minutes,
        notify_on_first_run=base.notify_on_first_run,
        notifications=base.notifications,
        searches=base.searches,
        quiet_hours=QuietHoursConfig(start_minutes=22 * 60, end_minutes=7 * 60),
    )
    status = StatusUpdate(60, 1, listing("1"), True)
    summary = monitor_module.RunSummary(60, 1, 0, 0, 0, status)
    notifier = RecordingNotifier()

    pending = monitor_module.maybe_send_startup_status(
        config,
        notifier,
        summary,
        pending=True,
    )
    assert not pending
    assert notifier.statuses == [(status, True)]


def test_watch_sends_one_authentication_alert_until_recovery(
    tmp_path: Path, monkeypatch
) -> None:
    async def expired_session(*_args, **_kwargs):
        raise BrowserSessionError("session expired")

    sleep_count = 0

    async def stop_after_two_cycles(_seconds):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(monitor_module, "run_once", expired_session)
    monkeypatch.setattr(monitor_module.asyncio, "sleep", stop_after_two_cycles)
    notifier = RecordingNotifier()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitor_module.watch(make_config(tmp_path), notifier))

    assert len(notifier.errors) == 1
    assert notifier.errors[0][0] == "Marketmon needs Facebook login"
    assert "Monitoring is paused" in notifier.errors[0][1]


def test_run_once_with_no_active_searches_does_not_open_browser(
    tmp_path: Path, monkeypatch
) -> None:
    async def fail_fetch(*_args):
        raise AssertionError("browser should not open")

    monkeypatch.setattr(monitor_module, "fetch_listings", fail_fetch)
    base = make_config(tmp_path)
    config = AppConfig(
        browser=base.browser,
        database_path=base.database_path,
        check_interval_minutes=base.check_interval_minutes,
        notify_on_first_run=base.notify_on_first_run,
        notifications=base.notifications,
        searches=(),
    )

    summary = asyncio.run(monitor_module.run_once(config, RecordingNotifier()))
    assert summary.discovered == 0
    assert summary.matched == 0


def test_dismissed_listing_is_omitted_from_status_and_notifications(
    tmp_path: Path, monkeypatch
) -> None:
    item = listing("1")

    async def fake_fetch(*_args):
        return [item]

    monkeypatch.setattr(monitor_module, "fetch_listings", fake_fetch)
    config = make_config(tmp_path)
    with ListingStore(config.database_path) as store:
        store.record(item)
        store.set_disposition(item.listing_id, "dismissed")

    summary = asyncio.run(monitor_module.run_once(config, RecordingNotifier()))

    assert summary.dismissed == 1
    assert summary.matched == 0
    assert summary.notified == 0
    assert summary.status.listing is None
