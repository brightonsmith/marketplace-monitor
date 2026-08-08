import asyncio

import pytest

import marketplace_monitor.browser as browser_module
from marketplace_monitor.browser import (
    BrowserSessionError,
    _ensure_authenticated,
    _extract_search_origin,
)


class FakeLocator:
    def __init__(self, count: int, visible: bool):
        self._count = count
        self._visible = visible

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible


class FakePage:
    def __init__(self, url: str, login_controls: int = 0, visible: bool = True):
        self.url = url
        self.login_controls = login_controls
        self.visible = visible

    def locator(self, _selector: str) -> FakeLocator:
        return FakeLocator(self.login_controls, self.visible)

    async def goto(self, url: str, *, wait_until: str) -> None:
        self.url = url


class FakeOriginPage:
    def __init__(self, values: list[str | None]):
        self.values = iter(values)
        self.waits: list[int] = []

    async def evaluate(self, _script: str) -> str | None:
        return next(self.values)

    async def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


class FakeContext:
    def __init__(self, page: FakePage):
        self.pages = [page]
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self):
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


def test_authentication_check_accepts_marketplace_page() -> None:
    asyncio.run(
        _ensure_authenticated(
            FakePage("https://www.facebook.com/marketplace/denver/search")
        )
    )


def test_search_origin_waits_for_facebook_location_control() -> None:
    page = FakeOriginPage([None, "Denver, Colorado"])

    assert asyncio.run(_extract_search_origin(page)) == "Denver, Colorado"
    assert page.waits == [500]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/login/?next=/marketplace/",
        "https://www.facebook.com/checkpoint/",
        "https://www.facebook.com/two_step_verification/",
    ],
)
def test_authentication_check_detects_login_flows(url: str) -> None:
    with pytest.raises(BrowserSessionError, match="saved Facebook session"):
        asyncio.run(_ensure_authenticated(FakePage(url)))


def test_authentication_check_detects_login_form_overlay() -> None:
    with pytest.raises(BrowserSessionError):
        asyncio.run(
            _ensure_authenticated(
                FakePage("https://www.facebook.com/marketplace/", login_controls=1)
            )
        )


def test_authentication_check_ignores_hidden_login_controls() -> None:
    asyncio.run(
        _ensure_authenticated(
            FakePage(
                "https://www.facebook.com/marketplace/",
                login_controls=1,
                visible=False,
            )
        )
    )


def test_verify_session_opens_marketplace_and_closes_browser(monkeypatch, tmp_path) -> None:
    page = FakePage("about:blank")
    context = FakeContext(page)
    playwright = FakePlaywright()

    async def fake_open_context(_config):
        return playwright, context

    monkeypatch.setattr(browser_module, "_open_context", fake_open_context)
    asyncio.run(
        browser_module.verify_session(
            browser_module.BrowserConfig(profile_dir=tmp_path / "profile")
        )
    )

    assert page.url == "https://www.facebook.com/marketplace/"
    assert context.closed
    assert playwright.stopped


def test_fetch_listings_applies_hard_radius_before_returning(
    monkeypatch, tmp_path
) -> None:
    page = FakePage("about:blank")
    context = FakeContext(page)
    playwright = FakePlaywright()

    async def fake_open_context(_config):
        return playwright, context

    async def fake_extract_origin(_page):
        return "Denver, Colorado"

    async def fake_extract_cards(_page):
        return [
            {
                "href": "https://facebook.com/marketplace/item/1",
                "text": "$400\nFlair 58\nAurora, CO",
            },
            {
                "href": "https://facebook.com/marketplace/item/2",
                "text": "$350\nFlair 58\nCasper, WY",
            },
        ]

    class FakeDistanceFilter:
        async def distance_between(self, _origin, location):
            return {"Aurora, CO": 12.0, "Casper, WY": 225.0}[location]

    monkeypatch.setattr(browser_module, "_open_context", fake_open_context)
    monkeypatch.setattr(browser_module, "_extract_search_origin", fake_extract_origin)
    monkeypatch.setattr(browser_module, "_extract_cards", fake_extract_cards)
    search = browser_module.SearchConfig(
        "Flair",
        "https://facebook.com/marketplace/denver/search?query=flair",
        max_distance_miles=40,
    )

    listings = asyncio.run(
        browser_module.fetch_listings(
            browser_module.BrowserConfig(
                profile_dir=tmp_path / "profile",
                scroll_count=0,
            ),
            (search,),
            distance_filter=FakeDistanceFilter(),
        )
    )

    assert [listing.listing_id for listing in listings] == ["1"]
    assert listings[0].distance_miles == 12.0
    assert context.closed
    assert playwright.stopped
