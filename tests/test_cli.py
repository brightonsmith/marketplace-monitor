from pathlib import Path

from marketplace_monitor.cli import build_parser


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
