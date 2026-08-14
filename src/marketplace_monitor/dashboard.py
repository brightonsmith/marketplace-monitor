from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

from .config import load_config
from .models import SearchConfig
from .notifier import format_price
from .ranking import RankedListing
from .storage import ListingStore, StoredCandidate


@dataclass(frozen=True)
class DashboardListing:
    candidate: RankedListing
    first_seen_utc: str
    last_seen_utc: str
    disposition: str | None
    is_current: bool


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
