
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cached_property, lru_cache
from io import BytesIO
import math
from typing import Any, cast

import h5py
import numpy as np
from numpy.typing import NDArray
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError
from scipy.ndimage import binary_dilation, label, map_coordinates
from wetterdienst.provider.dwd.radar import (
    DwdRadarDate,
    DwdRadarParameter,
    DwdRadarSite,
    DwdRadarValues,
)
from wetterdienst.provider.dwd.radar.api import DwdRadarSites


REFLECTIVITY_PRODUCT: DwdRadarParameter = DwdRadarParameter.HX_REFLECTIVITY
VELOCITY_PRODUCT: DwdRadarParameter = DwdRadarParameter.PR_VELOCITY
GERMANY_EXTENT: tuple[float, float, float, float] = (5.0, 16.0, 47.0, 56.0)
RAIN_CELL_MIN_DBZ: float = 20.0
RAIN_CELL_MAX_DBZ: float = 30.0
RAIN_CELL_MIN_PIXELS: int = 25
RAIN_CELL_MERGE_RADIUS_PIXELS: int = 3
RAIN_TRACK_MAX_DISTANCE_KM: float = 45.0
RAIN_TRACK_MAX_GAP: timedelta = timedelta(minutes=20)
RAIN_TRACK_MAX_POINTS: int = 2
RAIN_CELL_POINTER_DISTANCE_KM: float = 25.0
ROTATION_VELOCITY_THRESHOLD_MS: float = 20.0
ROTATION_MIN_PIXELS: int = 10
ROTATION_MAX_ASPECT_RATIO: float = 8.0
MAX_GEOGRAPHIC_IMAGE_SIZE: int = 1200


class RadarDataError(ValueError):
    """Raised when a DWD radar file cannot be decoded into a grid."""


@dataclass(frozen=True)
class RadarFrame:
    reflectivity: NDArray[np.float32]
    timestamp: datetime | None
    extent: tuple[float, float, float, float] = GERMANY_EXTENT
    projection: str | None = None
    xscale: float | None = None
    yscale: float | None = None

    @property
    def values(self) -> NDArray[np.float32]:
        """Return the decoded grid, whether it contains reflectivity or velocity."""
        return self.reflectivity

    @cached_property
    def geographic_values(self) -> NDArray[np.float32]:
        if self.projection is None or self.xscale is None or self.yscale is None:
            return self.values

        rows, columns = self.values.shape
        scale = max(
            1,
            math.ceil(rows / MAX_GEOGRAPHIC_IMAGE_SIZE),
            math.ceil(columns / MAX_GEOGRAPHIC_IMAGE_SIZE),
        )
        output_rows = math.ceil(rows / scale)
        output_columns = math.ceil(columns / scale)
        west, east, south, north = self.extent
        longitudes = west + (
            np.arange(output_columns, dtype=np.float64) + 0.5
        ) * (east - west) / output_columns
        latitudes = north - (
            np.arange(output_rows, dtype=np.float64) + 0.5
        ) * (north - south) / output_rows
        longitude_grid, latitude_grid = np.meshgrid(longitudes, latitudes)
        _, to_projection = _projection_transformers(self.projection)
        projected_x, projected_y = to_projection.transform(
            longitude_grid,
            latitude_grid,
        )
        source_columns = projected_x / self.xscale
        source_rows = -projected_y / self.yscale
        valid = np.isfinite(source_rows) & np.isfinite(source_columns)
        sampled = map_coordinates(
            self.values,
            (
                np.where(valid, source_rows, -1.0),
                np.where(valid, source_columns, -1.0),
            ),
            order=0,
            mode="constant",
            cval=np.nan,
            prefilter=False,
        )
        sampled[~valid] = np.nan
        return np.asarray(sampled, dtype=np.float32)


@dataclass(frozen=True)
class VelocityRadarSite:
    site: DwdRadarSite
    name: str
    latitude: float
    longitude: float
    max_range_km: float


@dataclass(frozen=True)
class RainTrackPoint:
    latitude: float
    longitude: float
    timestamp: datetime
    reflectivity_dbz: float


def project_rain_cell_direction(
    track: tuple[RainTrackPoint, ...],
    current_position: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    if len(track) < 2:
        return None

    previous, latest = track[-2:]
    start_latitude, start_longitude = current_position or (
        latest.latitude,
        latest.longitude,
    )
    previous_latitude = math.radians(previous.latitude)
    latest_latitude = math.radians(latest.latitude)
    longitude_delta = math.radians(
        (latest.longitude - previous.longitude + 180.0) % 360.0 - 180.0
    )
    latitude_delta = latest_latitude - previous_latitude
    angular_distance = 2.0 * math.asin(
        math.sqrt(
            math.sin(latitude_delta / 2.0) ** 2
            + math.cos(previous_latitude)
            * math.cos(latest_latitude)
            * math.sin(longitude_delta / 2.0) ** 2
        )
    )
    if angular_distance == 0:
        return None

    bearing = math.atan2(
        math.sin(longitude_delta) * math.cos(latest_latitude),
        math.cos(previous_latitude) * math.sin(latest_latitude)
        - math.sin(previous_latitude)
        * math.cos(latest_latitude)
        * math.cos(longitude_delta),
    )
    projection_distance = RAIN_CELL_POINTER_DISTANCE_KM / 6371.0
    start_latitude_radians = math.radians(start_latitude)
    start_longitude_radians = math.radians(start_longitude)
    projected_latitude = math.asin(
        math.sin(start_latitude_radians) * math.cos(projection_distance)
        + math.cos(start_latitude_radians)
        * math.sin(projection_distance)
        * math.cos(bearing)
    )
    projected_longitude = start_longitude_radians + math.atan2(
        math.sin(bearing)
        * math.sin(projection_distance)
        * math.cos(start_latitude_radians),
        math.cos(projection_distance)
        - math.sin(start_latitude_radians) * math.sin(projected_latitude),
    )
    return (
        math.degrees(projected_latitude),
        (math.degrees(projected_longitude) + 540.0) % 360.0 - 180.0,
    )


@dataclass(frozen=True)
class RainCell:
    identifier: str
    latitude: float
    longitude: float
    max_reflectivity_dbz: float
    area_pixels: int
    track: tuple[RainTrackPoint, ...] = ()


@dataclass(frozen=True)
class RotationDetection:
    latitude: float
    longitude: float
    negative_pixels: int
    positive_pixels: int


@dataclass
class _RainTrack:
    points: list[RainTrackPoint]
    last_seen: datetime


def _attribute_value(attributes: h5py.AttributeManager, name: str) -> float | None:
    value = attributes.get(name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _attribute_text(attributes: h5py.AttributeManager, name: str) -> str | None:
    value = attributes.get(name)
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


@lru_cache(maxsize=8)
def _projection_transformers(
    projection: str,
) -> tuple[Transformer, Transformer]:
    source_crs = CRS.from_user_input(projection)
    geographic_crs = CRS.from_epsg(4326)
    return (
        Transformer.from_crs(source_crs, geographic_crs, always_xy=True),
        Transformer.from_crs(geographic_crs, source_crs, always_xy=True),
    )


def _pixel_to_geographic(
    frame: RadarFrame,
    row: float,
    column: float,
) -> tuple[float, float]:
    if frame.projection is None or frame.xscale is None or frame.yscale is None:
        west, east, south, north = frame.extent
        rows, columns = frame.values.shape
        latitude = north - (row + 0.5) / rows * (north - south)
        longitude = west + (column + 0.5) / columns * (east - west)
        return latitude, longitude

    to_geographic, _ = _projection_transformers(frame.projection)
    longitude, latitude = to_geographic.transform(
        column * frame.xscale,
        -row * frame.yscale,
    )
    return float(latitude), float(longitude)


def _label_components(
    mask: NDArray[np.bool_],
    structure: NDArray[np.uint8],
) -> tuple[NDArray[np.intp], int]:
    result = cast(tuple[NDArray[Any], int], label(mask, structure=structure))
    return np.asarray(result[0], dtype=np.intp), result[1]


def _decode_reflectivity(dataset: h5py.Dataset) -> NDArray[np.float32]:
    values = np.asarray(dataset[...])
    if values.ndim > 2:
        values = np.squeeze(values)
    if values.ndim != 2:
        raise RadarDataError(
            f"Expected a two-dimensional radar grid, received shape {values.shape}."
        )

    dataset_parent = dataset.parent
    metadata = (
        dataset_parent.get("what")
        if isinstance(dataset_parent, h5py.Group)
        else None
    )
    attributes = metadata.attrs if isinstance(metadata, h5py.Group) else dataset.attrs
    gain = _attribute_value(attributes, "gain")
    offset = _attribute_value(attributes, "offset")
    nodata = _attribute_value(attributes, "nodata")
    undetect = _attribute_value(attributes, "undetect")

    decoded: NDArray[np.float32] = np.asarray(values, dtype=np.float32)
    if gain is not None:
        decoded = decoded * gain
    if offset is not None:
        decoded = decoded + offset
    if nodata is not None:
        decoded[values == nodata] = np.nan
    if undetect is not None:
        decoded[values == undetect] = np.nan
    return decoded


def decode_radar_file(contents: bytes, timestamp: datetime | None = None) -> RadarFrame:
    """Decode the largest radar grid and its geographic extent from DWD HDF5."""
    candidates: list[tuple[str, h5py.Dataset]] = []

    def collect_dataset(name: str, item: h5py.Dataset | h5py.Group) -> None:
        if isinstance(item, h5py.Dataset) and item.ndim >= 2:
            candidates.append((name, item))

    try:
        with h5py.File(BytesIO(contents), "r") as radar_file:
            radar_file.visititems(collect_dataset)
            if not candidates:
                raise RadarDataError("The DWD HDF5 file contains no radar grid.")

            dataset = max(
                candidates,
                key=lambda candidate: (
                    candidate[0].rsplit("/", 1)[-1].lower() == "data",
                    int(np.prod(candidate[1].shape)),
                ),
            )[1]
            reflectivity = _decode_reflectivity(dataset)
            extent = GERMANY_EXTENT
            projection = None
            xscale = None
            yscale = None
            dataset_parent = dataset.parent
            dataset_group = (
                dataset_parent.parent
                if isinstance(dataset_parent, h5py.Group)
                else None
            )
            metadata = None
            if isinstance(dataset_group, h5py.Group):
                metadata = dataset_group.get("where")
            if not isinstance(metadata, h5py.Group):
                metadata = radar_file.get("where")
            if isinstance(metadata, h5py.Group):
                attributes = metadata.attrs
                corner_names = ("LL_lon", "LR_lon", "UL_lon", "UR_lon")
                latitude_names = ("LL_lat", "LR_lat", "UL_lat", "UR_lat")
                if all(name in attributes for name in (*corner_names, *latitude_names)):
                    longitudes = [
                        value
                        for name in corner_names
                        if (value := _attribute_value(attributes, name)) is not None
                    ]
                    latitudes = [
                        value
                        for name in latitude_names
                        if (value := _attribute_value(attributes, name)) is not None
                    ]
                    if len(longitudes) == len(corner_names) and len(latitudes) == len(latitude_names):
                        extent = (
                            min(longitudes),
                            max(longitudes),
                            min(latitudes),
                            max(latitudes),
                        )
                projection = _attribute_text(attributes, "projdef")
                xscale = _attribute_value(attributes, "xscale")
                yscale = _attribute_value(attributes, "yscale")
                if projection is not None and xscale is not None and yscale is not None:
                    xsize = _attribute_value(attributes, "xsize")
                    ysize = _attribute_value(attributes, "ysize")
                    if (
                        xsize is None
                        or ysize is None
                        or int(xsize) != reflectivity.shape[1]
                        or int(ysize) != reflectivity.shape[0]
                        or xscale <= 0
                        or yscale <= 0
                    ):
                        raise RadarDataError(
                            "The DWD radar projection metadata does not match its grid."
                        )
                    try:
                        _projection_transformers(projection)
                    except (CRSError, ProjError, ValueError) as error:
                        raise RadarDataError(
                            f"Unable to read the DWD radar projection: {error}"
                        ) from error
    except RadarDataError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise RadarDataError(f"Unable to decode the DWD HDF5 radar file: {error}") from error

    return RadarFrame(
        reflectivity=reflectivity,
        timestamp=timestamp,
        extent=extent,
        projection=projection,
        xscale=xscale,
        yscale=yscale,
    )


@lru_cache(maxsize=1)
def velocity_radar_sites() -> tuple[VelocityRadarSite, ...]:
    sites = []
    for metadata in DwdRadarSites().all():
        odim_code = metadata.get("odimcode")
        if not odim_code or not metadata.get("status") or not metadata.get("doppler"):
            continue
        sites.append(
            VelocityRadarSite(
                site=DwdRadarSite(odim_code[2:]),
                name=str(metadata["location"]),
                latitude=float(metadata["latitude"]),
                longitude=float(metadata["longitude"]),
                max_range_km=float(metadata["maxrange"]),
            )
        )
    if not sites:
        raise RadarDataError("No active DWD Doppler radar stations are available.")
    return tuple(sites)


def nearest_velocity_radar_site(latitude: float, longitude: float) -> VelocityRadarSite:
    if not math.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    if not math.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180 degrees.")

    latitude_radians = math.radians(latitude)
    longitude_radians = math.radians(longitude)

    def distance(site: VelocityRadarSite) -> float:
        site_latitude = math.radians(site.latitude)
        site_longitude = math.radians(site.longitude)
        latitude_delta = site_latitude - latitude_radians
        longitude_delta = site_longitude - longitude_radians
        haversine = (
            math.sin(latitude_delta / 2) ** 2
            + math.cos(latitude_radians)
            * math.cos(site_latitude)
            * math.sin(longitude_delta / 2) ** 2
        )
        return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(haversine)))

    return min(velocity_radar_sites(), key=distance)


def detect_rain_cells(
    frame: RadarFrame,
    minimum_dbz: float = RAIN_CELL_MIN_DBZ,
    maximum_dbz: float = RAIN_CELL_MAX_DBZ,
    minimum_pixels: int = RAIN_CELL_MIN_PIXELS,
) -> tuple[RainCell, ...]:
    if minimum_dbz > maximum_dbz:
        raise ValueError("Minimum rain-cell reflectivity must not exceed its maximum.")
    if minimum_pixels < 1:
        raise ValueError("Minimum rain-cell area must be at least one pixel.")

    reflectivity = frame.reflectivity
    echo_mask: NDArray[np.bool_] = np.asarray(
        np.isfinite(reflectivity)
        & (reflectivity >= minimum_dbz)
        & (reflectivity <= maximum_dbz),
        dtype=np.bool_,
    )
    stronger_echoes: NDArray[np.bool_] = np.asarray(
        np.isfinite(reflectivity) & (reflectivity > maximum_dbz),
        dtype=np.bool_,
    )
    structure = np.ones((3, 3), dtype=np.uint8)
    nearby_stronger_echoes: NDArray[np.bool_] = np.asarray(
        binary_dilation(stronger_echoes, structure=structure),
        dtype=np.bool_,
    )
    echo_mask &= ~nearby_stronger_echoes
    grouped_labels, component_count = _label_components(
        np.asarray(
            binary_dilation(echo_mask, iterations=RAIN_CELL_MERGE_RADIUS_PIXELS),
            dtype=np.bool_,
        ),
        structure,
    )
    if component_count == 0:
        return ()

    pixel_rows, pixel_columns = np.nonzero(echo_mask)
    pixel_labels = np.asarray(
        grouped_labels[pixel_rows, pixel_columns],
        dtype=np.intp,
    )
    component_sizes = np.bincount(pixel_labels, minlength=component_count + 1)
    active_labels = np.flatnonzero(component_sizes >= minimum_pixels)
    active_labels = active_labels[active_labels != 0]
    if active_labels.size == 0:
        return ()

    row_sums = np.bincount(
        pixel_labels,
        weights=pixel_rows,
        minlength=component_count + 1,
    )
    column_sums = np.bincount(
        pixel_labels,
        weights=pixel_columns,
        minlength=component_count + 1,
    )
    maximum_reflectivity = np.full(component_count + 1, -np.inf)
    np.maximum.at(
        maximum_reflectivity,
        pixel_labels,
        reflectivity[pixel_rows, pixel_columns],
    )

    cells: list[RainCell] = []
    for component_id in active_labels:
        area_pixels = int(component_sizes[component_id])
        latitude, longitude = _pixel_to_geographic(
            frame,
            row_sums[component_id] / area_pixels,
            column_sums[component_id] / area_pixels,
        )
        maximum = float(maximum_reflectivity[component_id])
        cells.append(
            RainCell(
                identifier=f"rain-{len(cells) + 1:03d}",
                latitude=latitude,
                longitude=longitude,
                max_reflectivity_dbz=maximum,
                area_pixels=area_pixels,
            )
        )
    return tuple(cells)


def _distance_km(first: RainCell, second: RainCell) -> float:
    mean_latitude = math.radians((first.latitude + second.latitude) / 2)
    latitude_distance = (first.latitude - second.latitude) * 111.0
    longitude_distance = (
        (first.longitude - second.longitude) * 111.0 * math.cos(mean_latitude)
    )
    return math.hypot(latitude_distance, longitude_distance)


class RainCellTracker:

    def __init__(
        self,
        max_distance_km: float = RAIN_TRACK_MAX_DISTANCE_KM,
        max_gap: timedelta = RAIN_TRACK_MAX_GAP,
        max_points: int = RAIN_TRACK_MAX_POINTS,
    ) -> None:
        if max_distance_km <= 0 or max_gap.total_seconds() <= 0 or max_points < 2:
            raise ValueError("Rain-cell tracking limits must be positive.")
        self.max_distance_km = max_distance_km
        self.max_gap = max_gap
        self.max_points = max_points
        self._tracks: dict[str, _RainTrack] = {}
        self._next_identifier = 1

    def update(
        self,
        cells: tuple[RainCell, ...],
        timestamp: datetime | None,
    ) -> tuple[RainCell, ...]:
        observed_at = timestamp or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)

        self._tracks = {
            identifier: track
            for identifier, track in self._tracks.items()
            if timedelta(0) <= observed_at - track.last_seen <= self.max_gap
        }

        candidate_matches: list[tuple[float, int, str]] = []
        for cell_index, cell in enumerate(cells):
            for identifier, track in self._tracks.items():
                gap = observed_at - track.last_seen
                if gap < timedelta(0) or gap > self.max_gap:
                    continue
                previous = track.points[-1]
                previous_cell = RainCell(
                    identifier=identifier,
                    latitude=previous.latitude,
                    longitude=previous.longitude,
                    max_reflectivity_dbz=previous.reflectivity_dbz,
                    area_pixels=0,
                )
                distance = _distance_km(cell, previous_cell)
                if distance <= self.max_distance_km:
                    candidate_matches.append((distance, cell_index, identifier))

        matched_cells: dict[int, str] = {}
        matched_tracks: set[str] = set()
        for _, cell_index, identifier in sorted(candidate_matches):
            if cell_index in matched_cells or identifier in matched_tracks:
                continue
            matched_cells[cell_index] = identifier
            matched_tracks.add(identifier)

        tracked_cells: list[RainCell] = []
        for cell_index, cell in enumerate(cells):
            identifier = matched_cells.get(cell_index)
            if identifier is None:
                identifier = f"rain-{self._next_identifier:04d}"
                self._next_identifier += 1
                self._tracks[identifier] = _RainTrack(points=[], last_seen=observed_at)

            track = self._tracks[identifier]
            previous = track.points[-1] if track.points else None
            if previous is None:
                moved_km = float("inf")
            else:
                previous_cell = RainCell(
                    identifier=identifier,
                    latitude=previous.latitude,
                    longitude=previous.longitude,
                    max_reflectivity_dbz=previous.reflectivity_dbz,
                    area_pixels=0,
                )
                moved_km = _distance_km(cell, previous_cell)
            if previous is None or (
                observed_at > previous.timestamp and moved_km >= 0.1
            ):
                track.points.append(
                    RainTrackPoint(
                        latitude=cell.latitude,
                        longitude=cell.longitude,
                        timestamp=observed_at,
                        reflectivity_dbz=cell.max_reflectivity_dbz,
                    )
                )
                del track.points[:-self.max_points]
            track.last_seen = max(track.last_seen, observed_at)
            tracked_cells.append(
                RainCell(
                    identifier=identifier,
                    latitude=cell.latitude,
                    longitude=cell.longitude,
                    max_reflectivity_dbz=cell.max_reflectivity_dbz,
                    area_pixels=cell.area_pixels,
                    track=tuple(track.points),
                )
            )
        return tuple(tracked_cells)

def detect_rotations(
    frame: RadarFrame,
    threshold: float = ROTATION_VELOCITY_THRESHOLD_MS,
    min_pixels: int = ROTATION_MIN_PIXELS,
) -> tuple[RotationDetection, ...]:
    """Find compact inbound/outbound velocity couplets in a gridded scan.

    Candidate lobes must meet the per-pixel velocity and area thresholds,
    touch one another, and retain a strong robust velocity difference. Very
    elongated regions are discarded as likely shear boundaries.
    """
    if not math.isfinite(threshold) or threshold <= 0 or min_pixels < 1:
        raise ValueError("Rotation detection parameters must be positive.")
    velocity = frame.values
    negative_mask: NDArray[np.bool_] = np.asarray(
        np.isfinite(velocity) & (velocity <= -threshold),
        dtype=np.bool_,
    )
    positive_mask: NDArray[np.bool_] = np.asarray(
        np.isfinite(velocity) & (velocity >= threshold),
        dtype=np.bool_,
    )
    structure = np.ones((3, 3), dtype=np.uint8)
    negative_labels, negative_count = _label_components(negative_mask, structure)
    positive_labels, positive_count = _label_components(positive_mask, structure)
    if negative_count == 0 or positive_count == 0:
        return ()
    negative_sizes = np.bincount(negative_labels.ravel(), minlength=negative_count + 1)
    positive_sizes = np.bincount(positive_labels.ravel(), minlength=positive_count + 1)

    graph: dict[tuple[bool, int], set[tuple[bool, int]]] = {}
    for neg_label in range(1, negative_count + 1):
        if negative_sizes[neg_label] < min_pixels:
            continue
        neg_mask: NDArray[np.bool_] = np.asarray(
            negative_labels == neg_label,
            dtype=np.bool_,
        )
        adjacent_mask: NDArray[np.bool_] = np.asarray(
            binary_dilation(neg_mask, structure=structure),
            dtype=np.bool_,
        )
        adjacent_positive = np.unique(positive_labels[adjacent_mask])
        for pos_label in adjacent_positive:
            if pos_label == 0 or positive_sizes[pos_label] < min_pixels:
                continue
            negative_node = (False, neg_label)
            positive_node = (True, int(pos_label))
            graph.setdefault(negative_node, set()).add(positive_node)
            graph.setdefault(positive_node, set()).add(negative_node)

    detections: list[RotationDetection] = []
    visited: set[tuple[bool, int]] = set()
    west, east, south, north = frame.extent
    rows, columns = velocity.shape
    for start in graph:
        if start in visited:
            continue
        component: set[tuple[bool, int]] = set()
        pending = [start]
        while pending:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            pending.extend(graph[node] - visited)

        negative_ids = [identifier for is_positive, identifier in component if not is_positive]
        positive_ids = [identifier for is_positive, identifier in component if is_positive]
        negative_values = velocity[np.isin(negative_labels, negative_ids)]
        positive_values = velocity[np.isin(positive_labels, positive_ids)]
        if not negative_values.size or not positive_values.size:
            continue

        # Use the inner quartiles so one unfolded gate cannot make a weak
        # positive/negative pair look like a strong velocity couplet.
        delta_velocity = float(
            np.percentile(positive_values, 25)
            - np.percentile(negative_values, 75)
        )
        if delta_velocity < threshold:
            continue

        detection_mask: NDArray[np.bool_] = np.asarray(
            np.isin(negative_labels, negative_ids)
            | np.isin(positive_labels, positive_ids),
            dtype=np.bool_,
        )
        pixel_rows, pixel_columns = np.nonzero(detection_mask)
        row_span = int(pixel_rows.max() - pixel_rows.min() + 1)
        column_span = int(pixel_columns.max() - pixel_columns.min() + 1)
        center_row = float(np.mean(pixel_rows))
        center_column = float(np.mean(pixel_columns))

        # Reject very long, thin shear boundaries. Account for the grid's
        # physical aspect ratio when projection metadata is available.
        if frame.xscale is not None and frame.yscale is not None:
            width = column_span * abs(frame.xscale)
            height = row_span * abs(frame.yscale)
        else:
            west, east, south, north = frame.extent
            rows, columns = velocity.shape
            latitude = north - (center_row + 0.5) / rows * (north - south)
            width = (
                column_span
                * (east - west)
                / columns
                * 111_320.0
                * max(math.cos(math.radians(latitude)), 0.01)
            )
            height = row_span * (north - south) / rows * 111_320.0
        aspect_ratio = max(width, height) / max(min(width, height), 1.0)
        if aspect_ratio > ROTATION_MAX_ASPECT_RATIO:
            continue

        latitude, longitude = _pixel_to_geographic(
            frame,
            center_row,
            center_column,
        )
        detections.append(
            RotationDetection(
                latitude=latitude,
                longitude=longitude,
                negative_pixels=sum(int(negative_sizes[index]) for index in negative_ids),
                positive_pixels=sum(int(positive_sizes[index]) for index in positive_ids),
            )
        )
    return tuple(detections)


def rotation_detection(
    frame: RadarFrame,
    threshold: float = ROTATION_VELOCITY_THRESHOLD_MS,
    min_pixels: int = ROTATION_MIN_PIXELS,
) -> bool:
    """Return whether a velocity frame contains at least one qualifying couplet."""
    return bool(detect_rotations(frame, threshold, min_pixels))





def fetch_latest_radar() -> RadarFrame | None:
    return fetch_latest_radar_product(REFLECTIVITY_PRODUCT)


def fetch_latest_radar_product(parameter: DwdRadarParameter) -> RadarFrame | None:
    request = DwdRadarValues(parameter=parameter, start=DwdRadarDate.LATEST)
    result = next(iter(request.query()), None)
    if result is None:
        return None

    return decode_radar_file(
        result.data.getvalue(),
        timestamp=result.timestamp or result.data_updated_at,
    )



def radar_playback(
    start: datetime,
    end: datetime,
    step: timedelta = timedelta(minutes=5),
) -> tuple[RadarFrame, ...]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Start and end times must be timezone-aware.")
    if start >= end:
        raise ValueError("Start time must be before end time.")
    if step.total_seconds() <= 0:
        raise ValueError("Step duration must be positive.")

    frames: list[RadarFrame] = []
    current = start
    while current <= end:
        request = DwdRadarValues(
            parameter=REFLECTIVITY_PRODUCT,
            start=DwdRadarDate(current),
        )
        result = next(iter(request.query()), None)
        if result is not None:
            frame = decode_radar_file(
                result.data.getvalue(),
                timestamp=result.timestamp or result.data_updated_at,
            )
            frames.append(frame)
        current += step
    return tuple(frames)

def fetch_latest_velocity_radar(site: DwdRadarSite) -> RadarFrame | None:
    request = DwdRadarValues(
        parameter=VELOCITY_PRODUCT,
        site=site,
        start=DwdRadarDate.LATEST,
    )
    result = next(iter(request.query()), None)
    if result is None:
        return None

    return decode_radar_file(
        result.data.getvalue(),
        timestamp=result.timestamp or result.data_updated_at,
    )

def velocity_radar_playback(
    start: datetime,
    end: datetime,
    step: timedelta = timedelta(minutes=5),
) -> tuple[RadarFrame, ...]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Start and end times must be timezone-aware.")
    if start >= end:
        raise ValueError("Start time must be before end time.")
    if step.total_seconds() <= 0:
        raise ValueError("Step duration must be positive.")

    frames: list[RadarFrame] = []
    current = start
    while current <= end:
        request = DwdRadarValues(
            parameter=VELOCITY_PRODUCT,
            site=DwdRadarSite.HNR,
            start=DwdRadarDate(current),
        )
        result = next(iter(request.query()), None)
        if result is not None:
            frame = decode_radar_file(
                result.data.getvalue(),
                timestamp=result.timestamp or result.data_updated_at,
            )
            frames.append(frame)
        current += step
    return tuple(frames)
