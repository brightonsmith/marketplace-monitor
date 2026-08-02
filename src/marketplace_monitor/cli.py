from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError

from .browser import (
    BrowserSessionError,
    fetch_listings,
    interactive_login,
    verify_session,
)
from .config import ConfigError, load_config
from .config_manager import (
    active_searches,
    add_search_documents,
    add_searches,
    create_config,
    remove_search,
)
from .models import SearchConfig
from .monitor import run_once, send_authentication_alert, watch
from .notifier import build_notifier, format_price
from .report import format_report
from .service import (
    ServiceError,
    install_service,
    linger_command,
    restart_service,
    service_logs,
    service_status,
    uninstall_service,
)


def _default_config_path() -> Path:
    configured = os.getenv("MARKETMON_CONFIG")
    if configured:
        return Path(configured).expanduser()
    local = Path("config.yaml")
    if local.exists():
        return local
    return Path.home() / ".config/marketmon/config.yaml"


DEFAULT_CONFIG = _default_config_path()


def _version() -> str:
    try:
        return version("marketplace-monitor")
    except PackageNotFoundError:
        return "0.3.0"


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
        help=f"configuration file (default: {DEFAULT_CONFIG})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marketmon",
        description="Monitor Facebook Marketplace and notify on new matches.",
    )
    _config_argument(parser)
    parser.add_argument("--version", action="version", version=f"marketmon {_version()}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create the monitor configuration")
    _config_argument(init_parser, subcommand=True)
    init_parser.add_argument("--force", action="store_true", help="replace an existing file")

    login_parser = subparsers.add_parser(
        "login", help="open Facebook and save a verified browser session"
    )
    _config_argument(login_parser, subcommand=True)

    add_parser = subparsers.add_parser(
        "add", help="interactively add a search or import a search YAML file"
    )
    _config_argument(add_parser, subcommand=True)
    add_parser.add_argument(
        "source",
        type=Path,
        nargs="?",
        help="optional search or configuration YAML file",
    )
    add_parser.add_argument(
        "--replace", action="store_true", help="replace a search with the same name"
    )

    list_parser = subparsers.add_parser("list", help="show active searches")
    _config_argument(list_parser, subcommand=True)
    list_parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    remove_parser = subparsers.add_parser("remove", help="remove an active search")
    _config_argument(remove_parser, subcommand=True)
    remove_parser.add_argument("name", help="search name, matched case-insensitively")

    check_parser = subparsers.add_parser(
        "check", help="verify login and show current listings without changing history"
    )
    _config_argument(check_parser, subcommand=True)
    check_parser.add_argument(
        "-n", "--limit", type=int, default=10, help="listings to show (default: 10)"
    )
    check_parser.add_argument(
        "-s",
        "--search",
        action="append",
        help="search name; repeat to select multiple (default: all)",
    )

    watch_parser = subparsers.add_parser(
        "watch", help="monitor continuously, or perform one real cycle with --once"
    )
    _config_argument(watch_parser, subcommand=True)
    watch_parser.add_argument(
        "--once", action="store_true", help="update history and notifications once, then exit"
    )

    service_parser = subparsers.add_parser(
        "service", help="manage autonomous monitoring with user-level systemd"
    )
    _config_argument(service_parser, subcommand=True)
    service_subparsers = service_parser.add_subparsers(
        dest="service_command", required=True
    )
    install_parser = service_subparsers.add_parser(
        "install", help="install and start the service"
    )
    _config_argument(install_parser, subcommand=True)
    status_parser = service_subparsers.add_parser("status", help="show service status")
    _config_argument(status_parser, subcommand=True)
    logs_parser = service_subparsers.add_parser("logs", help="show service logs")
    _config_argument(logs_parser, subcommand=True)
    logs_parser.add_argument("-f", "--follow", action="store_true", help="follow new log output")
    restart_parser = service_subparsers.add_parser(
        "restart", help="restart after a configuration change"
    )
    _config_argument(restart_parser, subcommand=True)
    uninstall_parser = service_subparsers.add_parser(
        "uninstall", help="stop and remove the service"
    )
    _config_argument(uninstall_parser, subcommand=True)
    return parser


def _price_range(search: SearchConfig) -> str:
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


def _prompt_required(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print(f"{label} is required.")


def _prompt_optional_number(label: str) -> float | None:
    while True:
        value = input(f"{label} (blank for none): ").strip()
        if not value:
            return None
        try:
            number = float(value)
        except ValueError:
            print("Enter a number or leave it blank.")
            continue
        if number < 0:
            print("Price cannot be negative.")
            continue
        return number


def _prompt_terms(
    label: str,
    *,
    default: tuple[str, ...] = (),
    required: bool = False,
) -> list[str]:
    suffix = f" [{', '.join(default)}]" if default else ""
    while True:
        value = input(f"{label}, comma-separated{suffix}: ").strip()
        if not value and default:
            return list(default)
        terms = [term.strip() for term in value.split(",") if term.strip()]
        if terms or not required:
            return terms
        print("Enter at least one distinctive title phrase.")


def _interactive_search() -> dict[str, Any]:
    print("Add a Marketplace search. Configure location, radius, condition, and sorting")
    print("on Facebook first, then paste the complete results URL.")
    return {
        "name": _prompt_required("Search name"),
        "url": _prompt_required("Marketplace results URL"),
        "min_price": _prompt_optional_number("Minimum price"),
        "max_price": _prompt_optional_number("Maximum price"),
        "minimum_relevance": 0.20,
        "include_any": _prompt_terms("Exact title phrases", required=True),
        "exclude": _prompt_terms(
            "Excluded title phrases",
            default=("wanted", "looking for", "broken", "for parts", "parts only"),
        ),
    }


async def _run_browser_command(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    if args.command == "login":
        await interactive_login(config.browser)
        return

    notifier = build_notifier(config.notifications)
    try:
        if args.command == "check":
            if args.limit < 1:
                raise ConfigError("check limit must be positive")
            selected_searches = _select_searches(config.searches, args.search)
            if not selected_searches:
                await verify_session(config.browser)
                print("Facebook session is valid. No active searches.")
                return
            listings = await fetch_listings(config.browser, selected_searches)
            print(format_report(listings, selected_searches, limit=args.limit))
            return

        if args.once:
            summary = await run_once(config, notifier)
            print(
                f"Check complete: {summary.discovered} discovered, "
                f"{summary.matched} matched, {summary.new} new, "
                f"{summary.notified} notified, {summary.held} held"
            )
            return

        await watch(
            config,
            notifier,
            config_loader=lambda: load_config(args.config),
        )
    except BrowserSessionError as error:
        try:
            send_authentication_alert(notifier, error)
        except Exception as notification_error:
            print(
                f"Authentication alert could not be delivered: {notification_error}",
                file=sys.stderr,
            )
        raise


def _run_service_command(args: argparse.Namespace) -> None:
    if args.service_command == "install":
        destination = install_service(args.config)
        print(f"Installed and started {destination}")
        print("Enable startup before login once with:")
        print(f"  {linger_command()}")
    elif args.service_command == "status":
        service_status()
    elif args.service_command == "logs":
        service_logs(follow=args.follow)
    elif args.service_command == "restart":
        restart_service()
        print("Restarted marketmon.service")
    else:
        uninstall_service()
        print("Stopped and removed marketmon.service")


def _run_management_command(args: argparse.Namespace) -> bool:
    if args.command == "init":
        destination = create_config(args.config, force=args.force)
        print(f"Created {destination}")
        print("Next: configure notifications, run 'marketmon login', then 'marketmon add'.")
        return True
    if args.command == "add":
        if args.source is None:
            names = add_search_documents(
                args.config,
                [_interactive_search()],
                replace=args.replace,
            )
        else:
            names = add_searches(args.config, args.source, replace=args.replace)
        print("Activated: " + ", ".join(names))
        return True
    if args.command == "list":
        _list_searches(args)
        return True
    if args.command == "remove":
        print(f"Removed: {remove_search(args.config, args.name)}")
        return True
    if args.command == "service":
        _run_service_command(args)
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
    except BrowserSessionError as error:
        print(f"Authentication error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    except ServiceError as error:
        print(f"Service error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    except PlaywrightError as error:
        message = str(error)
        if "Executable doesn't exist" in message:
            message = (
                "Playwright Chromium is not installed. Run "
                "'python -m playwright install --with-deps chromium'."
            )
        print(f"Browser error: {message}", file=sys.stderr)
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        print("Stopped")


if __name__ == "__main__":
    main()
