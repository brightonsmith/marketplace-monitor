import sys
from pathlib import Path

from marketplace_monitor.service import SERVICE_NAME, unit_text


def test_user_service_uses_current_python_and_absolute_config(tmp_path: Path) -> None:
    config = tmp_path / "settings/config.yaml"
    text = unit_text(config)

    assert SERVICE_NAME == "marketmon.service"
    assert str(Path(sys.executable).resolve()) in text
    assert str(config.resolve()) in text
    assert str((config.parent / "environment").resolve()) in text
    assert "marketplace_monitor.cli watch" in text
    assert "Restart=always" in text
