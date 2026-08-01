from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from .browser import interactive_login
from .config import ConfigError, load_config
from .monitor import run_once, watch
from .notifier import build_notifier


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor Facebook Marketplace searches")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="configuration file (default: config.yaml)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("login", help="open a browser and save a local Facebook session")
    subparsers.add_parser("run-once", help="check all configured searches once")
    subparsers.add_parser("watch", help="check continuously at the configured interval")
    return parser


async def _run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    if args.command == "login":
        await interactive_login(config.browser)
        return

    notifier = build_notifier(config.notifications)
    if args.command == "run-once":
        summary = await run_once(config, notifier)
        print(
            f"Check complete: {summary.discovered} discovered, "
            f"{summary.matched} matched, {summary.new} new, "
            f"{summary.notified} notified, {summary.held} held"
        )
        return
    await watch(config, notifier)


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(_run(args))
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
