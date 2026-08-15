from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import Listing
from .ranking import RankedListing


@dataclass(frozen=True)
class StoredCandidate:
    listing: Listing
    relevance: float
    score: float
    exact: bool
    first_seen_utc: str
    last_seen_utc: str
    disposition: str | None
    is_current: bool


@dataclass(frozen=True)
class RunRecord:
    completed_utc: str
    discovered: int
    matched: int
    new: int
    notified: int
    held: int
    dismissed: int


class ListingStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS listings (
                listing_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                search_name TEXT NOT NULL,
                price_cents INTEGER,
                location TEXT,
                first_seen_utc TEXT NOT NULL,
                last_seen_utc TEXT NOT NULL,
                notified_utc TEXT
            )
            """
        )
        self._ensure_column("listings", "distance_miles", "REAL")
        self._ensure_column("listings", "image_url", "TEXT")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dashboard_candidates (
                listing_id TEXT NOT NULL,
                search_name TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                price_cents INTEGER,
                location TEXT,
                distance_miles REAL,
                image_url TEXT,
                relevance REAL NOT NULL,
                score REAL NOT NULL,
                exact INTEGER NOT NULL,
                first_seen_utc TEXT NOT NULL,
                last_seen_utc TEXT NOT NULL,
                PRIMARY KEY (listing_id, search_name)
            )
            """
        )
        self._ensure_column(
            "dashboard_candidates", "is_current", "INTEGER NOT NULL DEFAULT 1"
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_history (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                completed_utc TEXT NOT NULL,
                discovered INTEGER NOT NULL,
                matched INTEGER NOT NULL,
                new_count INTEGER NOT NULL,
                notified INTEGER NOT NULL,
                held INTEGER NOT NULL,
                dismissed INTEGER NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS listing_feedback (
                listing_id TEXT PRIMARY KEY,
                disposition TEXT NOT NULL,
                updated_utc TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def _ensure_column(self, table: str, name: str, declaration: str) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        }
        if name not in columns:
            self.connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
            )

    def __enter__(self) -> "ListingStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def is_initialized(self) -> bool:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'initialized'"
        ).fetchone()
        return bool(row and row["value"] == "true")

    def mark_initialized(self) -> None:
        self.connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES ('initialized', 'true')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        self.connection.commit()

    def prepare_search_baselines(self, search_names: tuple[str, ...]) -> None:
        """Migrate an existing database to per-search initialization state."""
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'search_baselines_enabled'"
        ).fetchone()
        if row and row["value"] == "true":
            return
        if self.is_initialized():
            for search_name in search_names:
                self._mark_search_initialized(search_name)
        self.connection.execute(
            """
            INSERT INTO metadata (key, value)
            VALUES ('search_baselines_enabled', 'true')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        self.connection.commit()

    def is_search_initialized(self, search_name: str) -> bool:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (f"search_initialized:{search_name.casefold()}",),
        ).fetchone()
        return bool(row and row["value"] == "true")

    def _mark_search_initialized(self, search_name: str) -> None:
        self.connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, 'true')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (f"search_initialized:{search_name.casefold()}",),
        )

    def mark_search_initialized(self, search_name: str) -> None:
        self._mark_search_initialized(search_name)
        self.connection.commit()

    def record(self, listing: Listing) -> bool:
        """Record a listing and return True only when its ID is new."""
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO listings (
                listing_id, title, url, search_name, price_cents, location,
                distance_miles, image_url, first_seen_utc, last_seen_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing.listing_id,
                listing.title,
                listing.url,
                listing.search_name,
                listing.price_cents,
                listing.location,
                listing.distance_miles,
                listing.image_url,
                now,
                now,
            ),
        )
        is_new = cursor.rowcount == 1
        if not is_new:
            self.connection.execute(
                """
                UPDATE listings
                SET title = ?, url = ?, search_name = ?, price_cents = ?,
                    location = ?, distance_miles = ?,
                    image_url = COALESCE(?, image_url), last_seen_utc = ?
                WHERE listing_id = ?
                """,
                (
                    listing.title,
                    listing.url,
                    listing.search_name,
                    listing.price_cents,
                    listing.location,
                    listing.distance_miles,
                    listing.image_url,
                    now,
                    listing.listing_id,
                ),
            )
        self.connection.commit()
        return is_new

    def mark_notified(self, listing_id: str) -> None:
        self.mark_notified_many((listing_id,))

    def mark_notified_many(self, listing_ids: tuple[str, ...]) -> None:
        if not listing_ids:
            return
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            f"UPDATE listings SET notified_utc = ? WHERE listing_id IN "
            f"({','.join('?' for _ in listing_ids)})",
            (now, *listing_ids),
        )
        self.connection.commit()

    def metadata_value(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_metadata_value(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO metadata (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.connection.commit()

    def delete_metadata_value(self, key: str) -> None:
        self.connection.execute("DELETE FROM metadata WHERE key = ?", (key,))
        self.connection.commit()

    def needs_notification(self, listing_id: str) -> bool:
        row = self.connection.execute(
            "SELECT notified_utc FROM listings WHERE listing_id = ?",
            (listing_id,),
        ).fetchone()
        return bool(row and row["notified_utc"] is None)

    def pending_listings(self) -> list[Listing]:
        rows = self.connection.execute(
            """
            SELECT listing_id, title, url, search_name, price_cents, location,
                   distance_miles, image_url
            FROM listings
            WHERE notified_utc IS NULL
              AND listing_id NOT IN (
                  SELECT listing_id
                  FROM listing_feedback
                  WHERE disposition = 'dismissed'
              )
            ORDER BY first_seen_utc
            """
        ).fetchall()
        return [
            Listing(
                listing_id=row["listing_id"],
                title=row["title"],
                url=row["url"],
                search_name=row["search_name"],
                price_cents=row["price_cents"],
                location=row["location"],
                distance_miles=row["distance_miles"],
                image_url=row["image_url"],
            )
            for row in rows
        ]

    def cancel_pending_search(self, search_name: str) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            UPDATE listings
            SET notified_utc = ?
            WHERE notified_utc IS NULL AND search_name = ? COLLATE NOCASE
            """,
            (now, search_name),
        )
        self.connection.commit()
        return cursor.rowcount

    def set_disposition(self, listing_id: str, disposition: str | None) -> None:
        if disposition not in {None, "interested", "dismissed"}:
            raise ValueError(f"Unsupported disposition: {disposition}")
        now = datetime.now(UTC).isoformat()
        if disposition is None:
            self.connection.execute(
                "DELETE FROM listing_feedback WHERE listing_id = ?",
                (listing_id,),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO listing_feedback (listing_id, disposition, updated_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(listing_id) DO UPDATE SET
                    disposition = excluded.disposition,
                    updated_utc = excluded.updated_utc
                """,
                (listing_id, disposition, now),
            )
        if disposition == "dismissed":
            self.connection.execute(
                """
                UPDATE listings
                SET notified_utc = COALESCE(notified_utc, ?)
                WHERE listing_id = ?
                """,
                (now, listing_id),
            )
        self.connection.commit()

    def dismissed_listing_ids(self) -> set[str]:
        rows = self.connection.execute(
            """
            SELECT listing_id
            FROM listing_feedback
            WHERE disposition = 'dismissed'
            """
        ).fetchall()
        return {row["listing_id"] for row in rows}

    def replace_dashboard_candidates(
        self,
        search_names: tuple[str, ...],
        candidates: list[RankedListing],
    ) -> None:
        """Replace current report snapshots without changing notification history."""
        now = datetime.now(UTC).isoformat()
        for search_name in search_names:
            self.connection.execute(
                """
                UPDATE dashboard_candidates
                SET is_current = 0
                WHERE search_name = ? COLLATE NOCASE
                """,
                (search_name,),
            )
        for candidate in candidates:
            listing = candidate.listing
            self.connection.execute(
                """
                INSERT INTO dashboard_candidates (
                    listing_id, search_name, title, url, price_cents, location,
                    distance_miles, image_url, relevance, score, exact,
                    first_seen_utc, last_seen_utc, is_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(listing_id, search_name) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    price_cents = excluded.price_cents,
                    location = excluded.location,
                    distance_miles = excluded.distance_miles,
                    image_url = COALESCE(
                        excluded.image_url,
                        dashboard_candidates.image_url
                    ),
                    relevance = excluded.relevance,
                    score = excluded.score,
                    exact = excluded.exact,
                    last_seen_utc = excluded.last_seen_utc,
                    is_current = 1
                """,
                (
                    listing.listing_id,
                    listing.search_name,
                    listing.title,
                    listing.url,
                    listing.price_cents,
                    listing.location,
                    listing.distance_miles,
                    listing.image_url,
                    candidate.relevance,
                    candidate.score,
                    candidate.exact,
                    now,
                    now,
                ),
            )
        self.connection.execute(
            """
            DELETE FROM dashboard_candidates
            WHERE is_current = 0
              AND listing_id NOT IN (
                  SELECT listing_id
                  FROM listing_feedback
                  WHERE disposition = 'interested'
              )
            """
        )
        self.connection.execute(
            """
            INSERT INTO metadata (key, value)
            VALUES ('dashboard_updated_utc', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (now,),
        )
        self.connection.commit()

    def dashboard_updated_utc(self) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'dashboard_updated_utc'"
        ).fetchone()
        return row["value"] if row else None

    def dashboard_listing_url(self, listing_id: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT url
            FROM dashboard_candidates
            WHERE listing_id = ?
            ORDER BY last_seen_utc DESC
            LIMIT 1
            """,
            (listing_id,),
        ).fetchone()
        return row["url"] if row else None

    def dashboard_listing(self, listing_id: str) -> StoredCandidate | None:
        """Return the latest stored dashboard record for one listing."""
        row = self.connection.execute(
            """
            SELECT candidates.listing_id, candidates.title, candidates.url,
                   candidates.search_name, candidates.price_cents,
                   candidates.location, candidates.distance_miles,
                   candidates.image_url, candidates.relevance, candidates.score,
                   candidates.exact, candidates.first_seen_utc,
                   candidates.last_seen_utc, feedback.disposition,
                   candidates.is_current
            FROM dashboard_candidates AS candidates
            LEFT JOIN listing_feedback AS feedback
              ON feedback.listing_id = candidates.listing_id
            WHERE candidates.listing_id = ?
            ORDER BY candidates.last_seen_utc DESC
            LIMIT 1
            """,
            (listing_id,),
        ).fetchone()
        if row is None:
            return None
        return StoredCandidate(
            listing=Listing(
                listing_id=row["listing_id"],
                title=row["title"],
                url=row["url"],
                search_name=row["search_name"],
                price_cents=row["price_cents"],
                location=row["location"],
                distance_miles=row["distance_miles"],
                image_url=row["image_url"],
            ),
            relevance=row["relevance"],
            score=row["score"],
            exact=bool(row["exact"]),
            first_seen_utc=row["first_seen_utc"],
            last_seen_utc=row["last_seen_utc"],
            disposition=row["disposition"],
            is_current=bool(row["is_current"]),
        )

    def dashboard_listings(self, view: str = "active") -> list[StoredCandidate]:
        """Return current candidate snapshots and feedback for the dashboard."""
        if view not in {"active", "interested", "dismissed"}:
            raise ValueError(f"Unsupported dashboard view: {view}")
        where = {
            "active": "COALESCE(feedback.disposition, '') != 'dismissed'",
            "interested": "feedback.disposition = 'interested'",
            "dismissed": "feedback.disposition = 'dismissed'",
        }[view]
        rows = self.connection.execute(
            f"""
            SELECT candidates.listing_id, candidates.title, candidates.url,
                   candidates.search_name, candidates.price_cents,
                   candidates.location, candidates.distance_miles,
                   candidates.image_url, candidates.relevance, candidates.score,
                   candidates.exact, candidates.first_seen_utc,
                   candidates.last_seen_utc, feedback.disposition,
                   candidates.is_current
            FROM dashboard_candidates AS candidates
            LEFT JOIN listing_feedback AS feedback
              ON feedback.listing_id = candidates.listing_id
            WHERE {where}
            ORDER BY candidates.score DESC, candidates.last_seen_utc DESC
            """
        ).fetchall()
        return [
            StoredCandidate(
                listing=Listing(
                    listing_id=row["listing_id"],
                    title=row["title"],
                    url=row["url"],
                    search_name=row["search_name"],
                    price_cents=row["price_cents"],
                    location=row["location"],
                    distance_miles=row["distance_miles"],
                    image_url=row["image_url"],
                ),
                relevance=row["relevance"],
                score=row["score"],
                exact=bool(row["exact"]),
                first_seen_utc=row["first_seen_utc"],
                last_seen_utc=row["last_seen_utc"],
                disposition=row["disposition"],
                is_current=bool(row["is_current"]),
            )
            for row in rows
        ]

    def disposition_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            """
            SELECT COALESCE(feedback.disposition, 'active') AS disposition,
                   COUNT(*) AS total
            FROM dashboard_candidates AS candidates
            LEFT JOIN listing_feedback AS feedback
              ON feedback.listing_id = candidates.listing_id
            GROUP BY COALESCE(feedback.disposition, 'active')
            """
        ).fetchall()
        counts = {"active": 0, "interested": 0, "dismissed": 0}
        for row in rows:
            counts[row["disposition"]] = row["total"]
        counts["active"] += counts["interested"]
        return counts

    def record_run(
        self,
        *,
        discovered: int,
        matched: int,
        new: int,
        notified: int,
        held: int,
        dismissed: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO run_history (
                completed_utc, discovered, matched, new_count, notified, held,
                dismissed
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(),
                discovered,
                matched,
                new,
                notified,
                held,
                dismissed,
            ),
        )
        self.connection.commit()

    def recent_runs(self, limit: int = 10) -> list[RunRecord]:
        rows = self.connection.execute(
            """
            SELECT completed_utc, discovered, matched, new_count, notified, held,
                   dismissed
            FROM run_history
            ORDER BY run_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            RunRecord(
                completed_utc=row["completed_utc"],
                discovered=row["discovered"],
                matched=row["matched"],
                new=row["new_count"],
                notified=row["notified"],
                held=row["held"],
                dismissed=row["dismissed"],
            )
            for row in rows
        ]
