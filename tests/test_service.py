import sys
from pathlib import Path

import marketplace_monitor.service as service
from marketplace_monitor.service import (
    DASHBOARD_SERVICE_NAME,
    SERVICE_NAME,
    dashboard_unit_text,
    service_logs,
    unit_text,
)


def test_user_service_uses_current_python_and_absolute_config(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "settings/config.yaml"
    venv_python = tmp_path / ".venv/bin/python"
    monkeypatch.setattr(sys, "executable", str(venv_python))
    text = unit_text(config)

    assert SERVICE_NAME == "marketmon.service"
    assert str(venv_python) in text
    assert str(config.resolve()) in text
    assert str((config.parent / "environment").resolve()) in text
    assert f"WorkingDirectory={config.parent.resolve()}" in text
    assert f'WorkingDirectory="{config.parent.resolve()}"' not in text
    assert f"EnvironmentFile=-{(config.parent / 'environment').resolve()}" in text
    assert "marketplace_monitor.cli watch" in text
    assert "Restart=always" in text

    dashboard = dashboard_unit_text(config)
    assert DASHBOARD_SERVICE_NAME == "marketmon-dashboard.service"
    assert str(venv_python) in dashboard
    assert "marketplace_monitor.cli dashboard" in dashboard
    assert "--host 0.0.0.0 --port 8000" in dashboard
    assert "After=network-online.target tailscaled.service" in dashboard


def test_user_service_escapes_scalar_paths(tmp_path: Path) -> None:
    config = tmp_path / "settings with spaces/config.yaml"

    text = unit_text(config)

    assert f"WorkingDirectory={str(config.parent.resolve()).replace(' ', r'\x20')}" in text
    assert (
        f"EnvironmentFile=-{str((config.parent / 'environment').resolve()).replace(' ', r'\x20')}"
        in text
    )


def test_service_logs_reads_volatile_user_unit_journal(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(service, "_require_systemd", lambda: None)
    monkeypatch.setattr(service, "_run", lambda *args, **kwargs: calls.append(args))

    service_logs()
    service_logs(follow=True)

    assert calls == [
        (
            "journalctl",
            "--user-unit=marketmon.service",
            "--user-unit=marketmon-dashboard.service",
            "-n",
            "100",
            "--no-pager",
        ),
        (
            "journalctl",
            "--user-unit=marketmon.service",
            "--user-unit=marketmon-dashboard.service",
            "-n",
            "100",
            "--follow",
        ),
    ]
