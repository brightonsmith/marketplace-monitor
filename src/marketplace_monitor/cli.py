from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from .browser import fetch_listings, interactive_login
from .config import ConfigError, load_config
from .config_manager import (
    active_searches,
    add_searches,
    create_config,
    remove_search,
    template_text,
    write_template,
)
from .monitor import run_once, watch
from .models import SearchConfig
from .notifier import build_notifier, format_price
from .report import format_report

DEFAULT_CONFIG = Path("config.yaml")


def _select_searches(
    searches: tuple[SearchConfig, ...],
    requested: list[str] | None,
) -> tuple[SearchConfig, ...]:
    if not requested:
        return searches
    by_name = {search.name.casefold(): search for search in searches}
    unknown = [name for name in requested if name.casefold() not in by_name]
    if unknown:
        raise ConfigError(
            f"Active search not found: {unknown[0]}. Run 'marketmon list' "
            "to see available names."
        )
    return tuple(dict.fromkeys(by_name[name.casefold()] for name in requested))


def _config_argument(parser: argparse.ArgumentParser, *, subcommand: bool = False) -> None:
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=argparse.SUPPRESS if subcommand else DEFAULT_CONFIG,
        help="configuration file (default: config.yaml)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marketmon",
        description="Monitor Facebook Marketplace searches",
    )
    _config_argument(parser)
    parser.add_argument("--version", action="version", version="marketmon 0.2.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a template configuration")
    _config_argument(init_parser, subcommand=True)
    init_parser.add_argument("--force", action="store_true", help="replace an existing file")

    template_parser = subparsers.add_parser(
        "template", help="print or write a configuration template"
    )
    template_parser.add_argument(
        "kind", choices=("config", "search"), help="template type"
    )
    template_parser.add_argument(
        "-o", "--output", type=Path, help="write to a file instead of stdout"
    )
    template_parser.add_argument(
        "--force", action="store_true", help="replace an existing file"
    )

    login_parser = subparsers.add_parser(
        "login", help="open a browser and save a local Facebook session"
    )
    _config_argument(login_parser, subcommand=True)

    check_parser = subparsers.add_parser(
        "check", aliases=["run-once"], help="check every active search once"
    )
    _config_argument(check_parser, subcommand=True)

    watch_parser = subparsers.add_parser(
        "watch", help="continuously check every active search"
    )
    _config_argument(watch_parser, subcommand=True)

    report_parser = subparsers.add_parser(
        "report", help="show the best listings from a fresh check"
    )
    _config_argument(report_parser, subcommand=True)
    report_parser.add_argument(
        "-n", "--limit", type=int, default=10, help="number of listings (default: 10)"
    )
    report_parser.add_argument(
        "-s",
        "--search",
        action="append",
        help="active search name; repeat to select multiple (default: all)",
    )

    add_parser = subparsers.add_parser(
        "add", help="add searches from another YAML file"
    )
    _config_argument(add_parser, subcommand=True)
    add_parser.add_argument("source", type=Path, help="search or configuration YAML file")
    add_parser.add_argument(
        "--replace", action="store_true", help="replace an active search with the same name"
    )

    list_parser = subparsers.add_parser(
        "list", aliases=["show-active"], help="show active searches"
    )
    _config_argument(list_parser, subcommand=True)
    list_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    remove_parser = subparsers.add_parser("remove", help="remove an active search by name")
    _config_argument(remove_parser, subcommand=True)
    remove_parser.add_argument("name", help="exact search name, matched case-insensitively")
    return parser


def _price_range(search) -> str:
    minimum = format_price(search.min_price_cents) if search.min_price_cents is not None else "any"
    maximum = format_price(search.max_price_cents) if search.max_price_cents is not None else "any"
    return f"{minimum}–{maximum}"


def _list_searches(args: argparse.Namespace) -> None:
    searches = active_searches(args.config)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": search.name,
                        "url": search.url,
                        "min_price": (
                            search.min_price_cents / 100
                            if search.min_price_cents is not None
                            else None
                        ),
                        "max_price": (
                            search.max_price_cents / 100
                            if search.max_price_cents is not None
                            else None
                        ),
                        "minimum_relevance": search.minimum_relevance,
                    }
                    for search in searches
                ],
                indent=2,
            )
        )
        return
    if not searches:
        print("No active searches.")
        return
    print(f"Active searches ({len(searches)}):")
    for index, search in enumerate(searches, start=1):
        print(f"{index}. {search.name} · {_price_range(search)}")
        print(f"   {search.url}")


async def _run_browser_command(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    if args.command == "login":
        await interactive_login(config.browser)
        return

    notifier = build_notifier(config.notifications)
    if args.command in {"check", "run-once"}:
        summary = await run_once(config, notifier)
        print(
            f"Check complete: {summary.discovered} discovered, "
            f"{summary.matched} matched, {summary.new} new, "
            f"{summary.notified} notified, {summary.held} held"
        )
        return
    if args.command == "report":
        if args.limit < 1:
            raise ConfigError("report limit must be positive")
        selected_searches = _select_searches(config.searches, args.search)
        listings = await fetch_listings(config.browser, selected_searches)
        print(format_report(listings, selected_searches, limit=args.limit))
        return
    await watch(
        config,
        notifier,
        config_loader=lambda: load_config(args.config),
    )


def _run_management_command(args: argparse.Namespace) -> bool:
    if args.command == "init":
        destination = create_config(args.config, force=args.force)
        print(f"Created {destination}")
        return True
    if args.command == "template":
        if args.output is None:
            print(template_text(args.kind), end="")
        else:
            destination = write_template(
                args.kind,
                args.output,
                force=args.force,
            )
            print(f"Created {destination}")
        return True
    if args.command == "add":
        names = add_searches(args.config, args.source, replace=args.replace)
        print("Activated: " + ", ".join(names))
        return True
    if args.command in {"list", "show-active"}:
        _list_searches(args)
        return True
    if args.command == "remove":
        print(f"Removed: {remove_search(args.config, args.name)}")
        return True
    return False


def main() -> None:
    args = build_parser().parse_args()
    try:
        if not _run_management_command(args):
            asyncio.run(_run_browser_command(args))
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    except PlaywrightError as error:
        message = str(error)
        if "Executable doesn't exist" in message:
            message = "Playwright Chromium is not installed. Run 'playwright install chromium'."
        print(f"Browser error: {message}", file=sys.stderr)
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        print("Stopped")


if __name__ == "__main__":
    main()
