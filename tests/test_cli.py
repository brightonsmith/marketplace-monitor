from pathlib import Path

import pytest

from marketplace_monitor.cli import _select_searches, build_parser
from marketplace_monitor.config import ConfigError
from marketplace_monitor.models import SearchConfig


def test_config_flag_works_before_or_after_subcommand() -> None:
    parser = build_parser()
    before = parser.parse_args(["-c", "one.yaml", "watch"])
    after = parser.parse_args(["watch", "-c", "two.yaml"])
    assert before.config == Path("one.yaml")
    assert after.config == Path("two.yaml")


def test_management_command_arguments() -> None:
    args = build_parser().parse_args(
        ["add", "flair.yaml", "--replace", "-c", "active.yaml"]
    )
    assert args.command == "add"
    assert args.source == Path("flair.yaml")
    assert args.replace
    assert args.config == Path("active.yaml")


def test_legacy_command_alias_is_retained() -> None:
    args = build_parser().parse_args(["run-once"])
    assert args.command == "run-once"


def test_template_command_accepts_kind_and_output_path() -> None:
    args = build_parser().parse_args(
        ["template", "search", "-o", "flair.yaml", "--force"]
    )
    assert args.command == "template"
    assert args.kind == "search"
    assert args.output == Path("flair.yaml")
    assert args.force


def test_report_command_accepts_limit_and_config() -> None:
    args = build_parser().parse_args(
        [
            "report",
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
    assert args.command == "report"
    assert args.limit == 15
    assert args.search == ["Flair 58 Plus", "Spider Putter"]
    assert args.config == Path("active.yaml")


def test_report_search_selection_is_case_insensitive_and_deduplicated() -> None:
    flair = SearchConfig("Flair 58 Plus", "https://facebook.com/marketplace/flair")
    spider = SearchConfig("Spider Putter", "https://facebook.com/marketplace/spider")
    searches = (flair, spider)

    assert _select_searches(searches, None) == searches
    assert _select_searches(
        searches,
        ["flair 58 plus", "FLAIR 58 PLUS", "Spider Putter"],
    ) == searches
    with pytest.raises(ConfigError, match="Active search not found"):
        _select_searches(searches, ["Unknown"])
