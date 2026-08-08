from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for

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
        return render_template(
            "dashboard.html",
            groups=_group_listings(stored, current.searches, limit),
            counts=counts,
            view=view,
            limit=limit,
            recent_runs=recent_runs,
        )

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
        if request.headers.get("HX-Request") == "true":
            response = app.response_class(status=204)
            response.headers["HX-Refresh"] = "true"
            return response
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
