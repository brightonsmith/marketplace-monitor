from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import load_config

SERVICE_NAME = "marketmon.service"


class ServiceError(RuntimeError):
    """Raised when the user-level systemd service cannot be managed."""


def _require_systemd() -> None:
    if sys.platform != "linux" or shutil.which("systemctl") is None:
        raise ServiceError("Service management requires Linux with systemd.")


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, check=check, text=True)
    except FileNotFoundError as error:
        raise ServiceError(f"Required service command is unavailable: {args[0]}") from error
    except subprocess.CalledProcessError as error:
        raise ServiceError(
            f"Service command failed ({error.returncode}): {' '.join(args)}"
        ) from error


def _quote(value: str | Path) -> str:
    return json.dumps(str(value))


def service_path() -> Path:
    return Path.home() / ".config/systemd/user" / SERVICE_NAME


def unit_text(config_path: str | Path) -> str:
    config = Path(config_path).expanduser().resolve()
    executable = Path(sys.executable).resolve()
    environment_file = config.parent / "environment"
    return f"""[Unit]
Description=Facebook Marketplace Monitor
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={_quote(config.parent)}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-{_quote(environment_file)}
ExecStart={_quote(executable)} -m marketplace_monitor.cli watch -c {_quote(config)}
Restart=always
RestartSec=60

[Install]
WantedBy=default.target
"""


def install_service(config_path: str | Path) -> Path:
    _require_systemd()
    config = Path(config_path).expanduser().resolve()
    load_config(config)

    system_service = _run(
        "systemctl", "is-active", "--quiet", SERVICE_NAME, check=False
    )
    if system_service.returncode == 0:
        raise ServiceError(
            "A system-wide marketmon service is already running. Disable it before "
            "installing the user service to avoid duplicate notifications."
        )

    destination = service_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(unit_text(config), encoding="utf-8")
    _run("systemctl", "--user", "daemon-reload")
    _run("systemctl", "--user", "enable", "--now", SERVICE_NAME)
    return destination


def service_status() -> None:
    _require_systemd()
    _run("systemctl", "--user", "status", SERVICE_NAME, "--no-pager")


def service_logs(*, follow: bool = False) -> None:
    _require_systemd()
    args = ["journalctl", "--user", "-u", SERVICE_NAME, "-n", "100"]
    if follow:
        args.append("--follow")
    else:
        args.append("--no-pager")
    _run(*args)


def restart_service() -> None:
    _require_systemd()
    _run("systemctl", "--user", "restart", SERVICE_NAME)


def uninstall_service() -> None:
    _require_systemd()
    _run("systemctl", "--user", "disable", "--now", SERVICE_NAME, check=False)
    service_path().unlink(missing_ok=True)
    _run("systemctl", "--user", "daemon-reload")


def linger_command() -> str:
    user = os.environ.get("USER") or getpass.getuser()
    return f"sudo loginctl enable-linger {user}"
