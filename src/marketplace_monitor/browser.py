from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

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


async def fetch_listings(
    config: BrowserConfig,
    searches: tuple[SearchConfig, ...],
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
            for _ in range(config.scroll_count):
                await page.mouse.wheel(0, 1800)
                await page.wait_for_timeout(1_000)
            cards = await _extract_cards(page)
            for card in cards:
                listing = listing_from_card(card, search)
                if listing is not None:
                    listings.setdefault(listing.listing_id, listing)
    finally:
        await context.close()
        await playwright.stop()
    return list(listings.values())
