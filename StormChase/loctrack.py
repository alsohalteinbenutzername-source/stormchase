

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class LocationFix:
    latitude: float
    longitude: float
    accuracy_meters: float


class _Coordinate(Protocol):
    latitude: float
    longitude: float
    accuracy: float


class _Geoposition(Protocol):
    coordinate: _Coordinate


class _Geolocator(Protocol):
    report_interval: int

    async def get_geoposition_async(self) -> _Geoposition: ...

    def add_position_changed(self, handler: Callable[..., None]) -> Any: ...

    def remove_position_changed(self, token: Any) -> None: ...


async def request_location_access() -> bool:
    from winrt.windows.devices.geolocation import (
        GeolocationAccessStatus,
        Geolocator,
    )

    return await Geolocator.request_access_async() == GeolocationAccessStatus.ALLOWED


def create_geolocator() -> _Geolocator:
    from winrt.windows.devices.geolocation import Geolocator

    geolocator = Geolocator()
    geolocator.desired_accuracy_in_meters = 1000
    geolocator.report_interval = 1000
    return geolocator


def location_from_geoposition(position: _Geoposition) -> LocationFix:
    coordinate = position.coordinate
    latitude = float(coordinate.latitude)
    longitude = float(coordinate.longitude)
    accuracy = float(coordinate.accuracy)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Windows returned coordinates outside the valid range.")
    return LocationFix(
        latitude=latitude,
        longitude=longitude,
        accuracy_meters=accuracy,
    )


async def read_device_location(geolocator: _Geolocator) -> LocationFix:
    position = await geolocator.get_geoposition_async()
    return location_from_geoposition(position)