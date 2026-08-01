from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Listing, NotificationConfig


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


class ConsoleNotifier(Notifier):
    def send(self, listing: Listing) -> None:
        location = f" · {listing.location}" if listing.location else ""
        print(f"NEW: {listing.title} · {format_price(listing.price_cents)}{location}")
        print(listing.url)


class NtfyNotifier(Notifier):
    def __init__(self, server: str, topic: str, access_token: str | None = None):
        self.endpoint = f"{server.rstrip('/')}/{topic}"
        self.access_token = access_token

    def send(self, listing: Listing) -> None:
        location = f"\n{listing.location}" if listing.location else ""
        body = f"{format_price(listing.price_cents)}{location}\n{listing.search_name}"
        payload = {
            "topic": self.endpoint.rsplit("/", 1)[-1],
            "title": listing.title,
            "message": body,
            "click": listing.url,
            "tags": ["shopping_cart"],
        }
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        request = Request(
            self.endpoint,
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
