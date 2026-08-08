from __future__ import annotations

import json
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Listing, NotificationConfig, StatusUpdate


class NotificationError(RuntimeError):
    """Raised when a notification cannot be delivered."""


def format_price(price_cents: int | None) -> str:
    if price_cents is None:
        return "Price unavailable"
    if price_cents == 0:
        return "Free"
    return f"${price_cents / 100:,.2f}"


class Notifier(ABC):
    @abstractmethod
    def send(self, listing: Listing) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_status(self, status: StatusUpdate, *, startup: bool = False) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_error(self, title: str, message: str) -> None:
        """Send an operational alert that must not be suppressed by quiet hours."""
        raise NotImplementedError


def _status_content(
    status: StatusUpdate,
    *,
    startup: bool,
) -> tuple[str, str, str | None]:
    prefix = "Started" if startup else "Still running"
    if status.listing is None:
        if status.discovered:
            return (
                f"{prefix} · no relevant candidates",
                f"{status.discovered} Marketplace listings checked",
                None,
            )
        return (
            f"{prefix} · no listings found",
            f"Latest Marketplace check completed · {status.discovered} checked",
            None,
        )

    listing = status.listing
    location = f" · {listing.location}" if listing.location else ""
    if status.is_exact_match:
        noun = "match" if status.matched == 1 else "matches"
        title = f"{prefix} · {status.matched} {noun}"
        label = "Best"
    else:
        title = f"{prefix} · no exact matches"
        label = "Closest"
    message = (
        f"{label}: {listing.title}\n"
        f"{format_price(listing.price_cents)}{location} · {status.discovered} checked"
    )
    return title, message, listing.url


class ConsoleNotifier(Notifier):
    def send(self, listing: Listing) -> None:
        location = f" · {listing.location}" if listing.location else ""
        print(f"NEW: {listing.title} · {format_price(listing.price_cents)}{location}")
        print(listing.url)

    def send_status(self, status: StatusUpdate, *, startup: bool = False) -> None:
        title, message, url = _status_content(status, startup=startup)
        print(f"STATUS: {title}")
        print(message)
        if url:
            print(url)

    def send_error(self, title: str, message: str) -> None:
        print(f"ERROR: {title}")
        print(message)


class NtfyNotifier(Notifier):
    def __init__(
        self,
        server: str,
        topic: str,
        access_token: str | None = None,
        dashboard_url: str | None = None,
    ):
        self.server = server.rstrip("/")
        self.topic = topic
        self.endpoint = f"{self.server}/{topic}"
        self.access_token = access_token
        self.dashboard_url = (
            dashboard_url or os.getenv("MARKETMON_DASHBOARD_URL") or _tailscale_url()
        )

    def send(self, listing: Listing) -> None:
        location = f"\n{listing.location}" if listing.location else ""
        body = f"{format_price(listing.price_cents)}{location}\n{listing.search_name}"
        dashboard_listing_url = (
            f"{self.dashboard_url.rstrip('/')}/listings/"
            f"{quote(listing.listing_id, safe='')}"
            if self.dashboard_url
            else None
        )
        payload: dict[str, object] = {
            "topic": self.topic,
            "title": listing.title,
            "message": body,
            "click": dashboard_listing_url or listing.url,
            "tags": ["shopping_cart"],
        }
        if dashboard_listing_url:
            payload["actions"] = [
                {
                    "action": "view",
                    "label": "Open Facebook",
                    "url": listing.url,
                    "clear": True,
                }
            ]
        self._send_payload(payload)

    def send_status(self, status: StatusUpdate, *, startup: bool = False) -> None:
        title, message, url = _status_content(status, startup=startup)
        payload = {
            "topic": self.topic,
            "title": title,
            "message": message,
            "tags": ["white_check_mark"],
        }
        if url:
            payload["click"] = url
        self._send_payload(payload)

    def send_error(self, title: str, message: str) -> None:
        self._send_payload(
            {
                "topic": self.topic,
                "title": title,
                "message": message,
                "priority": 5,
                "tags": ["warning"],
            }
        )

    def _send_payload(self, payload: dict[str, object]) -> None:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        request = Request(
            self.server,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:
                if not 200 <= response.status < 300:
                    raise NotificationError(f"ntfy returned HTTP {response.status}")
        except (HTTPError, URLError, TimeoutError) as error:
            raise NotificationError(f"Could not send ntfy notification: {error}") from error


def build_notifier(config: NotificationConfig) -> Notifier:
    if config.provider == "console":
        return ConsoleNotifier()
    return NtfyNotifier(
        server=config.ntfy.server,
        topic=config.ntfy.topic,
        access_token=os.getenv("NTFY_ACCESS_TOKEN"),
    )


def _tailscale_url(port: int = 8000) -> str | None:
    """Return this machine's stable Tailscale dashboard URL when available."""
    if shutil.which("tailscale") is None:
        return None
    try:
        result = subprocess.run(
            ("tailscale", "status", "--json"),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    self_status = status.get("Self") or {}
    hostname = str(self_status.get("DNSName") or "").rstrip(".")
    if not hostname:
        addresses = self_status.get("TailscaleIPs") or []
        hostname = next((str(value) for value in addresses if ":" not in str(value)), "")
    return f"http://{hostname}:{port}" if hostname else None
