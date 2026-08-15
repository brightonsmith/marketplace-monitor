from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def _minutes_to_time(value: int) -> str:
    hours, minutes = divmod(value, 60)
    return f"{hours:02d}:{minutes:02d}"


def local_timezone_name() -> str:
    """Return the host's IANA timezone name when it can be determined."""
    key = getattr(datetime.now().astimezone().tzinfo, "key", None)
    if key:
        return str(key)
    timezone_file = Path("/etc/timezone")
    try:
        candidate = timezone_file.read_text(encoding="utf-8").strip()
        ZoneInfo(candidate)
    except (OSError, ZoneInfoNotFoundError, ValueError):
        return "UTC"
    return candidate


def default_config_document() -> dict[str, Any]:
    """Return the YAML document represented by the Python model defaults."""
    defaults = AppConfig()
    quiet_hours = defaults.quiet_hours
    return {
        "browser": {
            "profile_dir": str(defaults.browser.profile_dir),
            "headless": defaults.browser.headless,
            "page_load_timeout_seconds": defaults.browser.page_load_timeout_seconds,
            "scroll_count": defaults.browser.scroll_count,
        },
        "database_path": str(defaults.database_path),
        "check_interval_minutes": defaults.check_interval_minutes,
        "status_interval_minutes": defaults.status_interval_minutes,
        "timezone": local_timezone_name(),
        "time_format": defaults.time_format,
        "quiet_hours": (
            {
                "start": _minutes_to_time(quiet_hours.start_minutes),
                "end": _minutes_to_time(quiet_hours.end_minutes),
            }
            if quiet_hours is not None
            else None
        ),
        "notify_on_first_run": defaults.notify_on_first_run,
        "notify_on_startup": defaults.notify_on_startup,
        "notifications": {
            "provider": defaults.notifications.provider,
            "delivery_mode": defaults.notifications.delivery_mode,
            "digest_interval_minutes": defaults.notifications.digest_interval_minutes,
            "ntfy": {
                "server": defaults.notifications.ntfy.server,
                "topic": defaults.notifications.ntfy.topic,
            },
        },
        "searches": [],
    }


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


def _relevance_threshold(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field_name} must be a number between 0 and 1")
    threshold = float(value)
    if not 0 <= threshold <= 1:
        raise ConfigError(f"{field_name} must be between 0 and 1")
    return threshold


def _positive_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field_name} must be a positive number")
    number = float(value)
    if number <= 0:
        raise ConfigError(f"{field_name} must be positive")
    return number


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


def load_config_document(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(
            f"Configuration file not found: {config_path}. "
            f"Create one with 'marketmon init -c {config_path}'."
        )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError("Configuration must be a YAML mapping")
    return raw


def _relative_to_config(value: Any, config_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return config_path.parent / path


def parse_config_document(raw: dict[str, Any], config_path: str | Path) -> AppConfig:
    config_path = Path(config_path)
    defaults = AppConfig()

    browser_raw = raw.get("browser", {})
    if not isinstance(browser_raw, dict):
        raise ConfigError("browser must be a mapping")
    browser = BrowserConfig(
        profile_dir=_relative_to_config(
            browser_raw.get("profile_dir", defaults.browser.profile_dir), config_path
        ),
        headless=bool(browser_raw.get("headless", defaults.browser.headless)),
        page_load_timeout_seconds=int(
            browser_raw.get(
                "page_load_timeout_seconds",
                defaults.browser.page_load_timeout_seconds,
            )
        ),
        scroll_count=int(
            browser_raw.get("scroll_count", defaults.browser.scroll_count)
        ),
    )
    if browser.page_load_timeout_seconds < 1:
        raise ConfigError("browser.page_load_timeout_seconds must be positive")
    if browser.scroll_count < 0:
        raise ConfigError("browser.scroll_count cannot be negative")

    searches_raw = raw.get("searches", [])
    if not isinstance(searches_raw, list):
        raise ConfigError("searches must be a list")

    searches: list[SearchConfig] = []
    search_names: set[str] = set()
    search_defaults = SearchConfig(name="", url="")
    for index, item in enumerate(searches_raw):
        prefix = f"searches[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{prefix} must be a mapping")
        name = item.get("name")
        url = item.get("url")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"{prefix}.name is required")
        normalized_name = name.strip().casefold()
        if normalized_name in search_names:
            raise ConfigError(f"{prefix}.name duplicates another active search")
        search_names.add(normalized_name)
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
                include_any=_string_tuple(
                    item.get("include_any"), f"{prefix}.include_any"
                ),
                exclude=_string_tuple(item.get("exclude"), f"{prefix}.exclude"),
                minimum_relevance=_relevance_threshold(
                    item.get("minimum_relevance", search_defaults.minimum_relevance),
                    f"{prefix}.minimum_relevance",
                ),
                max_distance_miles=_positive_number(
                    item.get("max_distance_miles"),
                    f"{prefix}.max_distance_miles",
                ),
            )
        )

    notification_raw = raw.get("notifications", {})
    if not isinstance(notification_raw, dict):
        raise ConfigError("notifications must be a mapping")
    provider = str(
        notification_raw.get("provider", defaults.notifications.provider)
    ).casefold()
    if provider not in {"console", "ntfy"}:
        raise ConfigError("notifications.provider must be 'console' or 'ntfy'")
    ntfy_raw = notification_raw.get("ntfy", {})
    if not isinstance(ntfy_raw, dict):
        raise ConfigError("notifications.ntfy must be a mapping")
    ntfy = NtfyConfig(
        server=str(ntfy_raw.get("server", defaults.notifications.ntfy.server)).rstrip(
            "/"
        ),
        topic=str(ntfy_raw.get("topic", defaults.notifications.ntfy.topic)).strip(),
    )
    if provider == "ntfy" and not ntfy.topic:
        raise ConfigError("notifications.ntfy.topic is required for the ntfy provider")
    delivery_mode = str(
        notification_raw.get("delivery_mode", defaults.notifications.delivery_mode)
    ).casefold()
    if delivery_mode not in {"immediate", "digest", "dashboard"}:
        raise ConfigError(
            "notifications.delivery_mode must be 'immediate', 'digest', or 'dashboard'"
        )
    digest_interval = int(
        notification_raw.get(
            "digest_interval_minutes",
            defaults.notifications.digest_interval_minutes,
        )
    )
    if digest_interval not in {30, 60, 180, 1440}:
        raise ConfigError(
            "notifications.digest_interval_minutes must be 30, 60, 180, or 1440"
        )

    interval = int(raw.get("check_interval_minutes", defaults.check_interval_minutes))
    if interval < 1:
        raise ConfigError("check_interval_minutes must be positive")

    status_interval = int(
        raw.get("status_interval_minutes", defaults.status_interval_minutes)
    )
    if status_interval < 0:
        raise ConfigError("status_interval_minutes cannot be negative")

    timezone_name = str(raw.get("timezone", local_timezone_name())).strip()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(f"Unknown timezone: {timezone_name}") from error

    time_format = str(raw.get("time_format", defaults.time_format)).casefold()
    if time_format not in {"12h", "24h"}:
        raise ConfigError("time_format must be '12h' or '24h'")

    quiet_hours_raw = raw.get("quiet_hours")
    quiet_hours = None
    if quiet_hours_raw is not None:
        if not isinstance(quiet_hours_raw, dict):
            raise ConfigError("quiet_hours must be a mapping")
        start_minutes = _time_to_minutes(
            quiet_hours_raw.get("start"), "quiet_hours.start"
        )
        end_minutes = _time_to_minutes(quiet_hours_raw.get("end"), "quiet_hours.end")
        if start_minutes == end_minutes:
            raise ConfigError("quiet_hours.start and quiet_hours.end must be different")
        quiet_hours = QuietHoursConfig(
            start_minutes=start_minutes,
            end_minutes=end_minutes,
        )

    return AppConfig(
        browser=browser,
        database_path=_relative_to_config(
            raw.get("database_path", defaults.database_path), config_path
        ),
        check_interval_minutes=interval,
        notify_on_first_run=bool(
            raw.get("notify_on_first_run", defaults.notify_on_first_run)
        ),
        notifications=NotificationConfig(
            provider=provider,
            ntfy=ntfy,
            delivery_mode=delivery_mode,
            digest_interval_minutes=digest_interval,
        ),
        searches=tuple(searches),
        status_interval_minutes=status_interval,
        quiet_hours=quiet_hours,
        notify_on_startup=bool(
            raw.get("notify_on_startup", defaults.notify_on_startup)
        ),
        timezone=timezone_name,
        time_format=time_format,
    )


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    return parse_config_document(load_config_document(config_path), config_path)
