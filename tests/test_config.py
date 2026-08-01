from pathlib import Path

import pytest

from marketplace_monitor.config import ConfigError, load_config


def test_load_config_converts_money_and_terms(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
notifications:
  provider: console
searches:
  - name: Espresso machine
    url: https://www.facebook.com/marketplace/search/?query=espresso
    min_price: 100.50
    max_price: 500
    include_any: [Flair, Espresso]
    exclude: [Wanted]
""",
        encoding="utf-8",
    )
    config = load_config(path)
    search = config.searches[0]
    assert search.min_price_cents == 10_050
    assert search.max_price_cents == 50_000
    assert search.include_any == ("flair", "espresso")
    assert search.exclude == ("wanted",)
    assert config.status_interval_minutes == 60
    assert config.notify_on_startup


def test_load_config_rejects_reversed_price_range(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
searches:
  - name: Example
    url: https://www.facebook.com/marketplace/search/?query=example
    min_price: 500
    max_price: 100
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="cannot exceed"):
        load_config(path)


def test_load_config_accepts_disabled_status_interval(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
status_interval_minutes: 0
searches:
  - name: Example
    url: https://www.facebook.com/marketplace/search/?query=example
""",
        encoding="utf-8",
    )
    assert load_config(path).status_interval_minutes == 0


def test_load_config_rejects_negative_status_interval(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
status_interval_minutes: -1
searches:
  - name: Example
    url: https://www.facebook.com/marketplace/search/?query=example
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="status_interval_minutes"):
        load_config(path)


def test_load_config_parses_overnight_quiet_hours(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
quiet_hours:
  start: "22:30"
  end: "07:15"
searches:
  - name: Example
    url: https://www.facebook.com/marketplace/search/?query=example
""",
        encoding="utf-8",
    )
    quiet_hours = load_config(path).quiet_hours
    assert quiet_hours is not None
    assert quiet_hours.start_minutes == 22 * 60 + 30
    assert quiet_hours.end_minutes == 7 * 60 + 15


@pytest.mark.parametrize("value", ["24:00", "7pm", "07:60"])
def test_load_config_rejects_invalid_quiet_hours(tmp_path: Path, value: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
quiet_hours:
  start: "{value}"
  end: "07:00"
searches:
  - name: Example
    url: https://www.facebook.com/marketplace/search/?query=example
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="HH:MM"):
        load_config(path)
