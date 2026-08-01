from __future__ import annotations

import os
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .config import (
    ConfigError,
    load_config,
    load_config_document,
    parse_config_document,
)
from .storage import ListingStore

TEMPLATE_FILES = {
    "config": "config.yaml",
    "search": "search.yaml",
}


def template_text(kind: str) -> str:
    try:
        template_name = TEMPLATE_FILES[kind]
    except KeyError as error:
        raise ConfigError(f"Unknown template type: {kind}") from error
    return (
        resources.files("marketplace_monitor")
        .joinpath(f"templates/{template_name}")
        .read_text(encoding="utf-8")
    )


def write_template(
    kind: str,
    path: str | Path,
    *,
    force: bool = False,
) -> Path:
    destination = Path(path)
    if destination.exists() and not force:
        raise ConfigError(f"File already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(template_text(kind), encoding="utf-8")
    return destination


def create_config(path: str | Path, *, force: bool = False) -> Path:
    destination = write_template("config", path, force=force)
    load_config(destination)
    return destination


def _write_document(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            yaml.safe_dump(document, stream, sort_keys=False, allow_unicode=True)
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _source_searches(path: str | Path) -> list[dict[str, Any]]:
    source_path = Path(path)
    raw = load_config_document(source_path)
    if "searches" in raw:
        searches = raw["searches"]
    elif "name" in raw or "url" in raw:
        searches = [raw]
    else:
        raise ConfigError(
            "Search file must be one search mapping or contain a searches list"
        )
    if not isinstance(searches, list) or not searches:
        raise ConfigError("Search file contains no searches")
    if not all(isinstance(search, dict) for search in searches):
        raise ConfigError("Every search must be a YAML mapping")
    return searches


def add_searches(
    config_path: str | Path,
    source_path: str | Path,
    *,
    replace: bool = False,
) -> tuple[str, ...]:
    destination = Path(config_path)
    document = load_config_document(destination)
    existing = document.get("searches")
    if not isinstance(existing, list):
        raise ConfigError("searches must be a list")

    merged = list(existing)
    positions = {
        str(search.get("name", "")).strip().casefold(): index
        for index, search in enumerate(merged)
        if isinstance(search, dict)
    }
    added: list[str] = []
    for search in _source_searches(source_path):
        name = search.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("Every added search requires a name")
        key = name.strip().casefold()
        if key in positions and not replace:
            raise ConfigError(
                f"Search already active: {name.strip()}. Use --replace to update it."
            )
        if key in positions:
            merged[positions[key]] = search
        else:
            positions[key] = len(merged)
            merged.append(search)
        added.append(name.strip())

    candidate = dict(document)
    candidate["searches"] = merged
    parse_config_document(candidate, destination)
    _write_document(destination, candidate)
    return tuple(added)


def remove_search(config_path: str | Path, name: str) -> str:
    destination = Path(config_path)
    app_config = load_config(destination)
    document = load_config_document(destination)
    existing = document.get("searches")
    if not isinstance(existing, list):
        raise ConfigError("searches must be a list")
    key = name.strip().casefold()
    matches = [
        search
        for search in existing
        if isinstance(search, dict)
        and str(search.get("name", "")).strip().casefold() == key
    ]
    if not matches:
        raise ConfigError(f"Active search not found: {name}")
    remaining = [search for search in existing if search not in matches]
    candidate = dict(document)
    candidate["searches"] = remaining
    parse_config_document(candidate, destination)
    _write_document(destination, candidate)
    removed_name = str(matches[0]["name"]).strip()
    with ListingStore(app_config.database_path) as store:
        store.cancel_pending_search(removed_name)
    return removed_name


def active_searches(config_path: str | Path):
    return load_config(config_path).searches
