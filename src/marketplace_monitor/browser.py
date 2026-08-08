from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .geocoding import DistanceFilter, GeocodingError
from .models import BrowserConfig, Listing, SearchConfig
from .parser import listing_from_card

CARD_SCRIPT = """
anchors => anchors.map(anchor => {
  let node = anchor;
  let bestText = anchor.innerText || anchor.textContent || "";
  for (let depth = 0; depth < 4 && node.parentElement; depth += 1) {
    node = node.parentElement;
    const candidate = node.innerText || node.textContent || "";
    const lines = candidate.split("\\n").filter(line => line.trim());
    if (lines.length >= 2 && lines.length <= 10 && candidate.length < 600) {
      bestText = candidate;
    }
  }
  return { href: anchor.href, text: bestText };
})
"""

SEARCH_ORIGIN_SCRIPT = """
() => {
  const pattern = /^(.+?)\\s*[·•]\\s*Within\\s+\\d+(?:\\.\\d+)?\\s*(?:mi|miles)\\b/i;
  const candidates = document.querySelectorAll('button, [role="button"], span');
  for (const element of candidates) {
    const text = (element.innerText || element.textContent || "")
      .replace(/\\s+/g, " ")
      .trim();
    const match = text.match(pattern);
    if (match && element.getClientRects().length) {
      return match[1].trim();
    }
  }
  return null;
}
"""


class BrowserSessionError(RuntimeError):
    """Raised when the saved Facebook browser session cannot access Marketplace."""


LOGIN_PATH_MARKERS = (
    "/login",
    "/checkpoint",
    "/recover",
    "/two_step_verification",
)


async def _ensure_authenticated(page: Page) -> None:
    path = urlsplit(page.url).path.casefold()
    login_controls = page.locator(
        'input[name="email"], input[name="pass"], form[action*="/login"]'
    )
    login_control_visible = bool(await login_controls.count()) and await (
        login_controls.first.is_visible()
    )
    if any(marker in path for marker in LOGIN_PATH_MARKERS) or login_control_visible:
        raise BrowserSessionError(
            "The saved Facebook session expired or requires verification. "
            "Run 'marketmon login' from a graphical desktop to sign in again."
        )


async def _open_context(
    config: BrowserConfig,
    headless: bool | None = None,
) -> tuple[object, BrowserContext]:
    config.profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(config.profile_dir.resolve()),
        headless=config.headless if headless is None else headless,
        viewport={"width": 1400, "height": 1000},
    )
    context.set_default_timeout(config.page_load_timeout_seconds * 1000)
    return playwright, context


async def interactive_login(config: BrowserConfig) -> None:
    playwright, context = await _open_context(config, headless=False)
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded")
        print("Log in to Facebook in the browser window.")
        await asyncio.to_thread(
            input,
            "When Marketplace is visible, press Enter here to save the session...",
        )
        await _ensure_authenticated(page)
        print("Facebook session saved and verified.")
    finally:
        await context.close()
        await playwright.stop()


async def verify_session(config: BrowserConfig) -> None:
    playwright, context = await _open_context(config)
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(
            "https://www.facebook.com/marketplace/",
            wait_until="domcontentloaded",
        )
        await _ensure_authenticated(page)
    finally:
        await context.close()
        await playwright.stop()


async def _extract_cards(page: Page) -> list[dict[str, str]]:
    locator = page.locator('a[href*="/marketplace/item/"]')
    try:
        await locator.first.wait_for(state="attached", timeout=15_000)
    except PlaywrightTimeoutError:
        return []
    return await locator.evaluate_all(CARD_SCRIPT)


async def _extract_search_origin(page: Page) -> str | None:
    for _ in range(20):
        origin = await page.evaluate(SEARCH_ORIGIN_SCRIPT)
        if origin:
            return origin
        await page.wait_for_timeout(500)
    return None


async def fetch_listings(
    config: BrowserConfig,
    searches: tuple[SearchConfig, ...],
    *,
    distance_filter: DistanceFilter | None = None,
    pre_distance_filter: Callable[[Listing, SearchConfig], bool] | None = None,
) -> list[Listing]:
    if not searches:
        return []
    playwright, context = await _open_context(config)
    listings: dict[str, Listing] = {}
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        for search in searches:
            await page.goto(search.url, wait_until="domcontentloaded")
            await _ensure_authenticated(page)
            origin_location = None
            if search.max_distance_miles is not None:
                if distance_filter is None:
                    raise GeocodingError(
                        "A distance filter is required when max_distance_miles is set"
                    )
                origin_location = await _extract_search_origin(page)
                if not origin_location:
                    raise GeocodingError(
                        f"Could not determine the Facebook search location for "
                        f"'{search.name}'"
                    )
            for _ in range(config.scroll_count):
                await page.mouse.wheel(0, 1800)
                await page.wait_for_timeout(1_000)
            cards = await _extract_cards(page)
            for card in cards:
                listing = listing_from_card(card, search)
                if listing is None:
                    continue
                if pre_distance_filter is not None and not pre_distance_filter(
                    listing, search
                ):
                    continue
                if search.max_distance_miles is not None:
                    if not listing.location:
                        continue
                    distance = await distance_filter.distance_between(
                        origin_location,
                        listing.location,
                    )
                    if distance is None or distance > search.max_distance_miles:
                        continue
                    listing = replace(listing, distance_miles=distance)
                listings.setdefault(listing.listing_id, listing)
    finally:
        await context.close()
        await playwright.stop()
    return list(listings.values())
