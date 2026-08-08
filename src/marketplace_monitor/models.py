from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Listing:
    listing_id: str
    title: str
    url: str
    search_name: str
    price_cents: int | None = None
    location: str | None = None
    distance_miles: float | None = None
    image_url: str | None = None


@dataclass(frozen=True)
class StatusUpdate:
    discovered: int
    matched: int
    listing: Listing | None
    is_exact_match: bool


@dataclass(frozen=True)
class BrowserConfig:
    profile_dir: Path = Path("browser-profile")
    headless: bool = True
    page_load_timeout_seconds: int = 45
    scroll_count: int = 2


@dataclass(frozen=True)
class SearchConfig:
    name: str
    url: str
    min_price_cents: int | None = None
    max_price_cents: int | None = None
    include_any: tuple[str, ...] = field(default_factory=tuple)
    exclude: tuple[str, ...] = field(default_factory=tuple)
    minimum_relevance: float = 0.20
    max_distance_miles: float | None = None


@dataclass(frozen=True)
class NtfyConfig:
    server: str = "https://ntfy.sh"
    topic: str = ""


@dataclass(frozen=True)
class NotificationConfig:
    provider: str = "console"
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)


@dataclass(frozen=True)
class QuietHoursConfig:
    start_minutes: int
    end_minutes: int


@dataclass(frozen=True)
class AppConfig:
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    database_path: Path = Path("data/marketplace.db")
    check_interval_minutes: int = 10
    notify_on_first_run: bool = False
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    searches: tuple[SearchConfig, ...] = field(default_factory=tuple)
    status_interval_minutes: int = 60
    quiet_hours: QuietHoursConfig | None = None
    notify_on_startup: bool = True
