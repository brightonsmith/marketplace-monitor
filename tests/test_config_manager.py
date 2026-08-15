from pathlib import Path

import pytest
import yaml

from marketplace_monitor.config import (
    ConfigError,
    default_config_document,
    load_config,
)
from marketplace_monitor.config_manager import (
    add_search_documents,
    add_searches,
    create_config,
    remove_search,
    replace_search_document,
    search_document,
    update_global_settings,
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


def test_create_config_serializes_python_model_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    create_config(path)

    assert yaml.safe_load(path.read_text(encoding="utf-8")) == default_config_document()


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


def test_search_document_can_be_loaded_and_edited_in_place(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    create_config(config_path)
    original = {
        "name": "Away Carry-On",
        "url": "https://www.facebook.com/marketplace/search/?query=away",
        "include_any": ["away carry on"],
    }
    add_search_documents(config_path, [original])

    edited = search_document(config_path, "away carry-on")
    edited["include_any"] = ["away carry-on", "away bigger carry-on"]

    assert replace_search_document(config_path, "Away Carry-On", edited) == (
        "Away Carry-On"
    )
    assert load_config(config_path).searches[0].include_any == (
        "away carry-on",
        "away bigger carry-on",
    )


def test_global_settings_update_preserves_searches_and_notification_provider(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    create_config(config_path)
    add_search_documents(
        config_path,
        [
            {
                "name": "Flair",
                "url": "https://www.facebook.com/marketplace/search/?query=flair",
            }
        ],
    )

    update_global_settings(
        config_path,
        check_interval_minutes=30,
        delivery_mode="digest",
        digest_interval_minutes=60,
        status_interval_minutes=0,
        notify_on_startup=False,
        quiet_hours={"start": "22:00", "end": "07:00"},
        timezone="America/Denver",
        time_format="12h",
    )

    updated = load_config(config_path)
    assert [search.name for search in updated.searches] == ["Flair"]
    assert updated.notifications.provider == "console"
    assert updated.notifications.delivery_mode == "digest"
    assert updated.check_interval_minutes == 30
    assert updated.status_interval_minutes == 0
    assert not updated.notify_on_startup
    assert updated.timezone == "America/Denver"
    assert updated.quiet_hours is not None
