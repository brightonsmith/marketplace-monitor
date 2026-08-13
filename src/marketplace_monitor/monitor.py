from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .browser import BrowserSessionError, fetch_listings
from .geocoding import DistanceFilter
from .models import AppConfig, Listing, QuietHoursConfig, SearchConfig, StatusUpdate
from .notifier import Notifier
from .parser import matches_search
from .ranking import rank_listings
from .storage import ListingStore


@dataclass(frozen=True)
class RunSummary:
    discovered: int
    matched: int
    new: int
    notified: int
    held: int
    status: StatusUpdate
    dismissed: int = 0


def quiet_hours_active(quiet_hours: QuietHoursConfig | None, at: datetime) -> bool:
    if quiet_hours is None:
        return False
    current = at.hour * 60 + at.minute
    start = quiet_hours.start_minutes
    end = quiet_hours.end_minutes
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _best_status_listing(
    listings: list[Listing],
    matched: list[Listing],
    searches: dict[str, SearchConfig],
) -> tuple[Listing | None, bool]:
    candidates = matched or listings
    if not candidates:
        return None, False

    best_candidate = rank_listings(candidates, searches)[0]
    best = best_candidate.listing
    if not matched:
        search = searches[best.search_name]
        if best_candidate.relevance < search.minimum_relevance:
            return None, False
    return best, bool(matched)


async def run_once(
    config: AppConfig,
    notifier: Notifier,
    *,
    now: datetime | None = None,
) -> RunSummary:
    quiet = quiet_hours_active(config.quiet_hours, now or datetime.now())
    distance_filter = (
        DistanceFilter(config.database_path)
        if any(search.max_distance_miles is not None for search in config.searches)
        else None
    )
    try:
        if not config.searches:
            listings = []
        elif distance_filter is None:
            listings = await fetch_listings(config.browser, config.searches)
        else:
            listings = await fetch_listings(
                config.browser,
                config.searches,
                distance_filter=distance_filter,
            )
    finally:
        if distance_filter is not None:
            distance_filter.close()
    searches = {search.name: search for search in config.searches}

    new_count = 0
    notified_count = 0
    held_count = 0
    dismissed_count = 0
    with ListingStore(config.database_path) as store:
        dismissed_ids = store.dismissed_listing_ids()
        visible_listings = [
            listing for listing in listings if listing.listing_id not in dismissed_ids
        ]
        dismissed_count = len(listings) - len(visible_listings)
        matched: list[Listing] = [
            listing
            for listing in visible_listings
            if matches_search(listing, searches[listing.search_name])
        ]
        ranked = [
            candidate
            for candidate in rank_listings(visible_listings, searches)
            if not candidate.excluded
        ]
        best_listing, is_exact_match = _best_status_listing(
            visible_listings,
            matched,
            searches,
        )
        search_names = tuple(search.name for search in config.searches)
        # Persist the dashboard snapshot before sending alerts. A user can tap an
        # ntfy notification immediately, so the direct listing route must exist
        # before notification delivery begins.
        store.replace_dashboard_candidates(search_names, ranked)
        store.prepare_search_baselines(search_names)
        baseline_searches = {
            search.name
            for search in config.searches
            if not store.is_search_initialized(search.name)
            and not config.notify_on_first_run
        }
        for listing in matched:
            is_new = store.record(listing)
            if is_new:
                new_count += 1
            if listing.search_name in baseline_searches:
                store.mark_notified(listing.listing_id)
                continue
            if not store.needs_notification(listing.listing_id):
                continue
            if quiet:
                continue
            notifier.send(listing)
            store.mark_notified(listing.listing_id)
            notified_count += 1
        if quiet:
            held_count = len(store.pending_listings())
        else:
            for listing in store.pending_listings():
                notifier.send(listing)
                store.mark_notified(listing.listing_id)
                notified_count += 1
        for search_name in search_names:
            store.mark_search_initialized(search_name)
        store.mark_initialized()

    summary = RunSummary(
        discovered=len(listings),
        matched=len(matched),
        new=new_count,
        notified=notified_count,
        held=held_count,
        status=StatusUpdate(
            discovered=len(listings),
            matched=len(matched),
            listing=best_listing,
            is_exact_match=is_exact_match,
        ),
        dismissed=dismissed_count,
    )
    with ListingStore(config.database_path) as store:
        store.record_run(
            discovered=summary.discovered,
            matched=summary.matched,
            new=summary.new,
            notified=summary.notified,
            held=summary.held,
            dismissed=summary.dismissed,
        )
    return summary


def maybe_send_status(
    config: AppConfig,
    notifier: Notifier,
    summary: RunSummary,
    *,
    last_notification_at: float,
    now: float,
    wall_time: datetime | None = None,
) -> float:
    if summary.notified:
        return now
    if quiet_hours_active(config.quiet_hours, wall_time or datetime.now()):
        return last_notification_at
    if config.status_interval_minutes == 0:
        return last_notification_at
    interval_seconds = config.status_interval_minutes * 60
    if now - last_notification_at < interval_seconds:
        return last_notification_at
    notifier.send_status(summary.status)
    return now


def maybe_send_startup_status(
    config: AppConfig,
    notifier: Notifier,
    summary: RunSummary,
    *,
    pending: bool,
) -> bool:
    if not pending or not config.notify_on_startup:
        return False
    notifier.send_status(summary.status, startup=True)
    return False


def send_authentication_alert(notifier: Notifier, error: BrowserSessionError) -> None:
    notifier.send_error(
        "Marketmon needs Facebook login",
        f"{error}\nMonitoring is paused until the Facebook session is restored.",
    )


async def watch(
    config: AppConfig,
    notifier: Notifier,
    *,
    config_loader: Callable[[], AppConfig] | None = None,
) -> None:
    last_notification_at = time.monotonic()
    startup_status_pending = config.notify_on_startup
    authentication_alert_sent = False
    while True:
        try:
            if config_loader is not None:
                config = config_loader()
            wall_time = datetime.now()
            summary = await run_once(config, notifier, now=wall_time)
            authentication_alert_sent = False
            check_completed_at = time.monotonic()
            was_startup_pending = startup_status_pending
            startup_status_pending = maybe_send_startup_status(
                config,
                notifier,
                summary,
                pending=startup_status_pending,
            )
            if was_startup_pending and not startup_status_pending:
                last_notification_at = check_completed_at
            else:
                last_notification_at = maybe_send_status(
                    config,
                    notifier,
                    summary,
                    last_notification_at=last_notification_at,
                    now=check_completed_at,
                    wall_time=wall_time,
                )
            print(
                f"Check complete: {summary.discovered} discovered, "
                f"{summary.matched} matched, {summary.new} new, "
                f"{summary.notified} notified, {summary.held} held, "
                f"{summary.dismissed} dismissed"
            )
        except BrowserSessionError as error:
            print(f"Authentication failed: {error}")
            if not authentication_alert_sent:
                try:
                    send_authentication_alert(notifier, error)
                except Exception as notification_error:
                    print(f"Authentication alert failed: {notification_error}")
                else:
                    authentication_alert_sent = True
        except Exception as error:
            print(f"Check failed: {error}")
        await asyncio.sleep(config.check_interval_minutes * 60)
