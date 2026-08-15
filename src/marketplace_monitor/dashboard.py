from __future__ import annotations

import asyncio
import copy
import hmac
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from playwright.async_api import Error as PlaywrightError

from .browser import BrowserSessionError
from .config import (
    ConfigError,
    load_config,
    load_config_document,
    parse_config_document,
)
from .config_manager import (
    add_search_documents,
    replace_search_document,
    search_document,
    update_global_settings,
)
from .geocoding import GeocodingError
from .models import SearchConfig
from .notifier import format_price
from .ranking import RankedListing
from .storage import ListingStore, StoredCandidate
from .suggestions import PhraseSuggestionReport, fetch_phrase_suggestions


@dataclass(frozen=True)
class DashboardListing:
    candidate: RankedListing
    first_seen_utc: str
    last_seen_utc: str
    disposition: str | None
    is_current: bool


def _terms_from_form(value: str) -> list[str]:
    return [term.strip() for term in re.split(r"[,\n]+", value) if term.strip()]


def _optional_number(value: str, label: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as error:
        raise ConfigError(f"{label} must be a number or left blank") from error


def _search_form_values(document: dict) -> dict[str, str]:
    def optional_value(name: str) -> str:
        value = document.get(name)
        return "" if value is None else str(value)

    return {
        "name": str(document.get("name", "")),
        "url": str(document.get("url", "")),
        "min_price": optional_value("min_price"),
        "max_price": optional_value("max_price"),
        "include_any": "\n".join(document.get("include_any") or ()),
        "exclude": "\n".join(document.get("exclude") or ()),
        "minimum_relevance": str(document.get("minimum_relevance", 0.20)),
        "max_distance_miles": optional_value("max_distance_miles"),
    }


def _search_document_from_form(
    values: dict[str, str],
    *,
    require_exact_phrases: bool = True,
) -> dict:
    name = values["name"].strip()
    url = values["url"].strip()
    include_any = _terms_from_form(values["include_any"])
    if not name:
        raise ConfigError("Search name is required")
    if not url:
        raise ConfigError("Marketplace URL is required")
    if require_exact_phrases and not include_any:
        raise ConfigError("Enter at least one exact-title phrase")
    return {
        "name": name,
        "url": url,
        "min_price": _optional_number(values["min_price"], "Minimum price"),
        "max_price": _optional_number(values["max_price"], "Maximum price"),
        "include_any": include_any,
        "exclude": _terms_from_form(values["exclude"]),
        "minimum_relevance": _optional_number(
            values["minimum_relevance"], "Minimum relevance"
        ),
        "max_distance_miles": _optional_number(
            values["max_distance_miles"], "Hard radius"
        ),
    }


def _suggestions_for_search_document(
    config_file: Path,
    search: dict,
) -> PhraseSuggestionReport:
    raw_config = copy.deepcopy(load_config_document(config_file))
    raw_config["searches"] = [search]
    draft_config = parse_config_document(raw_config, config_file)
    return asyncio.run(
        fetch_phrase_suggestions(draft_config, draft_config.searches[0])
    )


def _monitor_is_stale(completed_utc: str | None, interval_minutes: int) -> bool:
    if completed_utc is None:
        return False
    try:
        completed = datetime.fromisoformat(completed_utc)
    except ValueError:
        return True
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=UTC)
    tolerance = timedelta(minutes=max(5, interval_minutes * 2))
    return datetime.now(UTC) - completed > tolerance


def _clock_value(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    return f"{hours:02d}:{remainder:02d}"


def _format_local_datetime(
    value: str | None,
    timezone_name: str,
    time_format: str,
) -> str:
    if not value:
        return ""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    local = parsed.astimezone(ZoneInfo(timezone_name))
    clock = "%I:%M %p" if time_format == "12h" else "%H:%M"
    rendered = (
        f"{local.strftime('%b')} {local.day}, {local.year} · "
        f"{local.strftime(clock)}"
    )
    return rendered.replace(" 0", " ")


def _group_listings(
    stored: list[StoredCandidate],
    searches: tuple[SearchConfig, ...],
    limit: int,
) -> list[tuple[str, list[DashboardListing]]]:
    names = [search.name for search in searches]
    names.extend(
        sorted(
            {record.listing.search_name for record in stored} - set(names),
            key=str.casefold,
        )
    )
    groups: list[tuple[str, list[DashboardListing]]] = []
    for name in names:
        candidates = [
            DashboardListing(
                candidate=RankedListing(
                    listing=record.listing,
                    relevance=record.relevance,
                    score=record.score,
                    exact=record.exact,
                    excluded=False,
                ),
                first_seen_utc=record.first_seen_utc,
                last_seen_utc=record.last_seen_utc,
                disposition=record.disposition,
                is_current=record.is_current,
            )
            for record in stored
            if record.listing.search_name == name
        ][:limit]
        groups.append((name, candidates))
    return groups


def create_app(config_path: Path) -> Flask:
    config_path = config_path.resolve()
    load_config(config_path)
    app = Flask(__name__)
    app.config["MARKETMON_CONFIG_PATH"] = config_path
    app.config["MARKETMON_CSRF_TOKEN"] = secrets.token_urlsafe(32)

    @app.after_request
    def secure_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' https: data:; "
            "style-src 'self'; "
            "script-src 'self'; "
            "connect-src 'self'; "
            "form-action 'self' https://www.facebook.com; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )
        if response.mimetype in {"text/html", "application/json"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.template_filter("price")
    def price_filter(value: int | None) -> str:
        return format_price(value)

    @app.template_filter("local_datetime")
    def local_datetime_filter(
        value: str | None,
        timezone_name: str,
        time_format: str,
    ) -> str:
        return _format_local_datetime(value, timezone_name, time_format)

    @app.context_processor
    def display_preferences():
        current = load_config(app.config["MARKETMON_CONFIG_PATH"])
        return {
            "display_timezone": current.timezone,
            "display_time_format": current.time_format,
        }

    @app.get("/")
    def index():
        view = request.args.get("view", "active")
        if view not in {"active", "interested", "dismissed"}:
            abort(404)
        try:
            limit = int(request.args.get("limit", "10"))
        except ValueError:
            abort(400)
        if limit not in {5, 10, 25, 50}:
            abort(400)
        current = load_config(app.config["MARKETMON_CONFIG_PATH"])
        with ListingStore(current.database_path) as store:
            stored = store.dashboard_listings(view)
            counts = store.disposition_counts()
            recent_runs = store.recent_runs(8)
            dashboard_updated_utc = store.dashboard_updated_utc()
        latest_run = recent_runs[0] if recent_runs else None
        monitor_stale = _monitor_is_stale(
            latest_run.completed_utc if latest_run else None,
            current.check_interval_minutes,
        )
        return render_template(
            "dashboard.html",
            groups=_group_listings(stored, current.searches, limit),
            counts=counts,
            view=view,
            limit=limit,
            recent_runs=recent_runs,
            latest_run=latest_run,
            monitor_stale=monitor_stale,
            dashboard_updated_utc=dashboard_updated_utc,
        )

    @app.get("/api/status")
    def status():
        current = load_config(app.config["MARKETMON_CONFIG_PATH"])
        with ListingStore(current.database_path) as store:
            recent_runs = store.recent_runs(1)
            counts = store.disposition_counts()
            dashboard_updated_utc = store.dashboard_updated_utc()
        latest_run = asdict(recent_runs[0]) if recent_runs else None
        return jsonify(
            {
                "latest_run": latest_run,
                "counts": counts,
                "dashboard_updated_utc": dashboard_updated_utc,
                "monitor_stale": _monitor_is_stale(
                    recent_runs[0].completed_utc if recent_runs else None,
                    current.check_interval_minutes,
                ),
            }
        )

    @app.get("/healthz")
    def health():
        current = load_config(app.config["MARKETMON_CONFIG_PATH"])
        with ListingStore(current.database_path) as store:
            recent_runs = store.recent_runs(1)
        monitor_stale = _monitor_is_stale(
            recent_runs[0].completed_utc if recent_runs else None,
            current.check_interval_minutes,
        )
        return jsonify(
            {
                "status": "stale" if monitor_stale else "ok",
                "latest_run_utc": (
                    recent_runs[0].completed_utc if recent_runs else None
                ),
            }
        )

    @app.get("/service-worker.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "service-worker.js")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.route("/settings", methods=("GET", "POST"))
    def settings():
        config_file = app.config["MARKETMON_CONFIG_PATH"]
        error = None
        if request.method == "POST":
            supplied_token = request.form.get("csrf_token", "")
            if not hmac.compare_digest(
                supplied_token,
                app.config["MARKETMON_CSRF_TOKEN"],
            ):
                abort(400)
            try:
                check_interval = int(request.form.get("check_interval_minutes", ""))
                status_interval = int(
                    request.form.get("status_interval_minutes", "")
                )
                digest_interval = int(
                    request.form.get("digest_interval_minutes", "")
                )
                if check_interval not in {5, 10, 15, 30, 60, 120}:
                    raise ConfigError("Select a supported Marketplace check interval")
                if status_interval not in {0, 60, 360, 1440}:
                    raise ConfigError("Select a supported status update interval")
                if digest_interval not in {30, 60, 180, 1440}:
                    raise ConfigError("Select a supported digest interval")
                quiet_hours = None
                if request.form.get("quiet_hours_enabled") == "on":
                    quiet_hours = {
                        "start": request.form.get("quiet_hours_start", ""),
                        "end": request.form.get("quiet_hours_end", ""),
                    }
                update_global_settings(
                    config_file,
                    check_interval_minutes=check_interval,
                    delivery_mode=request.form.get("delivery_mode", ""),
                    digest_interval_minutes=digest_interval,
                    status_interval_minutes=status_interval,
                    notify_on_startup=request.form.get("notify_on_startup") == "on",
                    quiet_hours=quiet_hours,
                    timezone=request.form.get("timezone", ""),
                    time_format=request.form.get("time_format", ""),
                )
            except (ConfigError, ValueError) as caught:
                error = str(caught)
            else:
                return redirect(url_for("settings", saved="1"), code=303)

        current = load_config(config_file)
        quiet_hours = current.quiet_hours
        return (
            render_template(
                "settings.html",
                config=current,
                quiet_hours_start=(
                    _clock_value(quiet_hours.start_minutes) if quiet_hours else "22:00"
                ),
                quiet_hours_end=(
                    _clock_value(quiet_hours.end_minutes) if quiet_hours else "07:00"
                ),
                timezone_options=sorted(available_timezones()),
                csrf_token=app.config["MARKETMON_CSRF_TOKEN"],
                saved=request.args.get("saved") == "1",
                error=error,
            ),
            400 if error else 200,
        )

    @app.get("/searches")
    def searches():
        current = load_config(app.config["MARKETMON_CONFIG_PATH"])
        return render_template(
            "searches.html",
            searches=current.searches,
            saved=request.args.get("saved"),
        )

    @app.route("/searches/new", methods=("GET", "POST"))
    def add_search():
        config_file = app.config["MARKETMON_CONFIG_PATH"]
        error = None
        suggestion_report: PhraseSuggestionReport | None = None
        if request.method == "POST":
            supplied_token = request.form.get("csrf_token", "")
            if not hmac.compare_digest(
                supplied_token,
                app.config["MARKETMON_CSRF_TOKEN"],
            ):
                abort(400)
            values = {
                field: request.form.get(field, "")
                for field in (
                    "name",
                    "url",
                    "min_price",
                    "max_price",
                    "include_any",
                    "exclude",
                    "minimum_relevance",
                    "max_distance_miles",
                )
            }
            try:
                action = request.form.get("action", "save")
                document = _search_document_from_form(
                    values,
                    require_exact_phrases=action != "suggest",
                )
                if action == "suggest":
                    suggestion_report = _suggestions_for_search_document(
                        config_file,
                        document,
                    )
                    form = values
                elif action == "save":
                    added_name = add_search_documents(
                        config_file,
                        [document],
                    )[0]
                else:
                    abort(400)
            except (
                BrowserSessionError,
                ConfigError,
                GeocodingError,
                PlaywrightError,
            ) as caught:
                error = str(caught)
                form = values
            else:
                if action == "save":
                    return redirect(
                        url_for("searches", saved=added_name), code=303
                    )
        else:
            form = _search_form_values(
                {
                    "name": "",
                    "url": "",
                    "include_any": [],
                    "exclude": [
                        "wanted",
                        "looking for",
                        "broken",
                        "for parts",
                        "parts only",
                    ],
                    "minimum_relevance": 0.20,
                }
            )
        return (
            render_template(
                "edit_search.html",
                original_name=None,
                form=form,
                error=error,
                suggestion_report=suggestion_report,
                csrf_token=app.config["MARKETMON_CSRF_TOKEN"],
            ),
            400 if error else 200,
        )

    @app.route("/searches/<path:name>/edit", methods=("GET", "POST"))
    def edit_search(name: str):
        config_file = app.config["MARKETMON_CONFIG_PATH"]
        document = search_document(config_file, name)
        error = None
        suggestion_report: PhraseSuggestionReport | None = None
        if request.method == "POST":
            supplied_token = request.form.get("csrf_token", "")
            if not hmac.compare_digest(
                supplied_token,
                app.config["MARKETMON_CSRF_TOKEN"],
            ):
                abort(400)
            values = {
                field: request.form.get(field, "")
                for field in (
                    "name",
                    "url",
                    "min_price",
                    "max_price",
                    "include_any",
                    "exclude",
                    "minimum_relevance",
                    "max_distance_miles",
                )
            }
            try:
                action = request.form.get("action", "save")
                replacement = _search_document_from_form(
                    values,
                    require_exact_phrases=action != "suggest",
                )
                if action == "suggest":
                    suggestion_report = _suggestions_for_search_document(
                        config_file,
                        replacement,
                    )
                    form = values
                elif action == "save":
                    updated_name = replace_search_document(
                        config_file,
                        name,
                        replacement,
                    )
                else:
                    abort(400)
            except (
                BrowserSessionError,
                ConfigError,
                GeocodingError,
                PlaywrightError,
            ) as caught:
                error = str(caught)
                form = values
            else:
                if action == "save":
                    return redirect(
                        url_for("searches", saved=updated_name), code=303
                    )
        else:
            form = _search_form_values(document)
        return (
            render_template(
                "edit_search.html",
                original_name=name,
                form=form,
                error=error,
                suggestion_report=suggestion_report,
                csrf_token=app.config["MARKETMON_CSRF_TOKEN"],
            ),
            400 if error else 200,
        )

    @app.get("/listings/<listing_id>")
    def listing_detail(listing_id: str):
        current = load_config(app.config["MARKETMON_CONFIG_PATH"])
        with ListingStore(current.database_path) as store:
            record = store.dashboard_listing(listing_id)
        if record is None:
            abort(404)
        listing = DashboardListing(
            candidate=RankedListing(
                listing=record.listing,
                relevance=record.relevance,
                score=record.score,
                exact=record.exact,
                excluded=False,
            ),
            first_seen_utc=record.first_seen_utc,
            last_seen_utc=record.last_seen_utc,
            disposition=record.disposition,
            is_current=record.is_current,
        )
        return render_template("listing.html", record=listing)

    @app.post("/listings/<listing_id>/feedback")
    def feedback(listing_id: str):
        disposition_value = request.form.get("disposition", "")
        disposition = None if disposition_value == "clear" else disposition_value
        if disposition not in {None, "interested", "dismissed"}:
            abort(400)
        current = load_config(app.config["MARKETMON_CONFIG_PATH"])
        with ListingStore(current.database_path) as store:
            listing_url = store.dashboard_listing_url(listing_id)
            if disposition == "interested" and listing_url is None:
                abort(404)
            store.set_disposition(listing_id, disposition)
        if disposition == "interested":
            return redirect(listing_url, code=303)
        response = redirect(
            url_for(
                "index",
                view=request.form.get("view", "active"),
                limit=request.form.get("limit", "10"),
            ),
            code=303,
        )
        return response

    return app


def run_dashboard(
    config_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    app = create_app(config_path)
    print(f"Dashboard: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
