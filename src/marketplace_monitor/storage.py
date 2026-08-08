from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .models import Listing


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
                distance_miles, first_seen_utc, last_seen_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing.listing_id,
                listing.title,
                listing.url,
                listing.search_name,
                listing.price_cents,
                listing.location,
                listing.distance_miles,
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
                    location = ?, distance_miles = ?, last_seen_utc = ?
                WHERE listing_id = ?
                """,
                (
                    listing.title,
                    listing.url,
                    listing.search_name,
                    listing.price_cents,
                    listing.location,
                    listing.distance_miles,
                    now,
                    listing.listing_id,
                ),
            )
        self.connection.commit()
        return is_new

    def mark_notified(self, listing_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            "UPDATE listings SET notified_utc = ? WHERE listing_id = ?",
            (now, listing_id),
        )
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
                   distance_miles
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
