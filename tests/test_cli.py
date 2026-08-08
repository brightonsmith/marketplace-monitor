from pathlib import Path

import pytest

import marketplace_monitor.cli as cli
from marketplace_monitor.cli import (
    _interactive_config,
    _interactive_search,
    _select_searches,
    build_parser,
)
from marketplace_monitor.config import ConfigError
from marketplace_monitor.models import SearchConfig


def test_version_uses_published_distribution_name(monkeypatch) -> None:
    requested = []
    monkeypatch.setattr(
        cli, "version", lambda name: requested.append(name) or "0.3.1"
    )

    assert cli._version() == "0.3.1"
    assert requested == ["marketmon"]


def test_config_flag_works_before_or_after_top_level_command() -> None:
    parser = build_parser()
    before = parser.parse_args(["-c", "one.yaml", "watch"])
    after = parser.parse_args(["watch", "-c", "two.yaml"])
    assert before.config == Path("one.yaml")
    assert after.config == Path("two.yaml")


def test_add_supports_interactive_and_yaml_modes() -> None:
    parser = build_parser()
    interactive = parser.parse_args(["add", "-c", "active.yaml"])
    imported = parser.parse_args(
        ["add", "flair.yaml", "--replace", "-c", "active.yaml"]
    )
    assert interactive.source is None
    assert imported.source == Path("flair.yaml")
    assert imported.replace


def test_init_supports_interactive_and_default_modes() -> None:
    parser = build_parser()
    interactive = parser.parse_args(["init", "-c", "active.yaml"])
    defaults = parser.parse_args(["init", "--defaults"])
    assert not interactive.defaults
    assert defaults.defaults


def test_check_is_read_only_report_command() -> None:
    args = build_parser().parse_args(
        [
            "check",
            "-n",
            "15",
            "-s",
            "Flair 58 Plus",
            "-s",
            "Spider Putter",
            "-c",
            "active.yaml",
        ]
    )
    assert args.command == "check"
    assert args.limit == 15
    assert args.search == ["Flair 58 Plus", "Spider Putter"]
    assert args.config == Path("active.yaml")


def test_watch_once_is_explicit_stateful_cycle() -> None:
    args = build_parser().parse_args(["watch", "--once"])
    assert args.command == "watch"
    assert args.once


def test_feedback_command_accepts_durable_dispositions() -> None:
    args = build_parser().parse_args(
        ["feedback", "123456", "dismissed", "-c", "active.yaml"]
    )
    assert args.listing_id == "123456"
    assert args.disposition == "dismissed"
    assert args.config == Path("active.yaml")


def test_service_config_works_after_action() -> None:
    args = build_parser().parse_args(
        ["service", "install", "-c", "/tmp/marketmon/config.yaml"]
    )
    assert args.command == "service"
    assert args.service_command == "install"
    assert args.config == Path("/tmp/marketmon/config.yaml")


def test_removed_redundant_commands_are_not_public() -> None:
    parser = build_parser()
    for command in ("template", "report", "run-once", "show-active"):
        with pytest.raises(SystemExit):
            parser.parse_args([command])


def test_interactive_search_collects_defaults(monkeypatch) -> None:
    answers = iter(
        [
            "4",
            "550",
            "1",
            "Flair 58 Plus",
            "2",
            "https://www.facebook.com/marketplace/search/?query=flair%2058",
            "3",
            "200",
            "5",
            "flair 58, flair58",
            "s",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    search = _interactive_search()

    assert search["name"] == "Flair 58 Plus"
    assert search["min_price"] == 200
    assert search["include_any"] == ["flair 58", "flair58"]
    assert "broken" in search["exclude"]


def test_interactive_config_can_edit_settings_in_any_order(
    monkeypatch, tmp_path
) -> None:
    answers = iter(
        [
            "8",
            "my-private-topic",
            "6",
            "ntfy",
            "1",
            "5",
            "3",
            "22:00-07:00",
            "s",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    document = _interactive_config(tmp_path / "config.yaml")

    assert document is not None
    assert document["check_interval_minutes"] == 5
    assert document["notifications"]["provider"] == "ntfy"
    assert document["notifications"]["ntfy"]["topic"] == "my-private-topic"
    assert document["quiet_hours"] == {"start": "22:00", "end": "07:00"}


def test_search_selection_is_case_insensitive_and_deduplicated() -> None:
    flair = SearchConfig("Flair 58 Plus", "https://facebook.com/marketplace/flair")
    spider = SearchConfig("Spider Putter", "https://facebook.com/marketplace/spider")
    searches = (flair, spider)

    assert _select_searches(searches, None) == searches
    assert (
        _select_searches(
            searches,
            ["flair 58 plus", "FLAIR 58 PLUS", "Spider Putter"],
        )
        == searches
    )
    with pytest.raises(ConfigError, match="Active search not found"):
        _select_searches(searches, ["Unknown"])
