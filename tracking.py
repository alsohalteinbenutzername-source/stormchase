

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Final
from xml.etree import ElementTree

from wetterdienst.provider.dwd.radar import DwdRadarParameter, DwdRadarValues


KONRAD3D_PRODUCT: Final = DwdRadarParameter.KONRAD3D_CONVECTIVE_CELLS
_LATITUDE_NAMES: Final = {"lat", "latitude"}
_LONGITUDE_NAMES: Final = {"lon", "long", "longitude"}
_CELL_TAGS: Final = {"cell", "feature", "stormcell", "convectivecell", "convectivecellobject"}

GeoPoint = tuple[float, float]


@dataclass(frozen=True)
class StormCell:
    identifier: str
    latitude: float
    longitude: float
    track: tuple[GeoPoint, ...]
    reflectivity_dbz: float | None
    severity: str | None
    forecast: tuple[tuple[datetime, GeoPoint], ...] = ()


@dataclass(frozen=True)
class StormUpdate:
    cells: tuple[StormCell, ...]
    timestamp: datetime | None


def project_cell_motion(
    latitude: float,
    longitude: float,
    wind_direction_degrees: float,
    wind_speed_kmh: float,
    duration_minutes: int = 60,
    step_minutes: int = 10,
) -> tuple[GeoPoint, ...]:
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Cell coordinates are outside valid latitude/longitude ranges.")
    if wind_speed_kmh < 0:
        raise ValueError("Wind speed cannot be negative.")
    if duration_minutes <= 0 or step_minutes <= 0:
        raise ValueError("Projection duration and step must be positive.")

    start_latitude = math.radians(latitude)
    start_longitude = math.radians(longitude)
    # wind direction is the direction it comes from.
    bearing = math.radians((wind_direction_degrees + 180) % 360)
    points = [(latitude, longitude)]
    projection_minutes = list(range(step_minutes, duration_minutes + 1, step_minutes))
    if not projection_minutes or projection_minutes[-1] != duration_minutes:
        projection_minutes.append(duration_minutes)

    for minutes in projection_minutes:
        angular_distance = wind_speed_kmh * minutes / 60 / 6371.0
        projected_latitude = math.asin(
            math.sin(start_latitude) * math.cos(angular_distance)
            + math.cos(start_latitude)
            * math.sin(angular_distance)
            * math.cos(bearing)
        )
        projected_longitude = start_longitude + math.atan2(
            math.sin(bearing)
            * math.sin(angular_distance)
            * math.cos(start_latitude),
            math.cos(angular_distance)
            - math.sin(start_latitude) * math.sin(projected_latitude),
        )
        points.append(
            (
                math.degrees(projected_latitude),
                (math.degrees(projected_longitude) + 540) % 360 - 180,
            )
        )
    return tuple(points)


def _local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].replace("-", "").replace("_", "").lower()


def _coordinate_pairs(element: ElementTree.Element) -> list[GeoPoint]:
    pairs: list[GeoPoint] = []
    for node in element.iter():
        values = {
            _local_name(key): value.strip()
            for key, value in node.attrib.items()
        }
        values.update(
            {
                _local_name(child.tag): child.text.strip()
                for child in node
                if child.text and child.text.strip()
            }
        )
        latitude_text = next(
            (value for name, value in values.items() if name in _LATITUDE_NAMES),
            None,
        )
        longitude_text = next(
            (value for name, value in values.items() if name in _LONGITUDE_NAMES),
            None,
        )
        if latitude_text is None or longitude_text is None:
            continue
        try:
            latitude = float(latitude_text)
            longitude = float(longitude_text)
        except ValueError:
            continue
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            pairs.append((latitude, longitude))
    return pairs


def _direct_coordinate_pair(element: ElementTree.Element) -> GeoPoint | None:
    values = {
        _local_name(key): value.strip()
        for key, value in element.attrib.items()
    }
    values.update(
        {
            _local_name(child.tag): child.text.strip()
            for child in element
            if child.text and child.text.strip()
        }
    )
    latitude_text = next(
        (value for name, value in values.items() if name in _LATITUDE_NAMES),
        None,
    )
    longitude_text = next(
        (value for name, value in values.items() if name in _LONGITUDE_NAMES),
        None,
    )
    if latitude_text is None or longitude_text is None:
        return None
    try:
        latitude = float(latitude_text)
        longitude = float(longitude_text)
    except ValueError:
        return None
    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
        return latitude, longitude
    return None


def _current_cell_position(element: ElementTree.Element) -> GeoPoint | None:

    geometry = next(
        (node for node in element.iter() if _local_name(node.tag) == "geometry"),
        None,
    )
    if geometry is not None:
        centroids = [
            node
            for node in geometry.iter()
            if "centroid" in _local_name(node.tag)
        ]
        centroids.sort(
            key=lambda node: (
                "2d" not in _local_name(node.tag),
                len(list(node.iter())),
            )
        )
        for centroid in centroids:
            points = _coordinate_pairs(centroid)
            if points:
                return points[0]


    return _direct_coordinate_pair(element)


def _cell_track(
    element: ElementTree.Element,
    current_position: GeoPoint,
) -> tuple[GeoPoint, ...]:
    track_element = next(
        (node for node in element.iter() if _local_name(node.tag) == "track"),
        None,
    )
    points = _coordinate_pairs(track_element) if track_element is not None else []
    if not points or points[-1] != current_position:
        points.append(current_position)
    return tuple(
        point
        for index, point in enumerate(points)
        if index == 0 or point != points[index - 1]
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp


def _cell_forecasts(element: ElementTree.Element) -> tuple[tuple[datetime, GeoPoint], ...]:
    forecasts: list[tuple[datetime, GeoPoint]] = []
    for node in element.iter():
        if _local_name(node.tag) != "centroidforecast":
            continue
        timestamp = _parse_datetime(node.attrib.get("forecast_time"))
        points = _coordinate_pairs(node) if timestamp is not None else []
        point = points[0] if points else None
        if timestamp is not None and point is not None:
            forecasts.append((timestamp, point))
    return tuple(sorted(forecasts))


def _number_from_element(element: ElementTree.Element, names: set[str]) -> float | None:
    for node in element.iter():
        values = {
            _local_name(key): value.strip()
            for key, value in node.attrib.items()
        }
        values.update(
            {
                _local_name(child.tag): child.text.strip()
                for child in node
                if child.text and child.text.strip()
            }
        )
        for name, value in values.items():
            if name in names:
                try:
                    return float(value)
                except ValueError:
                    continue
    return None


def _text_from_element(element: ElementTree.Element, names: set[str]) -> str | None:
    for node in element.iter():
        for key, value in node.attrib.items():
            if _local_name(key) in names:
                return value.strip()
        for child in node:
            if _local_name(child.tag) in names and child.text:
                return child.text.strip()
    return None


def _parse_timestamp(root: ElementTree.Element) -> datetime | None:
    for element in root.iter():
        if _local_name(element.tag) in {"referencetime", "creationdate"}:
            timestamp = _parse_datetime(element.text)
            if timestamp is not None:
                return timestamp
    return None


def parse_konrad3d_xml(contents: bytes) -> StormUpdate:

    try:
        root = ElementTree.fromstring(contents)
    except ElementTree.ParseError as error:
        raise ValueError(f"Invalid KONRAD3D XML: {error}") from error

    parents = {child: parent for parent in root.iter() for child in parent}
    cells: list[StormCell] = []
    for index, element in enumerate(root.iter(), start=1):
        tag = _local_name(element.tag)
        parent = parents.get(element)
        is_cell = (
            tag in _CELL_TAGS
            and parent is not None
            and _local_name(parent.tag) == "cells"
        )
        if not is_cell:
            continue

        current_position = _current_cell_position(element)
        if current_position is None:

            points = _coordinate_pairs(element)
            current_position = points[0] if points else None
        if current_position is None:
            continue

        identifier = _text_from_element(
            element,
            {"id", "identifier", "cellid", "trackid", "objectid", "number"},
        )
        if not identifier:
            latitude, longitude = current_position
            identifier = f"cell-{index}-{latitude:.3f}-{longitude:.3f}"
        reflectivity = _number_from_element(
            element,
            {"dbz", "reflectivity", "maxreflectivity", "maximumreflectivity", "maxdbz"},
        )
        severity = _text_from_element(element, {"severity", "intensity", "category"})
        latitude, longitude = current_position
        cells.append(
            StormCell(
                identifier=identifier,
                latitude=latitude,
                longitude=longitude,
                track=_cell_track(element, current_position),
                reflectivity_dbz=reflectivity,
                severity=severity,
                forecast=_cell_forecasts(element),
            )
        )

    return StormUpdate(cells=tuple(cells), timestamp=_parse_timestamp(root))


def fetch_latest_storm_cells() -> StormUpdate:
    """Fetch the most recent 20 minutes of KONRAD3D files and use the newest."""
    end = datetime.now(timezone.utc)
    request = DwdRadarValues(
        parameter=KONRAD3D_PRODUCT,
        start=end - timedelta(minutes=20),
        end=end,
    )
    results = list(request.query())
    if not results:
        return StormUpdate(cells=(), timestamp=None)

    result = max(
        results,
        key=lambda item: item.timestamp
        or item.data_updated_at
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    update = parse_konrad3d_xml(result.data.getvalue())
    return StormUpdate(
        cells=update.cells,
        timestamp=update.timestamp or result.timestamp or result.data_updated_at,
    )
