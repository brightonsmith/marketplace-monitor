from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import (
    AppConfig,
    BrowserConfig,
    NotificationConfig,
    NtfyConfig,
    QuietHoursConfig,
    SearchConfig,
)


class ConfigError(ValueError):
    """Raised when the monitor configuration is invalid."""


def _money_to_cents(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field_name} must be a number")
    if value < 0:
        raise ConfigError(f"{field_name} cannot be negative")
    return round(float(value) * 100)


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{field_name} must be a list of strings")
    return tuple(item.strip().casefold() for item in value if item.strip())


def _time_to_minutes(value: Any, field_name: str) -> int:
    if not isinstance(value, str):
        raise ConfigError(f"{field_name} must use 24-hour HH:MM format")
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ConfigError(f"{field_name} must use 24-hour HH:MM format")
    hour, minute = (int(part) for part in parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ConfigError(f"{field_name} must use 24-hour HH:MM format")
    return hour * 60 + minute


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(
            f"Configuration file not found: {config_path}. "
            "Copy config.example.yaml to config.yaml first."
        )

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("Configuration must be a YAML mapping")

    browser_raw = raw.get("browser", {})
    if not isinstance(browser_raw, dict):
        raise ConfigError("browser must be a mapping")
    browser = BrowserConfig(
        profile_dir=Path(browser_raw.get("profile_dir", "browser-profile")),
        headless=bool(browser_raw.get("headless", True)),
        page_load_timeout_seconds=int(browser_raw.get("page_load_timeout_seconds", 45)),
        scroll_count=int(browser_raw.get("scroll_count", 2)),
    )
    if browser.page_load_timeout_seconds < 1:
        raise ConfigError("browser.page_load_timeout_seconds must be positive")
    if browser.scroll_count < 0:
        raise ConfigError("browser.scroll_count cannot be negative")

    searches_raw = raw.get("searches")
    if not isinstance(searches_raw, list) or not searches_raw:
        raise ConfigError("searches must contain at least one search")

    searches: list[SearchConfig] = []
    for index, item in enumerate(searches_raw):
        prefix = f"searches[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{prefix} must be a mapping")
        name = item.get("name")
        url = item.get("url")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{prefix}.name is required")
        if not isinstance(url, str) or "/marketplace/" not in url:
            raise ConfigError(f"{prefix}.url must be a Facebook Marketplace URL")
        min_price = _money_to_cents(item.get("min_price"), f"{prefix}.min_price")
        max_price = _money_to_cents(item.get("max_price"), f"{prefix}.max_price")
        if min_price is not None and max_price is not None and min_price > max_price:
            raise ConfigError(f"{prefix}.min_price cannot exceed max_price")
        searches.append(
            SearchConfig(
                name=name.strip(),
                url=url.strip(),
                min_price_cents=min_price,
                max_price_cents=max_price,
                include_any=_string_tuple(item.get("include_any"), f"{prefix}.include_any"),
                exclude=_string_tuple(item.get("exclude"), f"{prefix}.exclude"),
            )
        )

    notification_raw = raw.get("notifications", {})
    if not isinstance(notification_raw, dict):
        raise ConfigError("notifications must be a mapping")
    provider = str(notification_raw.get("provider", "console")).casefold()
    if provider not in {"console", "ntfy"}:
        raise ConfigError("notifications.provider must be 'console' or 'ntfy'")
    ntfy_raw = notification_raw.get("ntfy", {})
    if not isinstance(ntfy_raw, dict):
        raise ConfigError("notifications.ntfy must be a mapping")
    ntfy = NtfyConfig(
        server=str(ntfy_raw.get("server", "https://ntfy.sh")).rstrip("/"),
        topic=str(ntfy_raw.get("topic", "")).strip(),
    )
    if provider == "ntfy" and not ntfy.topic:
        raise ConfigError("notifications.ntfy.topic is required for the ntfy provider")

    interval = int(raw.get("check_interval_minutes", 10))
    if interval < 1:
        raise ConfigError("check_interval_minutes must be positive")

    status_interval = int(raw.get("status_interval_minutes", 60))
    if status_interval < 0:
        raise ConfigError("status_interval_minutes cannot be negative")

    quiet_hours_raw = raw.get("quiet_hours")
    quiet_hours = None
    if quiet_hours_raw is not None:
        if not isinstance(quiet_hours_raw, dict):
            raise ConfigError("quiet_hours must be a mapping")
        start_minutes = _time_to_minutes(quiet_hours_raw.get("start"), "quiet_hours.start")
        end_minutes = _time_to_minutes(quiet_hours_raw.get("end"), "quiet_hours.end")
        if start_minutes == end_minutes:
            raise ConfigError("quiet_hours.start and quiet_hours.end must be different")
        quiet_hours = QuietHoursConfig(
            start_minutes=start_minutes,
            end_minutes=end_minutes,
        )

    return AppConfig(
        browser=browser,
        database_path=Path(raw.get("database_path", "data/marketplace.db")),
        check_interval_minutes=interval,
        notify_on_first_run=bool(raw.get("notify_on_first_run", False)),
        notifications=NotificationConfig(provider=provider, ntfy=ntfy),
        searches=tuple(searches),
        status_interval_minutes=status_interval,
        quiet_hours=quiet_hours,
        notify_on_startup=bool(raw.get("notify_on_startup", True)),
    )
