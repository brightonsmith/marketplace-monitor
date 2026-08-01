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
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

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

    def record(self, listing: Listing) -> bool:
        """Record a listing and return True only when its ID is new."""
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO listings (
                listing_id, title, url, search_name, price_cents, location,
                first_seen_utc, last_seen_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing.listing_id,
                listing.title,
                listing.url,
                listing.search_name,
                listing.price_cents,
                listing.location,
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
                    location = ?, last_seen_utc = ?
                WHERE listing_id = ?
                """,
                (
                    listing.title,
                    listing.url,
                    listing.search_name,
                    listing.price_cents,
                    listing.location,
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
            SELECT listing_id, title, url, search_name, price_cents, location
            FROM listings
            WHERE notified_utc IS NULL
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
            )
            for row in rows
        ]
