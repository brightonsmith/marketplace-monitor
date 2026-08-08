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
from .config import (
    ConfigError,
    default_config_document,
    load_config,
    parse_config_document,
)
from .config_manager import (
    active_searches,
    add_search_documents,
    add_searches,
    create_config,
    remove_search,
)
from .models import SearchConfig
from .geocoding import DistanceFilter
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
from .storage import ListingStore


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
        return version("marketmon")
    except PackageNotFoundError:
        return "unknown"


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


def _config_argument(
    parser: argparse.ArgumentParser, *, subcommand: bool = False
) -> None:
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
    parser.add_argument(
        "--version", action="version", version=f"marketmon {_version()}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create the monitor configuration")
    _config_argument(init_parser, subcommand=True)
    init_parser.add_argument(
        "--force", action="store_true", help="replace an existing file"
    )
    init_parser.add_argument(
        "--defaults",
        action="store_true",
        help="create defaults without opening the interactive editor",
    )

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
    list_parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )

    remove_parser = subparsers.add_parser("remove", help="remove an active search")
    _config_argument(remove_parser, subcommand=True)
    remove_parser.add_argument("name", help="search name, matched case-insensitively")

    feedback_parser = subparsers.add_parser(
        "feedback", help="mark a known listing interested, dismissed, or clear"
    )
    _config_argument(feedback_parser, subcommand=True)
    feedback_parser.add_argument("listing_id", help="Facebook Marketplace listing ID")
    feedback_parser.add_argument(
        "disposition", choices=("interested", "dismissed", "clear")
    )

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
        "--once",
        action="store_true",
        help="update history and notifications once, then exit",
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
    logs_parser.add_argument(
        "-f", "--follow", action="store_true", help="follow new log output"
    )
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
    minimum = (
        format_price(search.min_price_cents)
        if search.min_price_cents is not None
        else "any"
    )
    maximum = (
        format_price(search.max_price_cents)
        if search.max_price_cents is not None
        else "any"
    )
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
                        "max_distance_miles": search.max_distance_miles,
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
        if search.max_distance_miles is not None:
            print(f"   Hard radius: {search.max_distance_miles:g} miles")
        print(f"   {search.url}")


def _prompt_text(label: str, current: str = "") -> str:
    suffix = f" [{current}]" if current else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or current


def _prompt_number(
    label: str,
    current: float | None,
    *,
    minimum: float = 0,
    integer: bool = False,
    allow_none: bool = False,
) -> int | float | None:
    while True:
        shown = "none" if current is None else str(current)
        value = input(f"{label} [{shown}]: ").strip()
        if not value:
            return current
        if allow_none and value.casefold() in {"none", "off"}:
            return None
        try:
            number = int(value) if integer else float(value)
        except ValueError:
            print("Enter a valid number, or press Enter to keep the current value.")
            continue
        if number < minimum:
            print(f"Value must be at least {minimum:g}.")
            continue
        return number


def _prompt_bool(label: str, current: bool) -> bool:
    while True:
        default = "Y/n" if current else "y/N"
        value = input(f"{label} [{default}]: ").strip().casefold()
        if not value:
            return current
        if value in {"y", "yes", "true", "on"}:
            return True
        if value in {"n", "no", "false", "off"}:
            return False
        print("Enter yes or no.")


def _prompt_terms(
    label: str,
    *,
    default: tuple[str, ...] = (),
) -> list[str]:
    shown = ", ".join(default) if default else "none"
    value = input(f"{label}, comma-separated [{shown}]: ").strip()
    if not value:
        return list(default)
    if value.casefold() in {"none", "off"}:
        return []
    return [term.strip() for term in value.split(",") if term.strip()]


def _display_value(value: Any) -> str:
    if value is None:
        return "off"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(value) if value else "none"
    return str(value) or "not set"


def _clear_interactive_screen() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")


def _quiet_hours_value(document: dict[str, Any]) -> str:
    quiet = document.get("quiet_hours")
    if not isinstance(quiet, dict):
        return "off"
    return f"{quiet.get('start')}–{quiet.get('end')}"


def _interactive_config(path: Path) -> dict[str, Any] | None:
    document = default_config_document()
    browser = document["browser"]
    notifications = document["notifications"]
    ntfy = notifications["ntfy"]

    while True:
        _clear_interactive_screen()
        print("\nMarketmon configuration")
        print("Monitoring")
        print(f"  1. Check interval: {document['check_interval_minutes']} minutes")
        print(
            f"  2. Status interval: {document['status_interval_minutes']} minutes (0 disables)"
        )
        print(f"  3. Quiet hours: {_quiet_hours_value(document)}")
        print(
            f"  4. Notify on first run: {_display_value(document['notify_on_first_run'])}"
        )
        print(
            f"  5. Notify on startup: {_display_value(document['notify_on_startup'])}"
        )
        print("Notifications")
        print(f"  6. Provider: {notifications['provider']}")
        print(f"  7. ntfy server: {ntfy['server']}")
        topic = _display_value(ntfy["topic"])
        if notifications["provider"] == "ntfy" and not ntfy["topic"]:
            topic = "required"
        print(f"  8. ntfy topic: {topic}")
        print("Browser and storage")
        print(f"  9. Browser profile: {browser['profile_dir']}")
        print(f" 10. Headless monitoring: {_display_value(browser['headless'])}")
        print(f" 11. Page timeout: {browser['page_load_timeout_seconds']} seconds")
        print(f" 12. Scroll count: {browser['scroll_count']}")
        print(f" 13. Database: {document['database_path']}")
        print("\n  S. Save configuration    Q. Cancel")
        choice = input("Select a setting: ").strip().casefold()

        if choice == "q":
            return None
        if choice == "s":
            try:
                parse_config_document(document, path)
            except ConfigError as error:
                print(f"Cannot save: {error}")
                continue
            return document
        if choice == "1":
            document["check_interval_minutes"] = _prompt_number(
                "Check interval in minutes",
                document["check_interval_minutes"],
                minimum=1,
                integer=True,
            )
        elif choice == "2":
            document["status_interval_minutes"] = _prompt_number(
                "Status interval in minutes",
                document["status_interval_minutes"],
                integer=True,
            )
        elif choice == "3":
            current = _quiet_hours_value(document)
            value = input(f"Quiet hours as HH:MM-HH:MM, or off [{current}]: ").strip()
            if value:
                if value.casefold() in {"off", "none"}:
                    document["quiet_hours"] = None
                else:
                    parts = value.split("-", 1)
                    if len(parts) != 2:
                        print("Use HH:MM-HH:MM, for example 22:00-07:00.")
                    else:
                        candidate = {
                            "start": parts[0].strip(),
                            "end": parts[1].strip(),
                        }
                        trial = dict(document)
                        trial["quiet_hours"] = candidate
                        try:
                            parse_config_document(trial, path)
                        except ConfigError as error:
                            print(error)
                        else:
                            document["quiet_hours"] = candidate
        elif choice == "4":
            document["notify_on_first_run"] = _prompt_bool(
                "Notify for existing matches on the first run",
                document["notify_on_first_run"],
            )
        elif choice == "5":
            document["notify_on_startup"] = _prompt_bool(
                "Send a notification when watch starts",
                document["notify_on_startup"],
            )
        elif choice == "6":
            provider = (
                input(f"Provider: console or ntfy [{notifications['provider']}]: ")
                .strip()
                .casefold()
            )
            if provider in {"console", "ntfy"}:
                notifications["provider"] = provider
            elif provider:
                print("Provider must be console or ntfy.")
        elif choice == "7":
            ntfy["server"] = _prompt_text("ntfy server", ntfy["server"])
        elif choice == "8":
            ntfy["topic"] = _prompt_text("ntfy topic", ntfy["topic"])
        elif choice == "9":
            browser["profile_dir"] = _prompt_text(
                "Browser profile path", browser["profile_dir"]
            )
        elif choice == "10":
            browser["headless"] = _prompt_bool(
                "Run monitoring headlessly", browser["headless"]
            )
        elif choice == "11":
            browser["page_load_timeout_seconds"] = _prompt_number(
                "Page timeout in seconds",
                browser["page_load_timeout_seconds"],
                minimum=1,
                integer=True,
            )
        elif choice == "12":
            browser["scroll_count"] = _prompt_number(
                "Scroll count", browser["scroll_count"], integer=True
            )
        elif choice == "13":
            document["database_path"] = _prompt_text(
                "Database path", document["database_path"]
            )
        else:
            print("Select a setting number, S to save, or Q to cancel.")


def _interactive_search() -> dict[str, Any] | None:
    search: dict[str, Any] = {
        "name": "",
        "url": "",
        "min_price": None,
        "max_price": None,
        "minimum_relevance": 0.20,
        "max_distance_miles": None,
        "include_any": [],
        "exclude": ["wanted", "looking for", "broken", "for parts", "parts only"],
    }
    while True:
        _clear_interactive_screen()
        print("\nMarketplace search")
        print(
            "Configure location, radius, condition, and sorting on Facebook, then paste the results URL."
        )
        name = _display_value(search["name"]) if search["name"] else "required"
        url = _display_value(search["url"]) if search["url"] else "required"
        print(f"  1. Name: {name}")
        print(f"  2. Marketplace URL: {url}")
        print(f"  3. Minimum price: {_display_value(search['min_price'])}")
        print(f"  4. Maximum price: {_display_value(search['max_price'])}")
        print(f"  5. Exact title phrases: {_display_value(search['include_any'])}")
        print(f"  6. Excluded title phrases: {_display_value(search['exclude'])}")
        print(f"  7. Minimum relevance: {search['minimum_relevance']}")
        print(
            f"  8. Hard radius in miles: {_display_value(search['max_distance_miles'])}"
        )
        print("\n  S. Save search    Q. Cancel")
        choice = input("Select a setting: ").strip().casefold()
        if choice == "q":
            return None
        if choice == "s":
            if not search["name"]:
                print("Name is required.")
            elif not search["url"]:
                print("Marketplace URL is required.")
            elif not search["include_any"]:
                print("Enter at least one distinctive exact-title phrase.")
            elif (
                search["min_price"] is not None
                and search["max_price"] is not None
                and search["min_price"] > search["max_price"]
            ):
                print("Minimum price cannot exceed maximum price.")
            else:
                return search
        elif choice == "1":
            search["name"] = _prompt_text("Search name", search["name"])
        elif choice == "2":
            search["url"] = _prompt_text("Marketplace results URL", search["url"])
        elif choice == "3":
            search["min_price"] = _prompt_number(
                "Minimum price", search["min_price"], allow_none=True
            )
        elif choice == "4":
            search["max_price"] = _prompt_number(
                "Maximum price", search["max_price"], allow_none=True
            )
        elif choice == "5":
            search["include_any"] = _prompt_terms(
                "Exact title phrases", default=tuple(search["include_any"])
            )
        elif choice == "6":
            search["exclude"] = _prompt_terms(
                "Excluded title phrases", default=tuple(search["exclude"])
            )
        elif choice == "7":
            relevance = _prompt_number(
                "Minimum relevance (0 to 1)", search["minimum_relevance"]
            )
            if relevance is not None and relevance <= 1:
                search["minimum_relevance"] = relevance
            else:
                print("Minimum relevance must be between 0 and 1.")
        elif choice == "8":
            search["max_distance_miles"] = _prompt_number(
                "Maximum distance in miles",
                search["max_distance_miles"],
                minimum=0.1,
                allow_none=True,
            )
        else:
            print("Select a setting number, S to save, or Q to cancel.")


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
            distance_filter = (
                DistanceFilter(config.database_path)
                if any(
                    search.max_distance_miles is not None
                    for search in selected_searches
                )
                else None
            )
            try:
                if distance_filter is None:
                    listings = await fetch_listings(
                        config.browser,
                        selected_searches,
                    )
                else:
                    listings = await fetch_listings(
                        config.browser,
                        selected_searches,
                        distance_filter=distance_filter,
                    )
            finally:
                if distance_filter is not None:
                    distance_filter.close()
            with ListingStore(config.database_path) as store:
                dismissed_ids = store.dismissed_listing_ids()
            listings = [
                listing
                for listing in listings
                if listing.listing_id not in dismissed_ids
            ]
            print(format_report(listings, selected_searches, limit=args.limit))
            return

        if args.once:
            summary = await run_once(config, notifier)
            print(
                f"Check complete: {summary.discovered} discovered, "
                f"{summary.matched} matched, {summary.new} new, "
                f"{summary.notified} notified, {summary.held} held, "
                f"{summary.dismissed} dismissed"
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
        document = None if args.defaults else _interactive_config(args.config)
        if document is None and not args.defaults:
            print("Cancelled; no configuration was created.")
            return True
        destination = create_config(
            args.config,
            force=args.force,
            document=document,
        )
        print(f"Created {destination}")
        print("Next: run 'marketmon login', then 'marketmon add'.")
        return True
    if args.command == "add":
        if args.source is None:
            search = _interactive_search()
            if search is None:
                print("Cancelled; no search was added.")
                return True
            names = add_search_documents(
                args.config,
                [search],
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
    if args.command == "feedback":
        config = load_config(args.config)
        disposition = None if args.disposition == "clear" else args.disposition
        with ListingStore(config.database_path) as store:
            store.set_disposition(args.listing_id, disposition)
        print(f"Listing {args.listing_id}: {args.disposition}")
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
    except EOFError:
        print("Cancelled")


if __name__ == "__main__":
    main()
