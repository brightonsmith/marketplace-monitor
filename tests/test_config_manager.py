from pathlib import Path

import pytest

from marketplace_monitor.config import ConfigError, load_config
from marketplace_monitor.config_manager import (
    add_search_documents,
    add_searches,
    create_config,
    remove_search,
)


def write_search(path: Path, name: str, query: str = "espresso") -> None:
    path.write_text(
        f"""
name: {name}
url: https://www.facebook.com/marketplace/search/?query={query}
max_price: 500
include_any:
  - {query}
""",
        encoding="utf-8",
    )


def test_create_add_replace_and_remove_search(tmp_path: Path) -> None:
    config_path = tmp_path / "settings" / "config.yaml"
    create_config(config_path)
    assert load_config(config_path).searches == ()

    source = tmp_path / "flair.yaml"
    write_search(source, "Flair 58 Plus", "flair 58")
    assert add_searches(config_path, source) == ("Flair 58 Plus",)
    assert load_config(config_path).searches[0].max_price_cents == 50_000

    with pytest.raises(ConfigError, match="already active"):
        add_searches(config_path, source)

    write_search(source, "Flair 58 Plus", "flair 58 plus")
    add_searches(config_path, source, replace=True)
    assert load_config(config_path).searches[0].include_any == ("flair 58 plus",)

    assert remove_search(config_path, "flair 58 plus") == "Flair 58 Plus"
    assert load_config(config_path).searches == ()


def test_add_accepts_a_full_config_with_multiple_searches(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    create_config(config_path)
    source = tmp_path / "bundle.yaml"
    source.write_text(
        """
searches:
  - name: Espresso machine
    url: https://www.facebook.com/marketplace/search/?query=espresso
  - name: Spider putter
    url: https://www.facebook.com/marketplace/search/?query=spider%20putter
""",
        encoding="utf-8",
    )

    names = add_searches(config_path, source)
    assert names == ("Espresso machine", "Spider putter")
    assert [search.name for search in load_config(config_path).searches] == list(names)


def test_create_config_requires_force_to_replace(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    create_config(path)
    with pytest.raises(ConfigError, match="already exists"):
        create_config(path)
    create_config(path, force=True)


def test_create_config_accepts_an_interactively_edited_document(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    create_config(
        path,
        document={
            "browser": {},
            "database_path": "data/marketplace.db",
            "check_interval_minutes": 5,
            "status_interval_minutes": 0,
            "quiet_hours": None,
            "notify_on_first_run": False,
            "notify_on_startup": True,
            "notifications": {"provider": "console", "ntfy": {}},
            "searches": [],
        },
    )
    assert load_config(path).check_interval_minutes == 5


def test_interactive_search_document_can_be_added(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    create_config(config_path)
    assert add_search_documents(
        config_path,
        [
            {
                "name": "Example product",
                "url": "https://www.facebook.com/marketplace/search/?query=example",
            }
        ],
    ) == ("Example product",)
    assert load_config(config_path).searches[0].minimum_relevance == 0.20
