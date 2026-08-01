from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .browser import fetch_listings
from .models import AppConfig, Listing
from .notifier import Notifier
from .parser import matches_search
from .storage import ListingStore


@dataclass(frozen=True)
class RunSummary:
    discovered: int
    matched: int
    new: int
    notified: int


async def run_once(config: AppConfig, notifier: Notifier) -> RunSummary:
    listings = await fetch_listings(config.browser, config.searches)
    searches = {search.name: search for search in config.searches}
    matched: list[Listing] = [
        listing
        for listing in listings
        if matches_search(listing, searches[listing.search_name])
    ]

    new_count = 0
    notified_count = 0
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
            notifier.send(listing)
            store.mark_notified(listing.listing_id)
            notified_count += 1
        store.mark_initialized()

    return RunSummary(
        discovered=len(listings),
        matched=len(matched),
        new=new_count,
        notified=notified_count,
    )


async def watch(config: AppConfig, notifier: Notifier) -> None:
    while True:
        try:
            summary = await run_once(config, notifier)
            print(
                f"Check complete: {summary.discovered} discovered, "
                f"{summary.matched} matched, {summary.new} new, "
                f"{summary.notified} notified"
            )
        except Exception as error:
            print(f"Check failed: {error}")
        await asyncio.sleep(config.check_interval_minutes * 60)
