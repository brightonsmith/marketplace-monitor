from __future__ import annotations

import asyncio
import os
import sqlite3
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Protocol

from geopy.exc import GeocoderServiceError
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim


class GeocodingError(RuntimeError):
    """Raised when a configured distance limit cannot be evaluated safely."""


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float


class Geocoder(Protocol):
    def geocode(self, location: str) -> Coordinates | None: ...


def normalize_location(location: str) -> str:
    return " ".join(location.casefold().split())


def distance_miles(first: Coordinates, second: Coordinates) -> float:
    """Return great-circle distance using the mean Earth radius."""
    lat1, lon1 = radians(first.latitude), radians(first.longitude)
    lat2, lon2 = radians(second.latitude), radians(second.longitude)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )
    return 2 * 3958.7613 * asin(sqrt(value))


class NominatimGeocoder:
    """Low-volume, rate-limited place lookup backed by OpenStreetMap."""

    def __init__(self) -> None:
        client = Nominatim(
            domain=os.getenv(
                "MARKETMON_GEOCODER_DOMAIN",
                "nominatim.openstreetmap.org",
            ),
            user_agent=(
                "marketmon/0.4 "
                "(+https://github.com/brightonsmith/marketplace-monitor)"
            ),
        )
        self._geocode = RateLimiter(
            client.geocode,
            min_delay_seconds=15,
            error_wait_seconds=15,
            swallow_exceptions=False,
        )

    def geocode(self, location: str) -> Coordinates | None:
        try:
            result = self._geocode(location, exactly_one=True)
        except GeocoderServiceError as error:
            raise GeocodingError(f"Geocoding service failed: {error}") from error
        if result is None:
            return None
        return Coordinates(float(result.latitude), float(result.longitude))


class GeocodeCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS geocode_cache (
                location_key TEXT PRIMARY KEY,
                location_text TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                resolved_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def get(self, location: str) -> Coordinates | None:
        row = self.connection.execute(
            "SELECT latitude, longitude FROM geocode_cache WHERE location_key = ?",
            (normalize_location(location),),
        ).fetchone()
        if row is None:
            return None
        return Coordinates(latitude=row[0], longitude=row[1])

    def put(self, location: str, coordinates: Coordinates) -> None:
        self.connection.execute(
            """
            INSERT INTO geocode_cache (
                location_key, location_text, latitude, longitude
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(location_key) DO UPDATE SET
                location_text = excluded.location_text,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                resolved_utc = CURRENT_TIMESTAMP
            """,
            (
                normalize_location(location),
                location,
                coordinates.latitude,
                coordinates.longitude,
            ),
        )
        self.connection.commit()


class DistanceFilter:
    def __init__(
        self,
        database_path: Path,
        geocoder: Geocoder | None = None,
    ) -> None:
        self.cache = GeocodeCache(database_path)
        self.geocoder = geocoder or NominatimGeocoder()

    def close(self) -> None:
        self.cache.close()

    async def resolve(self, location: str) -> Coordinates | None:
        cached = self.cache.get(location)
        if cached is not None:
            return cached
        coordinates = await asyncio.to_thread(self.geocoder.geocode, location)
        if coordinates is not None:
            self.cache.put(location, coordinates)
        return coordinates

    async def distance_between(
        self,
        origin_location: str,
        listing_location: str,
    ) -> float | None:
        origin = await self.resolve(origin_location)
        listing = await self.resolve(listing_location)
        if origin is None or listing is None:
            return None
        return distance_miles(origin, listing)
