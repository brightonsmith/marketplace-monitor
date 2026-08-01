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

