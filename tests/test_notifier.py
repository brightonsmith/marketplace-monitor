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
        captured["url"] = request.full_url
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
    assert captured["url"] == "https://ntfy.sh"


def test_ntfy_authentication_error_is_high_priority(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr(notifier_module, "urlopen", fake_urlopen)
    notifier = NtfyNotifier("https://ntfy.sh", "example-topic")

    notifier.send_error("Marketmon needs Facebook login", "Sign in again.")

    assert captured["payload"] == {
        "topic": "example-topic",
        "title": "Marketmon needs Facebook login",
        "message": "Sign in again.",
        "priority": 5,
        "tags": ["warning"],
    }


def test_ntfy_listing_opens_dashboard_with_separate_facebook_action(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr(notifier_module, "urlopen", fake_urlopen)
    listing = Listing(
        "123",
        "Flair 58 Plus",
        "https://www.facebook.com/marketplace/item/123",
        "Flair 58 Plus",
        42_500,
        "Denver, CO",
    )
    notifier = NtfyNotifier(
        "https://ntfy.sh",
        "example-topic",
        dashboard_url="http://marketplace-pi.example.ts.net:8000",
    )

    notifier.send(listing)

    assert captured["payload"]["message"] == "$425.00\nDenver, CO\nFlair 58 Plus"
    assert captured["payload"]["click"] == (
        "http://marketplace-pi.example.ts.net:8000/listings/123"
    )
    assert captured["payload"]["actions"] == [
        {
            "action": "view",
            "label": "Open Facebook",
            "url": listing.url,
            "clear": True,
        }
    ]


def test_tailscale_url_prefers_magicdns_name(monkeypatch) -> None:
    monkeypatch.setattr(notifier_module.shutil, "which", lambda _name: "/usr/bin/tailscale")
    monkeypatch.setattr(
        notifier_module.subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Result",
            (),
            {
                "stdout": json.dumps(
                    {
                        "Self": {
                            "DNSName": "marketplace-pi.example.ts.net.",
                            "TailscaleIPs": ["100.64.0.1"],
                        }
                    }
                )
            },
        )(),
    )

    assert notifier_module._tailscale_url() == (
        "http://marketplace-pi.example.ts.net:8000"
    )
