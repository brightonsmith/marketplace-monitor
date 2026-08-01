import json

import marketplace_monitor.notifier as notifier_module
from marketplace_monitor.models import Listing, StatusUpdate
from marketplace_monitor.notifier import NtfyNotifier


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_ntfy_startup_status_is_concise_and_clickable(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(notifier_module, "urlopen", fake_urlopen)
    listing = Listing(
        listing_id="123",
        title="Flair 58 Plus",
        url="https://www.facebook.com/marketplace/item/123",
        search_name="Flair",
        price_cents=42_500,
        location="Boulder, CO",
    )
    notifier = NtfyNotifier("https://ntfy.sh", "example-topic")

    notifier.send_status(StatusUpdate(60, 2, listing, True), startup=True)

    assert captured["payload"] == {
        "topic": "example-topic",
        "title": "Started · 2 matches",
        "message": "Best: Flair 58 Plus\n$425.00 · Boulder, CO · 60 checked",
        "click": listing.url,
        "tags": ["white_check_mark"],
    }
    assert captured["timeout"] == 15
