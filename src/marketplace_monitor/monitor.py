from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime

from .browser import fetch_listings
from .models import AppConfig, Listing, QuietHoursConfig, SearchConfig, StatusUpdate
from .notifier import Notifier
from .parser import matches_search
from .storage import ListingStore


@dataclass(frozen=True)
class RunSummary:
    discovered: int
    matched: int
    new: int
    notified: int
    held: int
    status: StatusUpdate


def quiet_hours_active(quiet_hours: QuietHoursConfig | None, at: datetime) -> bool:
    if quiet_hours is None:
        return False
    current = at.hour * 60 + at.minute
    start = quiet_hours.start_minutes
    end = quiet_hours.end_minutes
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _price_distance(listing: Listing, search: SearchConfig) -> int:
    if listing.price_cents is None:
        return 10**15
    if search.min_price_cents is not None and listing.price_cents < search.min_price_cents:
        return search.min_price_cents - listing.price_cents
    if search.max_price_cents is not None and listing.price_cents > search.max_price_cents:
        return listing.price_cents - search.max_price_cents
    return 0


def _best_status_listing(
    listings: list[Listing],
    matched: list[Listing],
    searches: dict[str, SearchConfig],
) -> tuple[Listing | None, bool]:
    if matched:
        best = min(
            matched,
            key=lambda item: (
                item.price_cents is None,
                item.price_cents if item.price_cents is not None else 0,
                item.title.casefold(),
            ),
        )
        return best, True
    if not listings:
        return None, False

    def near_match_key(item: Listing) -> tuple[int, int, int, int, str]:
        search = searches[item.search_name]
        title = item.title.casefold()
        include_hits = sum(term in title for term in search.include_any)
        exclude_hits = sum(term in title for term in search.exclude)
        return (
            -include_hits,
            exclude_hits,
            _price_distance(item, search),
            item.price_cents if item.price_cents is not None else 10**15,
            title,
        )

    return min(listings, key=near_match_key), False


async def run_once(
    config: AppConfig,
    notifier: Notifier,
    *,
    now: datetime | None = None,
) -> RunSummary:
    quiet = quiet_hours_active(config.quiet_hours, now or datetime.now())
    listings = await fetch_listings(config.browser, config.searches)
    searches = {search.name: search for search in config.searches}
    matched: list[Listing] = [
        listing
        for listing in listings
        if matches_search(listing, searches[listing.search_name])
    ]
    best_listing, is_exact_match = _best_status_listing(listings, matched, searches)

    new_count = 0
    notified_count = 0
    held_count = 0
    with ListingStore(config.database_path) as store:
        baseline = not store.is_initialized() and not config.notify_on_first_run
        for listing in matched:
            is_new = store.record(listing)
            if is_new:
                new_count += 1
            if baseline:
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
        store.mark_initialized()

    return RunSummary(
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
    )


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
    wall_time: datetime,
) -> bool:
    if not pending or not config.notify_on_startup:
        return False
    if quiet_hours_active(config.quiet_hours, wall_time):
        return True
    notifier.send_status(summary.status, startup=True)
    return False


async def watch(config: AppConfig, notifier: Notifier) -> None:
    last_notification_at = time.monotonic()
    startup_status_pending = config.notify_on_startup
    while True:
        try:
            wall_time = datetime.now()
            summary = await run_once(config, notifier, now=wall_time)
            check_completed_at = time.monotonic()
            was_startup_pending = startup_status_pending
            startup_status_pending = maybe_send_startup_status(
                config,
                notifier,
                summary,
                pending=startup_status_pending,
                wall_time=wall_time,
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
                f"{summary.notified} notified, {summary.held} held"
            )
        except Exception as error:
            print(f"Check failed: {error}")
        await asyncio.sleep(config.check_interval_minutes * 60)
