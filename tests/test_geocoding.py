import asyncio
from pathlib import Path

import pytest

from marketplace_monitor.geocoding import (
    Coordinates,
    DistanceFilter,
    distance_miles,
)


class FakeGeocoder:
    def __init__(self, results: dict[str, Coordinates | None]):
        self.results = results
        self.calls: list[str] = []

    def geocode(self, location: str) -> Coordinates | None:
        self.calls.append(location)
        return self.results[location]


def test_distance_miles_uses_great_circle_distance() -> None:
    result = distance_miles(Coordinates(0, 0), Coordinates(0, 1))
    assert result == pytest.approx(69.09, abs=0.02)


def test_distance_filter_persists_successful_geocodes(tmp_path: Path) -> None:
    geocoder = FakeGeocoder(
        {
            "Denver, Colorado": Coordinates(39.7392, -104.9903),
            "Casper, WY": Coordinates(42.8501, -106.3252),
        }
    )
    path = tmp_path / "marketplace.db"
    first = DistanceFilter(path, geocoder)
    try:
        result = asyncio.run(
            first.distance_between("Denver, Colorado", "Casper, WY")
        )
    finally:
        first.close()
    assert result is not None and result > 200
    assert geocoder.calls == ["Denver, Colorado", "Casper, WY"]

    cached = FakeGeocoder({})
    second = DistanceFilter(path, cached)
    try:
        repeated = asyncio.run(
            second.distance_between("Denver, Colorado", "Casper, WY")
        )
    finally:
        second.close()
    assert repeated == pytest.approx(result)
    assert cached.calls == []


def test_distance_filter_does_not_cache_unresolved_location(tmp_path: Path) -> None:
    geocoder = FakeGeocoder(
        {
            "Denver, Colorado": Coordinates(39.7392, -104.9903),
            "Unknown": None,
        }
    )
    distance_filter = DistanceFilter(tmp_path / "marketplace.db", geocoder)
    try:
        assert (
            asyncio.run(
                distance_filter.distance_between("Denver, Colorado", "Unknown")
            )
            is None
        )
        assert (
            asyncio.run(
                distance_filter.distance_between("Denver, Colorado", "Unknown")
            )
            is None
        )
    finally:
        distance_filter.close()
    assert geocoder.calls.count("Unknown") == 2
