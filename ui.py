
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
import time as time_module
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("TkAgg")

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from matplotlib.figure import Figure
import matplotlib.patheffects as path_effects
from matplotlib.tri import Triangulation, UniformTriRefiner
import numpy as np
from PIL import Image, ImageDraw

from loctrack import (
    LocationFix,
    create_geolocator,
    location_from_geoposition,
    read_device_location,
    request_location_access,
)
from lightning import LightningStrike
from lightning_stream import stream_blitzortung_lightning
from radar import (
    GERMANY_EXTENT,
    KONRAD3D_ROTATION_MATCH_RADIUS_KM,
    MESOCYCLONE_MIN_DISPLAY_LEVEL,
    RAIN_CELL_MAX_DBZ,
    RAIN_CELL_MIN_DBZ,
    ROTATION_STORM_SUPPORT_MIN_DBZ,
    RainCellTracker,
    RainCell,
    RadarFrame,
    RotationDetection,
    VelocityRadarSite,
    detect_rain_cells,
    fetch_latest_radar,
    fetch_latest_mesocyclone_detections,
    fetch_latest_velocity_radar,
    geographic_distance_km,
    maximum_reflectivity_near,
    nearest_velocity_radar_site,
    project_rain_cell_direction,
)
from supabase_client import AuthUser, SupabaseClient, SupabaseConfig, SupabaseError
from tracking import StormCell, StormUpdate, fetch_latest_storm_cells, project_cell_motion
from calculations import (
    NO_LFC_SENTINEL_M,
    calculate_best_day_window,
    calculate_manual_storm,
    nearest_outlook_entry,
)


BACKGROUND = "#101820"
PANEL = "#19252e"
FOREGROUND = "#e7eef2"
MUTED = "#9aabb5"
ACCENT = "#43c6ac"
SEVERE_THRESHOLD_DBZ = 50.0
DATA_REFRESH_SECONDS = 300
SHARED_DATA_STALE_AFTER = timedelta(minutes=12)
MAX_MAP_ZOOM = 20
LIVE_MAP = "Live map"
DWD_WARNING_MAP = "DWD warning map"
STORMANALYZER_MAP = "StormAnalyzer outlook map"
REFLECTIVITY_RADAR = "Reflectivity"
VELOCITY_RADAR = "Velocity"
OUTLOOK_EXTENT = (4.0, 16.0, 47.0, 56.0)
OUTLOOK_DAY_LABELS = tuple(f"Day {day}" for day in range(1, 3))
DWD_WARNING_WFS_URL = "https://maps.dwd.de/geoserver/dwd/ows"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PRESSURE_LEVELS_HPA = (1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400)
ECMWF_PRESSURE_LEVELS_HPA = (1000, 925, 850, 700, 600, 500, 400)
ESRI_EXPORT_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/export"
)
ESRI_ATTRIBUTION = (
    "Imagery © Esri, Vantor, Earthstar Geographics, and the GIS User Community"
)
OUTLOOK_COLORS = ("#78c679", "#f4e842", "#f6a13a", "#e95d45", "#d43b9b", "#8c42bd")
OVERALL_LEVELS = (20, 45, 60, 70, 85)
OVERALL_LABELS = ("Marginal", "Slight", "Moderate", "High", "Extreme")
OVERALL_SCORE_LABELS = (
    "Very low: <20",
    "Marginal: 20–<45",
    "Slight: 45–<60",
    "Moderate: 60–<70",
    "High: 70–<85",
    "Extreme: ≥85 / 100",
)
THREAT_PROBABILITIES = {
    "Tornado": ((20, 2), (30, 5), (40, 10), (50, 15), (60, 30), (70, 45)),
    "Hail": ((20, 2), (30, 5), (40, 10), (50, 15), (60, 30), (70, 45)),
    "Wind": ((20, 2), (30, 5), (40, 10), (50, 15), (60, 30), (70, 45)),
}
OUTLOOK_PARAMETER_SCALES = {"STP": 10.0, "SCP": 25.0}


def _format_optional_parameter(value: Any) -> str:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return "unavailable"
    if not math.isfinite(numeric_value):
        return "unavailable"
    return f"{numeric_value:.2f}"


def germany_grid_points(outlines: tuple[tuple[tuple[float, float], ...], ...], spacing: float = 0.4) -> list[tuple[float, float]]:
    # the grid covers a fixed rectangle, not the country outline.
    del outlines

    def axis_points(start: float, end: float) -> list[float]:
        values = [start + index * spacing for index in range(int((end - start) / spacing) + 1)]
        if values[-1] < end:
            values.append(end)
        return values

    latitudes = axis_points(OUTLOOK_EXTENT[2], OUTLOOK_EXTENT[3])
    longitudes = axis_points(OUTLOOK_EXTENT[0], OUTLOOK_EXTENT[1])
    return [(latitude, longitude) for latitude in latitudes for longitude in longitudes]


def calculate_germany_outlook(
    outlines: tuple[tuple[tuple[float, float], ...], ...],
    supabase: SupabaseClient | None = None,
) -> tuple[list[dict[str, Any]], datetime]:
    from weather_api import (
        OPTIONAL_STORMANALYZER_HOURLY_FIELDS,
        STORMANALYZER_HOURLY_FIELDS,
        _validate_hourly,
        get_germany_grid_weather,
    )

    points = germany_grid_points(outlines)
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    cached_outlook = None
    general_weather_grid = None
    if supabase is not None:
        cached_outlook = supabase.daily_outlook_grid(now.date())
        if cached_outlook is not None:
            try:
                general_weather_grid = supabase.daily_general_weather_grid(now.date())
            except SupabaseError as error:
                print(f"WARNING: Shared general-weather analysis is unavailable: {error}")

    if cached_outlook is None:
        weather = get_germany_grid_weather(points)
        issued_at = now
        calculation_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        weather = cached_outlook["grid"]
        issued_at = cached_outlook["issued_at"].astimezone(now.tzinfo)
        calculation_time = issued_at
        print(f"USING SUPABASE DAILY OUTLOOK ISSUED AT {issued_at.isoformat()}")

    fields = STORMANALYZER_HOURLY_FIELDS.split(",")
    required_fields = [
        field
        for field in fields
        if field not in OPTIONAL_STORMANALYZER_HOURLY_FIELDS
    ]

    def validate_grid_profiles(grid_weather: list[dict[str, Any]]) -> None:
        if len(grid_weather) != len(points):
            raise RuntimeError(
                f"Open-Meteo returned {len(grid_weather)} location forecasts for "
                f"{len(points)} outlook grid points."
            )
        for location in grid_weather:
            hourly = location.get("icon_d2")
            if not isinstance(hourly, dict):
                raise RuntimeError("Daily outlook is missing ICON-D2 hourly data.")
            _validate_hourly(hourly, required_fields)
            try:
                elevation = float(hourly["surface_elevation_m"])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError(
                    "Daily outlook is missing ICON-D2 surface elevation data."
                ) from error
            if not math.isfinite(elevation):
                raise RuntimeError(
                    "Daily outlook is missing ICON-D2 surface elevation data."
                )
            cin = hourly.get("cin")
            if not isinstance(cin, list) or len(cin) != len(hourly["time"]):
                raise RuntimeError("Daily outlook is missing matching CIN data.")

    if cached_outlook is not None:
        try:
            validate_grid_profiles(weather)
        except RuntimeError as error:
            print(
                "WARNING: Supabase daily outlook lacks the required "
                f"thermodynamic profile; refreshing from ICON-D2: {error}"
            )
            weather = get_germany_grid_weather(points)
            issued_at = now
            calculation_time = now.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )

    validate_grid_profiles(weather)

    outlook = []
    for point_index, ((latitude, longitude), location) in enumerate(zip(points, weather)):
        best_by_day: dict[int, dict[str, Any]] = {}
        hourly = location.get("icon_d2")
        for day in range(2):
            result = calculate_best_day_window(
                hourly,
                now.date() + timedelta(days=day),
                calculation_time,
            )
            if result is not None:
                best_by_day[day] = {
                    **result,
                    "latitude": latitude,
                    "longitude": longitude,
                }
                if general_weather_grid is not None and point_index < len(general_weather_grid):
                    daily_payload = general_weather_grid[point_index]
                    target_date = (now.date() + timedelta(days=day)).isoformat()
                    general_day = next(
                        (
                            forecast
                            for forecast in daily_payload.get("days", [])
                            if isinstance(forecast, dict)
                            and forecast.get("date") == target_date
                        ),
                        None,
                    )
                    if general_day is not None:
                        best_by_day[day]["general_weather"] = general_day
        outlook.extend({**entry, "day": day} for day, entry in best_by_day.items())
    return outlook, issued_at


def load_germany_outline() -> tuple[tuple[tuple[float, float], ...], ...]:
    boundary_path = Path(__file__).resolve().parent / "assets" / "germany.geojson"
    with boundary_path.open(encoding="utf-8") as boundary_file:
        feature = json.load(boundary_file)

    geometry = feature.get("geometry", {})
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        polygons = [coordinates]
    elif geometry_type == "MultiPolygon":
        polygons = coordinates
    else:
        raise ValueError(f"Unsupported Germany boundary geometry: {geometry_type!r}.")

    outlines: list[tuple[tuple[float, float], ...]] = []
    for polygon in polygons:
        if not polygon:
            continue
        outline = tuple((float(longitude), float(latitude)) for longitude, latitude, *_ in polygon[0])
        if len(outline) < 4:
            raise ValueError("A Germany boundary polygon has too few coordinates.")
        outlines.append(outline)
    if not outlines:
        raise ValueError("The Germany boundary asset contains no exterior polygons.")
    return tuple(outlines)


def load_outlook_boundaries() -> tuple[tuple[tuple[float, float], ...], ...]:
    boundary_path = Path(__file__).resolve().parent / "assets" / "countries_110m.geojson"
    with boundary_path.open(encoding="utf-8") as boundary_file:
        feature_collection = json.load(boundary_file)

    west, east, south, north = OUTLOOK_EXTENT
    outlines: list[tuple[tuple[float, float], ...]] = []
    for feature in feature_collection.get("features", ()):
        bounds = feature.get("bbox")
        if bounds and (bounds[2] < west or bounds[0] > east or bounds[3] < south or bounds[1] > north):
            continue
        geometry = feature.get("geometry") or {}
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates") or ()
        polygons = [coordinates] if geometry_type == "Polygon" else coordinates if geometry_type == "MultiPolygon" else ()
        for polygon in polygons:
            if polygon:
                ring = tuple((float(longitude), float(latitude)) for longitude, latitude, *_ in polygon[0])
                if len(ring) > 2:
                    outlines.append(ring)
    return tuple(outlines)


def zoom_axis_limits(
    current: tuple[float, float],
    base: tuple[float, float],
    anchor: float,
    scale_factor: float,
) -> tuple[float, float]:

    current_span = current[1] - current[0]
    base_span = base[1] - base[0]
    new_span = min(
        base_span,
        max(base_span / MAX_MAP_ZOOM, current_span * scale_factor),
    )
    if new_span == base_span:
        return base
    anchor_ratio = (anchor - current[0]) / current_span
    return (
        anchor - new_span * anchor_ratio,
        anchor + new_span * (1 - anchor_ratio),
    )


def fit_extent_to_aspect(
    extent: tuple[float, float, float, float],
    aspect_ratio: float,
) -> tuple[float, float, float, float]:

    if aspect_ratio <= 0:
        raise ValueError("Map image aspect ratio must be positive.")
    west, east, south, north = extent
    center_x = (west + east) / 2
    center_y = (south + north) / 2
    width = east - west
    height = north - south
    if width <= 0 or height <= 0:
        raise ValueError("Map extent must have positive width and height.")
    if width / height < aspect_ratio:
        width = height * aspect_ratio
    else:
        height = width / aspect_ratio
    return (
        center_x - width / 2,
        center_x + width / 2,
        center_y - height / 2,
        center_y + height / 2,
    )


def esri_bbox(
    extent: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:

    west, east, south, north = extent
    return west, south, east, north


def fetch_esri_imagery(
    extent: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[np.ndarray, tuple[float, float, float, float]]:

    image_extent = fit_extent_to_aspect(extent, width / height)
    parameters = urlencode(
        {
            "bbox": ",".join(
                f"{coordinate:.8f}" for coordinate in esri_bbox(image_extent)
            ),
            "bboxSR": "4326",
            "imageSR": "4326",
            "size": f"{width},{height}",
            "format": "jpg",
            "f": "image",
        }
    )
    with urlopen(f"{ESRI_EXPORT_URL}?{parameters}", timeout=20) as response:
        with Image.open(response) as image:
            return np.asarray(image.convert("RGB")), image_extent


def fetch_dwd_warning_overlay(
    extent: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    image_extent = fit_extent_to_aspect(extent, width / height)
    west, east, south, north = image_extent
    parameters = urlencode(
        {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "dwd:Warnungen_Gemeinden_vereinigt",
            "outputFormat": "application/json",
            "srsName": "CRS:84",
            "bbox": f"{west:.8f},{south:.8f},{east:.8f},{north:.8f},CRS:84",
            "count": "2000",
        }
    )
    with urlopen(f"{DWD_WARNING_WFS_URL}?{parameters}", timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(payload.get("message", "DWD warning query failed"))

    features = payload.get("features")
    if not isinstance(features, list):
        raise RuntimeError("DWD warning query returned no feature collection.")

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    drawing = ImageDraw.Draw(image)
    for feature in features:
        properties = feature.get("properties", {})
        color_values = properties.get("EC_AREA_COLOR")
        try:
            color = tuple(int(value) for value in color_values.split())
        except (AttributeError, ValueError) as error:
            raise RuntimeError("DWD warning has an invalid area color.") from error
        if len(color) != 3 or any(value < 0 or value > 255 for value in color):
            raise RuntimeError("DWD warning has an invalid area color.")

        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            raise RuntimeError("DWD warning is missing its geometry.")
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "Polygon":
            polygons = [coordinates]
        elif geometry_type == "MultiPolygon":
            polygons = coordinates
        else:
            raise RuntimeError(f"Unsupported DWD warning geometry: {geometry_type!r}.")

        for polygon in polygons:
            if not polygon:
                continue
            pixels = [
                (
                    round((float(point[0]) - west) / (east - west) * (width - 1)),
                    round((north - float(point[1])) / (north - south) * (height - 1)),
                )
                for point in polygon[0]
            ]
            drawing.polygon(
                pixels,
                fill=(*color, 110),
                outline=(*color, 235),
                width=2,
            )

    return np.asarray(image), image_extent


def fetch_dwd_warning_at(latitude: float, longitude: float) -> list[dict[str, Any]]:

    delta = 0.00001
    parameters = urlencode(
        {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "dwd:Warnungen_Gemeinden",
            "outputFormat": "application/json",
            "srsName": "CRS:84",
            "bbox": (
                f"{longitude - delta:.6f},{latitude - delta:.6f},"
                f"{longitude + delta:.6f},{latitude + delta:.6f},CRS:84"
            ),
        }
    )
    with urlopen(f"{DWD_WARNING_WFS_URL}?{parameters}", timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(payload.get("message", "DWD warning query failed"))
    return [
        feature.get("properties", {})
        for feature in payload.get("features", [])
        if isinstance(feature, dict)
    ]


def fetch_openmeteo_nowcast(
    latitude: float,
    longitude: float,
) -> tuple[str, dict[str, Any]]:
    from weather_api import STORMANALYZER_HOURLY_FIELDS, WeatherRateLimitError, get_weather_json

    common = {
        "latitude": f"{latitude:.5f}",
        "longitude": f"{longitude:.5f}",
        "wind_speed_unit": "kmh",
        "forecast_hours": "6",
        "timezone": "auto",
    }
    errors: list[str] = []
    for model in ("icon_d2", "ecmwf_ifs025"):
        model_parameters = {
            **common,
            "models": model,
            "hourly": STORMANALYZER_HOURLY_FIELDS,
        }
        try:
            payload = get_weather_json(
                OPEN_METEO_FORECAST_URL,
                model_parameters,
                timeout=15,
                cache_seconds=3600,
            )
            if payload.get("error"):
                raise RuntimeError(payload.get("reason", "model unavailable"))
            if not payload.get("hourly", {}).get("time"):
                raise RuntimeError("model returned no hourly conditions")
            return model, payload
        except WeatherRateLimitError:
            raise
        except Exception as error:
            errors.append(f"{model}: {type(error).__name__}: {error}")
    raise RuntimeError("; ".join(errors))


def fetch_storm_cell_winds(cells: tuple[StormCell, ...]) -> dict[str, tuple[float, float]]:
    from weather_api import get_weather_json_batch

    if not cells:
        return {}

    winds: dict[str, tuple[float, float]] = {}
    for start in range(0, len(cells), 100):
        batch = cells[start:start + 100]
        payloads = get_weather_json_batch(
            OPEN_METEO_FORECAST_URL,
            {
                "latitude": ",".join(f"{cell.latitude:.5f}" for cell in batch),
                "longitude": ",".join(f"{cell.longitude:.5f}" for cell in batch),
                "models": "icon_d2",
                "hourly": "wind_speed_700hPa,wind_direction_700hPa",
                "forecast_hours": "3",
                "timezone": "Europe/Berlin",
                "wind_speed_unit": "kmh",
            },
            timeout=20,
            cache_seconds=300,
        )
        if len(payloads) != len(batch):
            raise RuntimeError(
                "Open-Meteo returned a different number of wind profiles than storm cells."
            )
        for cell, payload in zip(batch, payloads):
            hourly = payload.get("hourly", {})
            speeds = hourly.get("wind_speed_700hPa", [])
            directions = hourly.get("wind_direction_700hPa", [])
            for speed, direction in zip(speeds, directions):
                if speed is None or direction is None:
                    continue
                try:
                    wind = float(speed), float(direction)
                except (TypeError, ValueError):
                    continue
                if np.isfinite(wind).all() and wind[0] > 0:
                    winds[cell.identifier] = wind
                    break
    return winds


def fetch_ecmwf_cin(
    latitude: float,
    longitude: float,
    valid_time: str | None,
) -> float | None:
    """Fetch ECMWF IFS CIN and return the value nearest the requested hour."""
    from weather_api import get_weather_json

    payload = get_weather_json(
        OPEN_METEO_FORECAST_URL,
        {
            "latitude": f"{latitude:.5f}",
            "longitude": f"{longitude:.5f}",
            "models": "ecmwf_ifs",
            "hourly": "convective_inhibition",
            "forecast_days": "2",
            "timezone": "auto",
        },
        timeout=20,
        cache_seconds=3600,
    )

    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    values = hourly.get("convective_inhibition", [])
    if not valid_time or not times or not values:
        return None

    def parse_valid_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Europe/Berlin"))
        return parsed.astimezone(ZoneInfo("UTC"))

    target = parse_valid_time(str(valid_time))
    matched_index = min(
        range(min(len(times), len(values))),
        key=lambda index: abs((parse_valid_time(str(times[index])) - target).total_seconds()),
    )
    difference = abs((parse_valid_time(str(times[matched_index])) - target).total_seconds())
    value = values[matched_index]
    if value is None or difference > 90 * 60:
        return None
    return -abs(float(value))


def derive_bulk_shear(payload: dict[str, Any]) -> dict[str, float]:

    hourly = payload.get("hourly", {})
    elevation = float(payload.get("elevation", 0) or 0)

    def first_value(key: str) -> float | None:
        values = hourly.get(key, [])
        if not values or values[0] is None:
            return None
        try:
            return float(values[0])
        except (TypeError, ValueError):
            return None

    def components(speed: float, direction: float) -> tuple[float, float]:
        radians = np.deg2rad(direction)
        return -speed * np.sin(radians), -speed * np.cos(radians)

    surface_speed = first_value("wind_speed_10m")
    surface_direction = first_value("wind_direction_10m")
    if surface_speed is None or surface_direction is None:
        return {}
    profile = [(10.0, *components(surface_speed, surface_direction))]
    for level in PRESSURE_LEVELS_HPA:
        speed = first_value(f"wind_speed_{level}hPa")
        direction = first_value(f"wind_direction_{level}hPa")
        height = first_value(f"geopotential_height_{level}hPa")
        if speed is None or direction is None or height is None:
            continue
        height_agl = height - elevation
        if height_agl > 10:
            profile.append((height_agl, *components(speed, direction)))
    profile.sort(key=lambda point: point[0])
    heights = np.asarray([point[0] for point in profile], dtype=float)
    u_wind = np.asarray([point[1] for point in profile], dtype=float)
    v_wind = np.asarray([point[2] for point in profile], dtype=float)

    result: dict[str, float] = {}
    for label, top in (("0-1km", 1000.0), ("0-6km", 6000.0)):
        if len(profile) < 2 or top > heights[-1]:
            continue
        top_u = float(np.interp(top, heights, u_wind))
        top_v = float(np.interp(top, heights, v_wind))
        result[label] = float(
            np.hypot(top_u - u_wind[0], top_v - v_wind[0])
        )
    return result


def fetch_ecmwf_wind_profile(latitude: float, longitude: float) -> dict[str, Any]:

    fields = ",".join(
        field
        for level in ECMWF_PRESSURE_LEVELS_HPA
        for field in (
            f"wind_speed_{level}hPa",
            f"wind_direction_{level}hPa",
            f"geopotential_height_{level}hPa",
        )
    )
    from weather_api import get_weather_json

    return get_weather_json(
        OPEN_METEO_FORECAST_URL,
        {
            "latitude": f"{latitude:.5f}",
            "longitude": f"{longitude:.5f}",
            "models": "ecmwf_ifs025",
            "hourly": f"wind_speed_10m,wind_direction_10m,{fields}",
            "forecast_hours": "6",
            "timezone": "auto",
            "wind_speed_unit": "ms",
        },
        timeout=15,
        cache_seconds=3600,
    )


def derive_ecmwf_srh(payload: dict[str, Any]) -> dict[str, float]:

    hourly = payload.get("hourly", {})
    elevation = float(payload.get("elevation", 0) or 0)

    def first_value(key: str) -> float | None:
        values = hourly.get(key, [])
        if not values or values[0] is None:
            return None
        try:
            return float(values[0])
        except (TypeError, ValueError):
            return None

    def components(speed: float, direction: float) -> tuple[float, float]:
        radians = np.deg2rad(direction)
        return -speed * np.sin(radians), -speed * np.cos(radians)

    surface_speed = first_value("wind_speed_10m")
    surface_direction = first_value("wind_direction_10m")
    if surface_speed is None or surface_direction is None:
        return {}
    profile = [(10.0, *components(surface_speed, surface_direction))]
    for level in ECMWF_PRESSURE_LEVELS_HPA:
        speed = first_value(f"wind_speed_{level}hPa")
        direction = first_value(f"wind_direction_{level}hPa")
        height = first_value(f"geopotential_height_{level}hPa")
        if speed is None or direction is None or height is None:
            continue
        height_agl = height - elevation
        if height_agl > 10:
            profile.append((height_agl, *components(speed, direction)))
    profile.sort(key=lambda point: point[0])
    if len(profile) < 3 or profile[-1][0] < 6000:
        return {}

    heights = np.asarray([point[0] for point in profile], dtype=float)
    u_wind = np.asarray([point[1] for point in profile], dtype=float)
    v_wind = np.asarray([point[2] for point in profile], dtype=float)

    def wind_at(height: float) -> tuple[float, float]:
        return (
            float(np.interp(height, heights, u_wind)),
            float(np.interp(height, heights, v_wind)),
        )

    def mean_wind(bottom: float, top: float) -> tuple[float, float]:
        levels = [bottom]
        levels.extend(float(height) for height in heights if bottom < height < top)
        levels.append(top)
        u_values = [wind_at(height)[0] for height in levels]
        v_values = [wind_at(height)[1] for height in levels]
        u_integral = sum(
            (u_values[index] + u_values[index + 1])
            * (levels[index + 1] - levels[index])
            / 2
            for index in range(len(levels) - 1)
        )
        v_integral = sum(
            (v_values[index] + v_values[index + 1])
            * (levels[index + 1] - levels[index])
            / 2
            for index in range(len(levels) - 1)
        )
        return u_integral / (top - bottom), v_integral / (top - bottom)

    mean_u, mean_v = mean_wind(0, 6000)
    low_u, low_v = mean_wind(0, 500)
    high_u, high_v = mean_wind(5500, 6000)
    shear_u, shear_v = high_u - low_u, high_v - low_v
    shear_length = float(np.hypot(shear_u, shear_v))
    if shear_length < 0.1:
        return {}
    storm_u = mean_u + 7.5 * shear_v / shear_length
    storm_v = mean_v - 7.5 * shear_u / shear_length

    def helicity(top: float) -> float:
        levels = [0.0]
        levels.extend(float(height) for height in heights if 0 < height < top)
        levels.append(top)
        hodograph = [wind_at(height) for height in levels]
        total = 0.0
        for (u0, v0), (u1, v1) in zip(hodograph, hodograph[1:]):
            total += (u0 - storm_u) * (v1 - v0) - (v0 - storm_v) * (u1 - u0)
        return total

    return {"0-1km": helicity(1000), "0-3km": helicity(3000)}


class StormRadarApp:


    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("StormChase | Live Storm Radar")
        self.root.geometry("1280x820")
        self.root.minsize(1000, 680)
        self.root.configure(bg=BACKGROUND)

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="storm-data")
        self._closed = False
        self._refreshing = False
        self._tracking_task: asyncio.Task[None] | None = None
        self._lightning_task: asyncio.Task[None] | None = None
        self._auth_task: asyncio.Task[None] | None = None
        self.supabase: SupabaseClient | None = None
        self.supabase_configuration_error: str | None = None
        try:
            self.supabase = SupabaseClient(SupabaseConfig.from_environment())
        except SupabaseError as error:
            self.supabase_configuration_error = str(error)
        self.auth_user: AuthUser | None = None
        self.community_reports: tuple[dict[str, Any], ...] = ()
        self._geolocator: Any | None = None
        self._location_event_token: Any | None = None
        self._location_changed_handler: Any | None = None
        self._has_device_fix = False
        self.radar_frame: RadarFrame | None = None
        self.rain_cell_tracker = RainCellTracker()
        self.velocity_radar_frame: RadarFrame | None = None
        self.velocity_radar_site: VelocityRadarSite | None = None
        self.velocity_radar_frames: dict[str, RadarFrame] = {}
        self.velocity_rotation_detections: dict[str, tuple[RotationDetection, ...]] = {}
        self._velocity_loading_sites: set[str] = set()
        self.storm_update = StormUpdate(cells=(), timestamp=None)
        self.cell_motion_winds: dict[str, tuple[float, float]] = {}
        self.location: LocationFix | None = None
        self.selected_cell: StormCell | RainCell | None = None
        self.rain_cells: tuple[RainCell, ...] = ()
        self.lightning_strikes: dict[str, LightningStrike] = {}
        self._visible_cells: list[StormCell | RainCell] = []
        self.track_history: dict[str, list[tuple[float, float]]] = {}
        self._cell_artist: Any | None = None
        self._rain_artist: Any | None = None
        self.germany_outline = load_germany_outline()
        self.outlook_boundaries = load_outlook_boundaries()
        self._map_xlim = GERMANY_EXTENT[:2]
        self._map_ylim = GERMANY_EXTENT[2:]
        self._last_map_scroll_at = 0.0
        self.map_mode = LIVE_MAP
        self.radar_product = REFLECTIVITY_RADAR
        self._basemap_image: np.ndarray | None = None
        self._basemap_extent: tuple[float, float, float, float] | None = None
        self._basemap_artist: Any | None = None
        self._basemap_after_id: str | None = None
        self._basemap_requested_key: tuple[float, float, float, float, int, int] | None = None
        self._warning_image: np.ndarray | None = None
        self._warning_extent: tuple[float, float, float, float] | None = None
        self._warning_after_id: str | None = None
        self._warning_requested_key: tuple[float, float, float, float, int, int] | None = None
        self._weather_request_id = 0
        self._warning_click_request_id = 0
        self._weather_location: tuple[float, float] | None = None
        self._storm_outlook: list[dict[str, Any]] = []
        self._storm_outlook_loading = False
        self._storm_outlook_error: str | None = None
        self._storm_outlook_loaded_at: datetime | None = None
        self._outlook_use_local_feed = False
        self._outlook_day = 0
        self._outlook_metric = "Overall risk"
        self._outlook_location: tuple[float, float] | None = None
        self._outlook_location_source: str | None = None

        self._build_ui()
        self.lightning_status.set("Blitzortung lightning: connecting")
        self._refresh_auth_controls()
        if self.supabase is not None:
            self._auth_task = self.loop.create_task(self._restore_auth_session())
        else:
            self.status.set(
                "Local DWD feeds starting · Supabase reports/tracking unavailable: "
                f"{self.supabase_configuration_error}"
            )
        self.root.after(60_000, self._expire_lightning_strikes)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(25, self._pump_asyncio)
        self.root.after(100, self.refresh_data)
        self.root.after(DATA_REFRESH_SECONDS * 1000, self._schedule_refresh)
        self._lightning_task = self.loop.create_task(
            stream_blitzortung_lightning(
                self._on_lightning_strike,
                self.lightning_status.set,
            )
        )

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "TFrame",
            background=BACKGROUND,
        )
        style.configure(
            "Panel.TFrame",
            background=PANEL,
        )
        style.configure(
            "Panel.TLabelframe",
            background=PANEL,
            bordercolor="#52636c",
        )
        style.configure(
            "Panel.TLabelframe.Label",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI Semibold", 9),
        )
        style.configure(
            "TLabel",
            background=BACKGROUND,
            foreground=FOREGROUND,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Panel.TLabel",
            background=PANEL,
            foreground=FOREGROUND,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Title.TLabel",
            background=BACKGROUND,
            foreground=FOREGROUND,
            font=("Segoe UI Semibold", 21),
        )
        style.configure(
            "Section.TLabel",
            background=PANEL,
            foreground=ACCENT,
            font=("Segoe UI Semibold", 11),
        )
        style.configure(
            "Status.TLabel",
            background=BACKGROUND,
            foreground=MUTED,
            font=("Segoe UI", 9),
        )
        style.configure(
            "TButton",
            background="#263b46",
            foreground=FOREGROUND,
            padding=(10, 7),
            borderwidth=0,
        )
        style.map("TButton", background=[("active", "#355563")])

        self.status = tk.StringVar()
        self.location_status = tk.StringVar()
        self.lightning_status = tk.StringVar()
        self.alert_status = tk.StringVar()
        self.weather_detail = tk.StringVar()
        self.warning_detail = tk.StringVar()
        self.cell_detail = tk.StringVar()
        self.auth_status = tk.StringVar(value="Set a report username")
        self.latitude_input = tk.StringVar()
        self.longitude_input = tk.StringVar()

        header = ttk.Frame(self.root, padding=(22, 16, 22, 10))
        header.pack(fill="x")
        ttk.Label(header, text="STORMCHASE", style="Title.TLabel").pack(side="left")
        self.auth_button = ttk.Button(
            header,
            text="Set username",
            command=self._on_auth_button,
        )
        self.auth_button.pack(side="right", pady=(5, 0), padx=(8, 0))
        ttk.Label(
            header,
            textvariable=self.auth_status,
            style="Status.TLabel",
        ).pack(side="right", padx=(8, 4), pady=(5, 0))
        ttk.Button(header, text="Refresh data", command=self.refresh_data).pack(
            side="right", pady=(5, 0)
        )
        ttk.Label(
            header,
            textvariable=self.lightning_status,
            style="Status.TLabel",
        ).pack(side="right", padx=(0, 12), pady=(5, 0))

        body = ttk.Frame(self.root, padding=(18, 4, 18, 10))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(body, style="Panel.TFrame", padding=14, width=270)
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        sidebar.grid_propagate(False)
        ttk.Label(sidebar, text="LOCATION", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            sidebar,
            text="Device location is used only after you opt in. It stays on this device.",
            style="Panel.TLabel",
            wraplength=230,
            justify="left",
        ).pack(anchor="w", pady=(8, 10))
        ttk.Button(
            sidebar,
            text="Enable device location",
            command=self.enable_location,
        ).pack(fill="x")
        ttk.Button(
            sidebar,
            text="Stop location tracking",
            command=self.stop_location_tracking,
        ).pack(fill="x", pady=(6, 8))
        ttk.Label(sidebar, text="USE COORDINATES INSTEAD", style="Section.TLabel").pack(
            anchor="w"
        )
        coordinate_row = ttk.Frame(sidebar, style="Panel.TFrame")
        coordinate_row.pack(fill="x", pady=(8, 6))
        ttk.Entry(coordinate_row, textvariable=self.latitude_input, width=10).pack(
            side="left"
        )
        ttk.Entry(coordinate_row, textvariable=self.longitude_input, width=10).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(
            sidebar,
            text="Set map location",
            command=self.set_manual_location,
        ).pack(fill="x")

        ttk.Label(sidebar, text="STORM CELLS", style="Section.TLabel").pack(
            anchor="w", pady=(20, 7)
        )
        self.cell_list = tk.Listbox(
            sidebar,
            height=12,
            bg="#111b22",
            fg=FOREGROUND,
            selectbackground="#276b64",
            selectforeground="white",
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=("Segoe UI", 9),
        )
        self.cell_list.pack(fill="both", expand=True)
        self.cell_list.bind("<<ListboxSelect>>", self._on_cell_list_select)
        ttk.Label(
            sidebar,
            textvariable=self.cell_detail,
            style="Panel.TLabel",
            wraplength=230,
            justify="left",
        ).pack(fill="x", pady=(8, 4))
        self.file_report_button_widget = ttk.Button(
            sidebar,
            text="File report for selected cell",
            command=self.file_report_button,
            state="disabled",
        )
        self.file_report_button_widget.pack(fill="x", pady=(4, 0))

        map_panel = ttk.Frame(body, style="Panel.TFrame", padding=10)
        map_panel.grid(row=0, column=1, sticky="nsew")
        map_panel.rowconfigure(1, weight=1)
        map_panel.columnconfigure(0, weight=1)
        map_toolbar = ttk.Frame(map_panel, style="Panel.TFrame")
        map_toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 6))
        self.map_mode_var = tk.StringVar(value=LIVE_MAP)
        ttk.Combobox(
            map_toolbar,
            textvariable=self.map_mode_var,
            values=(LIVE_MAP, DWD_WARNING_MAP, STORMANALYZER_MAP),
            state="readonly",
            width=25,
        ).pack(side="left", padx=(12, 0))
        self.map_mode_var.trace_add("write", self._on_map_mode_change)
        self.radar_product_var = tk.StringVar(value=REFLECTIVITY_RADAR)
        self.radar_product_combo = ttk.Combobox(
            map_toolbar,
            textvariable=self.radar_product_var,
            values=(REFLECTIVITY_RADAR, VELOCITY_RADAR),
            state="readonly",
            width=13,
        )
        self.radar_product_combo.pack(side="left", padx=(8, 0))
        self.radar_product_var.trace_add("write", self._on_radar_product_change)
        self.radar_product_status = tk.StringVar(value="Reflectivity: waiting for data")
        ttk.Label(
            map_toolbar,
            textvariable=self.radar_product_status,
            style="Status.TLabel",
        ).pack(side="left", padx=(8, 0))
        self.outlook_day_var = tk.StringVar(value="Day 1")
        self.outlook_metric_var = tk.StringVar(value="Overall risk")
        self.outlook_day_combo = ttk.Combobox(
            map_toolbar, textvariable=self.outlook_day_var,
            values=OUTLOOK_DAY_LABELS, state="readonly", width=7,
        )
        self.outlook_day_combo.pack(side="left", padx=(8, 0))
        self.outlook_day_var.trace_add("write", self._on_outlook_selection_change)
        self.outlook_metric_combo = ttk.Combobox(
            map_toolbar, textvariable=self.outlook_metric_var,
            values=("Overall risk", "Tornado", "Hail", "Wind", "STP", "SCP"),
            state="readonly", width=12,
        )
        self.outlook_metric_combo.pack(side="left", padx=(5, 0))
        self.outlook_metric_var.trace_add("write", self._on_outlook_selection_change)
        self.outlook_day_combo.configure(state="disabled")
        self.outlook_metric_combo.configure(state="disabled")
        self.outlook_load_button = ttk.Button(
            map_toolbar,
            text="Load map",
            width=11,
            command=self._ensure_storm_outlook,
            state="disabled",
        )
        self.outlook_load_button.pack(side="left", padx=(8, 0))
        self.outlook_source_button = ttk.Button(
            map_toolbar,
            text="Use local feed",
            width=14,
            command=self._toggle_outlook_source,
            state="disabled",
        )
        self.outlook_source_button.pack(side="left", padx=(5, 0))
        ttk.Button(
            map_toolbar,
            text="Zoom +",
            width=8,
            command=lambda: self._change_zoom(1 / 1.2),
        ).pack(side="right", padx=(5, 0))
        ttk.Button(
            map_toolbar,
            text="Zoom −",
            width=8,
            command=lambda: self._change_zoom(1.2),
        ).pack(side="right", padx=(5, 0))
        ttk.Button(
            map_toolbar,
            text="Reset",
            width=7,
            command=self._reset_map_zoom,
        ).pack(side="right")
        self.figure = Figure(figsize=(8, 6), dpi=100, facecolor=PANEL)
        self.axes = self.figure.add_subplot(111)
        self.map_canvas = FigureCanvasTkAgg(self.figure, master=map_panel)
        self.map_canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")
        self.map_canvas.mpl_connect("pick_event", self._on_map_pick)
        self.map_canvas.mpl_connect("button_press_event", self._on_map_click)
        self.map_canvas.mpl_connect("scroll_event", self._on_map_scroll)
        self.map_canvas.mpl_connect("resize_event", self._on_map_resize)
        report_panel = ttk.LabelFrame(
            map_panel,
            text="Details and community reports",
            style="Panel.TLabelframe",
            padding=(6, 4),
            height=150,
        )
        self.report_panel = report_panel
        report_panel.grid(row=2, column=0, sticky="ew", padx=8, pady=(8, 2))
        report_panel.grid_propagate(False)
        report_panel.columnconfigure(0, weight=1)
        report_panel.rowconfigure(0, weight=1)
        self.report_notebook = ttk.Notebook(report_panel)
        self.report_notebook.grid(row=0, column=0, sticky="nsew")
        details_tab = ttk.Frame(self.report_notebook)
        community_tab = ttk.Frame(self.report_notebook)
        details_tab.columnconfigure(0, weight=1)
        details_tab.rowconfigure(0, weight=1)
        community_tab.columnconfigure(0, weight=1)
        community_tab.rowconfigure(0, weight=1)
        self.report_notebook.add(details_tab, text="Details")
        self.report_notebook.add(community_tab, text="Community")
        self.outlook_analysis_tab = ttk.Frame(self.report_notebook)
        self.report_notebook.add(self.outlook_analysis_tab, text="Outlook analysis")
        self._build_outlook_analysis_tab()
        self.report_text = tk.Text(
            details_tab,
            height=4,
            wrap="word",
            state="disabled",
            bg="#111b22",
            fg=FOREGROUND,
            insertbackground=FOREGROUND,
            relief="flat",
            highlightthickness=0,
            font=("Segoe UI", 9),
        )
        self.report_text.grid(row=0, column=0, sticky="nsew")
        report_scrollbar = ttk.Scrollbar(
            details_tab,
            orient="vertical",
            command=self.report_text.yview,
        )
        report_scrollbar.grid(row=0, column=1, sticky="ns")
        self.report_text.configure(yscrollcommand=report_scrollbar.set)
        self.community_report_text = tk.Text(
            community_tab,
            height=4,
            wrap="word",
            state="disabled",
            bg="#111b22",
            fg=FOREGROUND,
            relief="flat",
            highlightthickness=0,
            font=("Segoe UI", 9),
        )
        self.community_report_text.grid(row=0, column=0, sticky="nsew")
        community_scrollbar = ttk.Scrollbar(
            community_tab,
            orient="vertical",
            command=self.community_report_text.yview,
        )
        community_scrollbar.grid(row=0, column=1, sticky="ns")
        self.community_report_text.configure(yscrollcommand=community_scrollbar.set)
        self.weather_detail.trace_add("write", self._render_click_reports)
        self.warning_detail.trace_add("write", self._render_click_reports)
        self._draw_map()

    def _build_outlook_analysis_tab(self) -> None:
        self.outlook_analysis_tab.columnconfigure(0, weight=1)
        self.outlook_analysis_tab.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            self.outlook_analysis_tab,
            bg=PANEL,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            self.outlook_analysis_tab,
            orient="vertical",
            command=canvas.yview,
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        content = ttk.Frame(canvas, style="Panel.TFrame", padding=10)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(content_window, width=event.width),
        )
        canvas.bind(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"),
        )

        coordinates_box = ttk.LabelFrame(
            content,
            text="Calculation location",
            style="Panel.TLabelframe",
            padding=8,
        )
        coordinates_box.pack(fill="x", pady=(0, 10))
        self.outlook_latitude_input = tk.StringVar()
        self.outlook_longitude_input = tk.StringVar()
        ttk.Label(coordinates_box, text="Latitude", style="Panel.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 6),
        )
        ttk.Entry(
            coordinates_box,
            textvariable=self.outlook_latitude_input,
            width=14,
        ).grid(row=0, column=1, sticky="w", padx=(0, 14))
        ttk.Label(coordinates_box, text="Longitude", style="Panel.TLabel").grid(
            row=0,
            column=2,
            sticky="w",
            padx=(0, 6),
        )
        ttk.Entry(
            coordinates_box,
            textvariable=self.outlook_longitude_input,
            width=14,
        ).grid(row=0, column=3, sticky="w", padx=(0, 14))
        ttk.Button(
            coordinates_box,
            text="Use coordinates",
            command=self._set_manual_outlook_location,
        ).grid(row=0, column=4, sticky="w")
        self.outlook_location_status = tk.StringVar(
            value="No device location set. Enter coordinates to show a calculation."
        )
        ttk.Label(
            coordinates_box,
            textvariable=self.outlook_location_status,
            style="Status.TLabel",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))

        current_box = ttk.LabelFrame(
            content,
            text="Current outlook calculation",
            style="Panel.TLabelframe",
            padding=8,
        )
        current_box.pack(fill="x", pady=(0, 10))
        self.outlook_calculation_text = tk.StringVar(
            value="Enter coordinates or enable device location to show a calculation."
        )
        ttk.Label(
            current_box,
            textvariable=self.outlook_calculation_text,
            style="Panel.TLabel",
            justify="left",
            wraplength=900,
        ).pack(anchor="w", fill="x")

        manual_box = ttk.LabelFrame(
            content,
            text="Manual environment calculation",
            style="Panel.TLabelframe",
            padding=8,
        )
        manual_box.pack(fill="x")
        fields = (
            ("cape", "CAPE (J/kg)", "2000"),
            ("cin", "CIN (J/kg)", "-50"),
            ("temperature", "Temperature (°C)", "30"),
            ("dewpoint", "Dewpoint (°C)", "18"),
            ("humidity", "Humidity (%)", "70"),
            ("precipitation", "Precipitation (mm/hour)", "0"),
            ("wind_speed", "Surface wind (km/h)", "15"),
            ("srh", "Full-profile SRH (m²/s²)", "150"),
            ("high_shear", "Deep layer shear (kt)", "35"),
            ("low_shear", "Low level shear (kt)", "20"),
            ("lapse", "Lapse rate (°C/km)", "6.5"),
            ("lcl", "LCL (m)", "1000"),
        )
        self.manual_outlook_inputs: dict[str, tk.StringVar] = {}
        for index, (key, label, default) in enumerate(fields):
            row = index // 2
            column = (index % 2) * 2
            value = tk.StringVar(value=default)
            self.manual_outlook_inputs[key] = value
            ttk.Label(manual_box, text=label, style="Panel.TLabel").grid(
                row=row,
                column=column,
                sticky="w",
                padx=(0, 6),
                pady=3,
            )
            ttk.Entry(manual_box, textvariable=value, width=12).grid(
                row=row,
                column=column + 1,
                sticky="ew",
                padx=(0, 14),
                pady=3,
            )
        manual_box.columnconfigure(1, weight=1)
        manual_box.columnconfigure(3, weight=1)
        advanced_box = ttk.LabelFrame(
            manual_box,
            text="Required near-surface tornado inputs",
            style="Panel.TLabelframe",
            padding=6,
        )
        advanced_box.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Label(
            advanced_box,
            text=(
                "All fields are required. Enter "
                f"{NO_LFC_SENTINEL_M:g} m when no LFC is found in the profile."
            ),
            style="Panel.TLabel",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 3))
        profile_fields = (
            ("srh_0_1km", "0–1 km SRH (m²/s²)", "300"),
            ("srh_0_3km", "0–3 km SRH (m²/s²)", "400"),
            ("srh_efficiency", "SRH efficiency (0–1)", "1.0"),
            ("shear_0_500m", "0–500 m shear (m/s)", "15"),
            ("critical_angle", "Critical angle (°)", "90"),
            ("low_level_lapse", "0–2 km lapse rate (°C/km)", "7.5"),
            ("cape_0_3km", "0–3 km CAPE (J/kg)", "150"),
            ("lfc", "LFC height (m)", "500"),
        )
        for index, (key, label, default) in enumerate(profile_fields):
            row = index // 2 + 1
            column = (index % 2) * 2
            value = tk.StringVar(value=default)
            self.manual_outlook_inputs[key] = value
            ttk.Label(advanced_box, text=label, style="Panel.TLabel").grid(
                row=row,
                column=column,
                sticky="w",
                padx=(0, 6),
                pady=3,
            )
            ttk.Entry(advanced_box, textvariable=value, width=12).grid(
                row=row,
                column=column + 1,
                sticky="ew",
                padx=(0, 14),
                pady=3,
            )
        advanced_box.columnconfigure(1, weight=1)
        advanced_box.columnconfigure(3, weight=1)
        composite_box = ttk.LabelFrame(
            manual_box,
            text="SPC effective-layer composite inputs",
            style="Panel.TLabelframe",
            padding=6,
        )
        composite_box.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Label(
            composite_box,
            text=(
                "Enter effective-layer values here; these are distinct from the "
                "full-profile SRH and deep-layer shear above."
            ),
            style="Panel.TLabel",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 3))
        composite_fields = (
            ("mlcape", "Mixed-layer CAPE (J/kg)", "1500"),
            ("mllcl", "Mixed-layer LCL (m AGL)", "800"),
            ("mlcin", "Mixed-layer CIN (J/kg)", "-50"),
            ("mucape", "Most-unstable CAPE (J/kg)", "1500"),
            ("effective_srh", "Effective-layer SRH (m²/s²)", "150"),
            ("effective_bwd", "Effective-layer bulk wind difference (m/s)", "20"),
            ("effective_base_height", "Effective inflow base (m AGL)", "0"),
        )
        for index, (key, label, default) in enumerate(composite_fields):
            row = index // 2 + 1
            column = (index % 2) * 2
            value = tk.StringVar(value=default)
            self.manual_outlook_inputs[key] = value
            ttk.Label(composite_box, text=label, style="Panel.TLabel").grid(
                row=row,
                column=column,
                sticky="w",
                padx=(0, 6),
                pady=3,
            )
            ttk.Entry(composite_box, textvariable=value, width=12).grid(
                row=row,
                column=column + 1,
                sticky="ew",
                padx=(0, 14),
                pady=3,
            )
        composite_box.columnconfigure(1, weight=1)
        composite_box.columnconfigure(3, weight=1)
        ttk.Button(
            manual_box,
            text="Calculate manual risk",
            command=self._calculate_manual_outlook,
        ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(8, 4))
        self.manual_outlook_result = tk.StringVar(
            value="Enter environmental values and calculate a manual storm-risk assessment."
        )
        ttk.Label(
            manual_box,
            textvariable=self.manual_outlook_result,
            style="Panel.TLabel",
            justify="left",
            wraplength=900,
        ).grid(row=10, column=0, columnspan=4, sticky="ew", pady=(4, 0))

    def _set_manual_outlook_location(self) -> None:
        try:
            latitude = float(self.outlook_latitude_input.get())
            longitude = float(self.outlook_longitude_input.get())
        except ValueError:
            self.outlook_location_status.set("Enter numeric latitude and longitude.")
            return
        if (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not (-90 <= latitude <= 90 and -180 <= longitude <= 180)
        ):
            self.outlook_location_status.set(
                "Latitude must be between -90 and 90; longitude between -180 and 180."
            )
            return

        self._outlook_location_source = "manual"
        self.outlook_location_status.set(
            f"Using manual coordinates: {latitude:.4f}, {longitude:.4f}"
        )
        self._show_outlook_calculation(latitude, longitude)
        if self.map_mode == STORMANALYZER_MAP:
            self._draw_map()

    def _clear_outlook_calculation(self, message: str) -> None:
        self._outlook_location = None
        self.outlook_calculation_text.set(message)

    def _calculate_manual_outlook(self) -> None:
        try:
            values = {}
            for key, value in self.manual_outlook_inputs.items():
                values[key] = float(value.get().strip())
        except ValueError:
            self.manual_outlook_result.set(
                "Calculation error: enter a valid number in every field."
            )
            return
        if not all(math.isfinite(value) for value in values.values()):
            self.manual_outlook_result.set("Calculation error: all values must be finite numbers.")
            return

        try:
            analysis = calculate_manual_storm(values)
        except ValueError as error:
            self.manual_outlook_result.set(f"Calculation error: {error}")
            return

        threats = ", ".join(analysis["threats"]) or "No specific severe-weather threats indicated"
        criticism = "\n".join(analysis["criticism"]) or "No additional assessment notes."
        self.manual_outlook_result.set(
            f"MANUAL RESULT · Overall risk {analysis['risk']}/100 · {analysis['category']}\n"
            f"Storm type: {analysis['storm_type']}\n"
            f"Tornado {analysis['tornado']}/80 · Hail {analysis['hail']}/80 · "
            f"Wind {analysis['wind']}/80 · Supercell {analysis['supercell']}/20\n"
            f"SPC effective-layer STP {analysis['stp']:.2f} · "
            f"SCP {analysis['scp']:.2f}\n"
            f"Full-profile SRH {values['srh']:.0f} m²/s² · "
            f"Low-level profile: 0–1 km SRH {values['srh_0_1km']:.0f} · "
            f"0–3 km SRH {values['srh_0_3km']:.0f} · "
            f"SRH efficiency {values['srh_efficiency']:.2f} · "
            f"0–500 m shear {values['shear_0_500m']:.1f} m/s · "
            f"critical angle {values['critical_angle']:.0f}°\n"
            f"0–2 km lapse {values['low_level_lapse']:.1f} °C/km · "
            f"0–3 km CAPE {values['cape_0_3km']:.0f} J/kg · "
            f"LFC {values['lfc']:.0f} m\n"
            f"Threats: {threats}\n"
            f"Issued: {analysis['issued']}\n"
            f"Assessment:\n{criticism}\n"
            "Precipitation and surface wind are shown as context; "
            "the severe-thunderstorm score uses the other environmental inputs."
        )

    def _show_outlook_calculation(self, latitude: float, longitude: float) -> None:
        self._outlook_location = (longitude, latitude)
        entry = nearest_outlook_entry(
            self._storm_outlook,
            self._outlook_day,
            latitude,
            longitude,
        )
        if entry is None:
            self.outlook_calculation_text.set(
                f"No calculation is available for Day {self._outlook_day + 1} at "
                f"{latitude:.2f}° N, {longitude:.2f}° E yet."
            )
            return

        environment = entry["environment"]
        threats = ", ".join(entry["threats"]) or "No specific severe-weather threats indicated"
        criticism = "\n".join(entry["criticism"]) or "No additional assessment notes."
        general_weather_text = ""
        general_weather = entry.get("general_weather")
        analysis = general_weather.get("analysis") if isinstance(general_weather, dict) else None
        if isinstance(analysis, dict):
            probability = analysis.get("precipitation_probability_percent")
            probability_text = (
                f"{probability:.0f}% precipitation chance"
                if isinstance(probability, (int, float))
                else "precipitation chance unavailable"
            )
            rain_text = "Rain likely" if analysis.get("rain_likely") else "Rain not indicated"
            frozen_text = (
                " · Frozen precipitation likely"
                if analysis.get("frozen_precipitation_likely")
                else ""
            )
            general_weather_text = (
                f"General weather: {analysis.get('headline', analysis.get('condition', 'Unavailable'))} · "
                f"{analysis.get('condition', 'Unavailable')} · {probability_text} · {rain_text}"
                f"{frozen_text}\n"
            )
        self.outlook_calculation_text.set(
            f"Day {self._outlook_day + 1} · nearest forecast grid point "
            f"{entry['latitude']:.2f}° N, {entry['longitude']:.2f}° E · {entry['time']}\n"
            f"Selected location: {latitude:.2f}° N, {longitude:.2f}° E\n"
            f"Overall risk: {entry['risk']}/100 · {entry['category']} · {entry['storm_type']}\n"
            f"CAPE {environment['cape']} J/kg · CIN {environment['cin']} J/kg · "
            f"LCL {environment['lcl']} m · Lapse {environment['lapse']} °C/km\n"
            f"Temperature {environment['temperature']} °C · "
            f"Dewpoint {environment['dewpoint']} °C · Humidity {environment['humidity']}%\n"
            f"Full-profile SRH {environment['srh']} m²/s² · "
            f"Deep shear {environment['high_shear']} kt · "
            f"Low-level shear {environment['low_shear']} kt\n"
            f"{general_weather_text}"
            f"Near-surface: 0–1 km SRH {environment['srh_0_1km']:.0f} · "
            f"0–3 km SRH {environment['srh_0_3km']:.0f} · "
            f"efficiency {environment['srh_efficiency']:.2f} · "
            f"0–500 m shear {environment['shear_0_500m']:.1f} m/s · "
            f"critical angle {environment['critical_angle']:.0f}°\n"
            f"0–2 km lapse {environment['low_level_lapse']:.1f} °C/km · "
            f"0–3 km CAPE {environment['cape_0_3km']:.0f} J/kg · "
            f"LFC {environment['lfc']:.0f} m · "
            f"effective-layer STP {_format_optional_parameter(environment['stp'])} · "
            f"SCP {_format_optional_parameter(environment['scp'])}\n"
            f"Tornado {entry['tornado']}/80 · Hail {entry['hail']}/80 · "
            f"Wind {entry['wind']}/80 · Supercell {entry['supercell']}/20\n"
            f"Threats: {threats}\nIssued: {entry['issued']}\nAssessment:\n{criticism}"
        )

    def _on_radar_product_change(self, *_args: Any) -> None:
        self.radar_product = self.radar_product_var.get()
        if self.radar_product == VELOCITY_RADAR:
            if self.velocity_radar_site is None:
                center_longitude = sum(self.axes.get_xlim()) / 2
                center_latitude = sum(self.axes.get_ylim()) / 2
                self._select_velocity_radar_site(
                    nearest_velocity_radar_site(center_latitude, center_longitude)
                )
            elif self.velocity_radar_site.site.value not in self.velocity_radar_frames:
                self._request_velocity_radar(self.velocity_radar_site)
            else:
                self.velocity_radar_frame = self.velocity_radar_frames[
                    self.velocity_radar_site.site.value
                ]
                self.radar_product_status.set(
                    self._velocity_site_status(
                        self.velocity_radar_site, self.velocity_radar_frame
                    )
                )
        elif self.radar_product == REFLECTIVITY_RADAR:
            self.radar_product_status.set(
                self._radar_status_text(REFLECTIVITY_RADAR, self.radar_frame)
            )
            if self.radar_frame is None:
                self.refresh_data()
        else:
            self.radar_product_status.set(
                self._radar_status_text(VELOCITY_RADAR, self.velocity_radar_frame)
            )
        self._draw_map()

    @staticmethod
    def _velocity_site_status(
        site: VelocityRadarSite,
        frame: RadarFrame | None,
    ) -> str:
        if frame is None:
            return f"Velocity · {site.name}: unavailable"
        return f"Velocity · {site.name}: {StormRadarApp._format_time(frame.timestamp)}"

    def _select_velocity_radar_site(self, site: VelocityRadarSite) -> None:
        self.velocity_radar_site = site
        self.velocity_radar_frame = self.velocity_radar_frames.get(site.site.value)
        if self.velocity_radar_frame is None:
            self._request_velocity_radar(site)
        else:
            self.radar_product_status.set(
                self._velocity_site_status(site, self.velocity_radar_frame)
            )
        self._draw_map()

    def _request_velocity_radar(self, site: VelocityRadarSite) -> None:
        site_code = site.site.value
        self.radar_product_status.set(f"Velocity · {site.name}: loading…")
        if site_code in self._velocity_loading_sites:
            return
        self._velocity_loading_sites.add(site_code)
        self.loop.create_task(self._load_velocity_radar(site))

    def _pump_asyncio(self) -> None:
        if self._closed:
            return
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        self.root.after(25, self._pump_asyncio)

    def _schedule_refresh(self) -> None:
        if self._closed:
            return
        self.refresh_data()
        self.root.after(DATA_REFRESH_SECONDS * 1000, self._schedule_refresh)

    def refresh_data(self) -> None:
        if self._closed or self._refreshing:
            return
        self._refreshing = True
        self.loop.create_task(self._load_weather_data())
        if self.radar_product == VELOCITY_RADAR and self.velocity_radar_site is not None:
            self._request_velocity_radar(self.velocity_radar_site)

    async def _load_weather_data(self) -> None:
        reports_future = None
        try:
            client = self.supabase
            radar_future = self.loop.run_in_executor(
                self.executor,
                fetch_latest_radar,
            )
            reports_future = (
                self.loop.run_in_executor(self.executor, client.recent_reports)
                if (
                    client is not None
                    and self.auth_user is not None
                    and self.auth_user.username is not None
                )
                else None
            )

            storm_error: BaseException | None = None
            if client is not None:
                try:
                    storm_result = await self.loop.run_in_executor(
                        self.executor,
                        client.latest_storm_update,
                    )
                except Exception as error:
                    storm_error = error
                    storm_result = None
            else:
                storm_result = None

            if storm_result is None:
                try:
                    storm_result = await self.loop.run_in_executor(
                        self.executor,
                        fetch_latest_storm_cells,
                    )
                except Exception as error:
                    if storm_error is not None:
                        error.add_note(
                            f"Supabase tracking lookup also failed: {storm_error}"
                        )
                    storm_result = error

            radar_result = await asyncio.gather(radar_future, return_exceptions=True)
            radar_result = radar_result[0]
            errors: list[BaseException] = []
            wind_error: Exception | None = None

            if isinstance(radar_result, BaseException):
                errors.append(radar_result)
            elif radar_result is not None:
                radar_frame = radar_result
                self.radar_frame = radar_frame
                try:
                    detected_cells = await self.loop.run_in_executor(
                        self.executor,
                        detect_rain_cells,
                        radar_frame,
                    )
                except Exception as error:
                    errors.append(error)
                else:
                    self.rain_cells = self.rain_cell_tracker.update(
                        detected_cells,
                        radar_frame.timestamp,
                    )
                if self.radar_product == REFLECTIVITY_RADAR:
                    self.radar_product_status.set(
                        self._radar_status_text(REFLECTIVITY_RADAR, radar_frame)
                    )

            if isinstance(storm_result, BaseException):
                errors.append(storm_result)
            elif storm_result is not None:
                self.storm_update = storm_result
                self._remember_tracks(storm_result.cells)
                try:
                    cells_without_forecast = tuple(
                        cell for cell in storm_result.cells if not cell.forecast
                    )
                    self.cell_motion_winds = await self.loop.run_in_executor(
                        self.executor,
                        fetch_storm_cell_winds,
                        cells_without_forecast,
                    )
                except Exception as error:
                    self.cell_motion_winds = {}
                    wind_error = error
                else:
                    wind_error = None

            if (
                reports_future is not None
                and self.auth_user is not None
                and self.auth_user.username is not None
            ):
                reports_result = await asyncio.gather(
                    reports_future,
                    return_exceptions=True,
                )
                reports_result = reports_result[0]
                if isinstance(reports_result, BaseException):
                    errors.append(reports_result)
                    self._sync_auth_state()
                else:
                    self.community_reports = reports_result
                    self._render_community_reports()
            else:
                self._render_community_reports()

            self._populate_cell_list()
            self._update_alert_status()
            if wind_error is not None:
                wind_status = (
                    f" · Wind projection unavailable: "
                    f"{type(wind_error).__name__}: {wind_error}"
                )
            elif storm_result is not None:
                missing_winds = sum(
                    1
                    for cell in storm_result.cells
                    if not cell.forecast
                    and cell.identifier not in self.cell_motion_winds
                )
                wind_status = (
                    f" · Wind projection unavailable for {missing_winds} cells"
                    if missing_winds > 0
                    else ""
                )
            else:
                wind_status = ""
            tracking_status = ""
            if storm_error is not None:
                tracking_status = (
                    f" · Supabase tracking unavailable; using local KONRAD3D "
                    f"({type(storm_error).__name__})"
                )
            elif client is None:
                tracking_status = " · Supabase reports/tracking not configured"
            if errors:
                self.status.set(
                    "Local data refresh failed: "
                    + "; ".join(
                        f"{type(error).__name__}: {error}" for error in errors
                    )
                    + wind_status
                    + tracking_status
                )
            else:
                radar_time = self._format_time(
                    self.radar_frame.timestamp if self.radar_frame is not None else None
                )
                storm_time = self._format_time(self.storm_update.timestamp)
                stale_products = []
                now = datetime.now(timezone.utc)
                for name, timestamp in (
                    ("reflectivity", self.radar_frame.timestamp if self.radar_frame else None),
                    ("KONRAD3D", self.storm_update.timestamp),
                ):
                    if timestamp is not None and timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    if (
                        timestamp is None
                        or now - timestamp.astimezone(timezone.utc)
                        > SHARED_DATA_STALE_AFTER
                    ):
                        stale_products.append(name)
                stale_status = (
                    f" · Stale/unavailable: {', '.join(stale_products)}"
                    if stale_products
                    else ""
                )
                if self.radar_frame is None:
                    self.status.set(
                        f"No local reflectivity frame is available. KONRAD3D: {storm_time}."
                        + stale_status
                        + wind_status
                        + tracking_status
                    )
                else:
                    self.status.set(
                        f"Local DWD · Radar {radar_time} · KONRAD3D {storm_time} · "
                        f"{len(self.storm_update.cells)} convective · "
                        f"{len(self.rain_cells)} rain cells"
                        + stale_status
                        + wind_status
                        + tracking_status
                    )
            self._draw_map()
        except Exception as error:
            self.status.set(f"Data update failed: {type(error).__name__}: {error}")
            self._draw_map()
        finally:
            self._refreshing = False
            if (
                self.auth_user is not None
                and reports_future is None
                and self.supabase is not None
                and not self._closed
            ):
                self.root.after(0, self.refresh_data)

    @staticmethod
    def _radar_status_text(product: str, frame: RadarFrame | None) -> str:
        if frame is None:
            return f"{product}: unavailable"
        return f"{product}: {StormRadarApp._format_time(frame.timestamp)}"

    async def _load_velocity_radar(
        self,
        site: VelocityRadarSite,
    ) -> None:
        site_code = site.site.value
        try:
            frame = await self.loop.run_in_executor(
                self.executor,
                fetch_latest_velocity_radar,
                site.site,
            )
        except Exception as error:
            if (
                not self._closed
                and self.radar_product == VELOCITY_RADAR
                and self.velocity_radar_site == site
            ):
                self.radar_product_status.set(
                    f"Velocity · {site.name} failed: {type(error).__name__}: {error}"
                )
        else:
            try:
                rotations = (
                    await self.loop.run_in_executor(
                        self.executor,
                        fetch_latest_mesocyclone_detections,
                    )
                    if frame is not None
                    else ()
                )
            except Exception:
                # Keep the velocity image available when the optional volume
                # mesocyclone product is delayed or unavailable.
                rotations = ()
            if not self._closed:
                if frame is not None:
                    self.velocity_radar_frames[site_code] = frame
                self.velocity_rotation_detections[site_code] = rotations
                if self.velocity_radar_site == site and frame is not None:
                    self.velocity_radar_frame = frame
                if (
                    self.radar_product == VELOCITY_RADAR
                    and self.velocity_radar_site == site
                ):
                    self.radar_product_status.set(
                        self._velocity_site_status(site, frame)
                    )
                if (
                    self.map_mode == LIVE_MAP
                    and self.radar_product == VELOCITY_RADAR
                    and self.velocity_radar_site == site
                ):
                    self._draw_map()
        finally:
            self._velocity_loading_sites.discard(site_code)

    async def _restore_auth_session(self) -> None:
        client = self.supabase
        if client is None:
            return
        restore_warning = None
        try:
            user = await self.loop.run_in_executor(
                self.executor,
                client.restore_session,
            )
        except Exception as error:
            self.status.set(
                f"Could not restore reporter identity: {type(error).__name__}: {error}"
            )
            self.auth_user = None
            restore_warning = f"Identity restore failed: {type(error).__name__}"
        else:
            self.auth_user = user
            if client.session_persistence_error:
                self.status.set(client.session_persistence_error)
            elif client.session_restore_notice:
                self.status.set(client.session_restore_notice)
        self._refresh_auth_controls()
        if restore_warning is not None:
            self.auth_status.set(restore_warning)
        elif client.session_persistence_error:
            self.auth_status.set("Reporter identity storage failed")
        elif client.session_restore_notice:
            self.auth_status.set("Reporter identity needs to be set again")
        self._render_community_reports()
        if self.auth_user is not None and self.auth_user.username is not None:
            self.refresh_data()

    def _refresh_auth_controls(self) -> None:
        if not hasattr(self, "auth_button"):
            return
        if self.supabase is None:
            self.auth_status.set("Supabase not configured")
        else:
            username = self.auth_user.username if self.auth_user is not None else None
            self.auth_status.set(
                f"Reporter: {username}" if username else "Set a report username"
            )
        self.auth_button.configure(
            text=(
                f"Reporter: {self.auth_user.username}"
                if self.auth_user is not None and self.auth_user.username is not None
                else "Set username"
            ),
            state=(
                "disabled"
                if self.supabase is None
                or (self.auth_user is not None and self.auth_user.username is not None)
                else "normal"
            ),
        )
        eligible_cell = isinstance(self.selected_cell, StormCell)
        self.file_report_button_widget.configure(
            state=(
                "normal"
                if self.supabase is not None and eligible_cell
                else "disabled"
            )
        )

    def _sync_auth_state(self) -> None:
        if (
            self.auth_user is not None
            and self.supabase is not None
            and self.supabase.current_user is None
        ):
            self.auth_user = None
            self.community_reports = ()
            self._refresh_auth_controls()
            self._render_community_reports()

    def _on_auth_button(self) -> None:
        if self.auth_user is None or self.auth_user.username is None:
            self._open_auth_dialog()

    def _open_auth_dialog(self) -> None:
        if self.supabase is None:
            messagebox.showerror(
                "Supabase not configured",
                self.supabase_configuration_error or "Supabase is unavailable.",
                parent=self.root,
            )
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Set report username")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Username").grid(row=0, column=0, sticky="w", pady=4)
        username_entry = ttk.Entry(frame, width=36)
        username_entry.grid(row=0, column=1, sticky="ew", pady=4)
        status_text = tk.StringVar(
            value=(
                "3–24 letters, numbers, or underscores. This name is permanent "
                "for this device and cannot be recovered if its saved identity is lost."
            )
        )
        ttk.Label(
            frame,
            textvariable=status_text,
            wraplength=320,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        def save_username() -> None:
            save_button.configure(state="disabled")
            dialog.protocol("WM_DELETE_WINDOW", lambda: None)
            self.loop.create_task(
                self._set_reporter_username(
                    username_entry.get(),
                    dialog,
                    status_text,
                    save_button,
                )
            )

        save_button = ttk.Button(
            frame,
            text="Save username",
            command=save_username,
        )
        save_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        frame.columnconfigure(1, weight=1)
        username_entry.focus_set()

    async def _set_reporter_username(
        self,
        username: str,
        dialog: tk.Toplevel,
        status_text: tk.StringVar,
        save_button: ttk.Button,
    ) -> None:
        client = self.supabase
        if client is None:
            return
        try:
            user = await self.loop.run_in_executor(
                self.executor,
                client.set_reporter_username,
                username,
            )
        except Exception as error:
            status_text.set(f"{type(error).__name__}: {error}")
            self.status.set(
                f"Could not set reporter identity: {type(error).__name__}: {error}"
            )
            save_button.configure(state="normal")
            dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
            return

        self.auth_user = user
        self._refresh_auth_controls()
        if client.session_persistence_error:
            self.status.set(client.session_persistence_error)
        else:
            self.status.set(f"Reports will be filed as {user.username}.")
        self._render_community_reports()
        if dialog.winfo_exists():
            dialog.grab_release()
            dialog.destroy()
        try:
            self.community_reports = await self.loop.run_in_executor(
                self.executor,
                client.recent_reports,
            )
        except Exception as error:
            self.status.set(
                f"Username saved, but reports could not load: {type(error).__name__}: {error}"
            )
        self.refresh_data()

    def _render_community_reports(self) -> None:
        if not hasattr(self, "community_report_text"):
            return
        if self.auth_user is None or self.auth_user.username is None:
            content = "Set a report username to view and file community storm reports."
        elif not self.community_reports:
            content = "No community reports have been filed yet."
        else:
            entries = []
            for report in self.community_reports:
                created_at = report.get("created_at", "time unavailable")
                username = report.get("reporter_username", "unknown reporter")
                cell_id = report.get("cell_id", "unknown cell")
                body = report.get("report", "")
                entries.append(f"{username} · {created_at} · {cell_id}\n{body}")
            content = "\n\n".join(entries)
        self.community_report_text.configure(state="normal")
        self.community_report_text.delete("1.0", tk.END)
        self.community_report_text.insert("1.0", content)
        self.community_report_text.configure(state="disabled")

    def _expire_lightning_strikes(self) -> None:
        if self._closed:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
        remaining = {
            identifier: strike
            for identifier, strike in self.lightning_strikes.items()
            if strike.timestamp >= cutoff
        }
        if len(remaining) != len(self.lightning_strikes):
            self.lightning_strikes = remaining
            self._draw_map()
        self.root.after(60_000, self._expire_lightning_strikes)

    def _on_lightning_strike(self, strike: LightningStrike) -> None:
        if self._closed:
            return
        self.lightning_strikes[strike.identifier] = strike
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
        self.lightning_strikes = {
            identifier: observed
            for identifier, observed in self.lightning_strikes.items()
            if observed.timestamp >= cutoff
        }
        self.lightning_status.set(
            f"Blitzortung lightning · {len(self.lightning_strikes)} strokes · "
            f"{self._format_time(strike.timestamp)}"
        )
        self._draw_map()

    @staticmethod
    def _format_time(value: datetime | None) -> str:
        if value is None:
            return "not available"
        return value.astimezone().strftime("%H:%M:%S %Z")

    def _remember_tracks(self, cells: tuple[StormCell, ...]) -> None:
        for cell in cells:
            history = self.track_history.setdefault(cell.identifier, [])
            point = (cell.latitude, cell.longitude)
            if not history or history[-1] != point:
                history.append(point)
            del history[:-24]

    def _populate_cell_list(self) -> None:
        selected_identifier = (
            self.selected_cell.identifier
            if self.selected_cell is not None
            else None
        )
        self.cell_list.delete(0, tk.END)
        self._visible_cells = []
        for cell in self.storm_update.cells:
            intensity = (
                f" · {cell.reflectivity_dbz:.0f} dBZ"
                if cell.reflectivity_dbz is not None
                else ""
            )
            self.cell_list.insert(
                tk.END,
                f"KONRAD3D  {cell.identifier}{intensity}",
            )
            self._visible_cells.append(cell)
        selected_index = next(
            (
                index
                for index, cell in enumerate(self._visible_cells)
                if cell.identifier == selected_identifier
            ),
            None,
        )
        if selected_index is None:
            self.selected_cell = None
            self.cell_detail.set("")
            self._refresh_auth_controls()
        else:
            self._select_cell(selected_index)
        for cell in self.rain_cells:
            self.cell_list.insert(
                tk.END,
                f"RAIN  {cell.max_reflectivity_dbz:.0f} dBZ · {cell.identifier}",
            )
            self._visible_cells.append(cell)

    def _update_alert_status(self) -> None:
        severe_cells = [
            cell
            for cell in self.storm_update.cells
            if cell.reflectivity_dbz is not None
            and cell.reflectivity_dbz >= SEVERE_THRESHOLD_DBZ
        ]
        if severe_cells:
            self.alert_status.set(
                f"⚠ {len(severe_cells)} cell(s) at or above "
                f"{SEVERE_THRESHOLD_DBZ:.0f} dBZ — "
                f"{len(self.rain_cells)} rain cells — check official warnings"
            )
        else:
            self.alert_status.set(
                f"{len(self.storm_update.cells)} convective · "
                f"{len(self.rain_cells)} rain cells "
                f"({RAIN_CELL_MIN_DBZ:.0f}–{RAIN_CELL_MAX_DBZ:.0f} dBZ)"
            )

    def _draw_outlook_legend(self) -> None:
        if self._outlook_metric in OUTLOOK_PARAMETER_SCALES:
            maximum = OUTLOOK_PARAMETER_SCALES[self._outlook_metric]
            levels = np.linspace(0.0, maximum, len(OUTLOOK_COLORS) + 1)
            labels = [
                f"{levels[index]:.1f}–<{levels[index + 1]:.1f}"
                for index in range(len(levels) - 2)
            ]
            labels.append(f"{levels[-2]:.1f}–{maximum:g} (scale maximum)")
            colors = OUTLOOK_COLORS
            title = (
                f"{self.outlook_day_var.get()} · SPC effective-layer "
                f"{self._outlook_metric} (0–{maximum:g})"
            )
        elif self._outlook_metric == "Overall risk":
            labels = list(OVERALL_SCORE_LABELS)
            colors = ("#ffffff", *OUTLOOK_COLORS[1:])
            title = f"{self.outlook_day_var.get()} · StormAnalyzer risk score / 100"
        else:
            tiers = THREAT_PROBABILITIES[self._outlook_metric]
            probabilities = [probability for _, probability in tiers]
            labels = [
                (
                    f"{probability}–<{probabilities[index + 1]}% {self._outlook_metric.lower()} "
                    f"(adjusted score {tiers[index][0]}–{tiers[index + 1][0] - 1})"
                    if index + 1 < len(probabilities)
                    else f"≥{probability}% {self._outlook_metric.lower()} (adjusted score {tiers[index][0]}+)"
                )
                for index, probability in enumerate(probabilities)
            ]
            colors = OUTLOOK_COLORS
            title = f"{self.outlook_day_var.get()} · risk-adjusted probability tiers"

        handles = [
            matplotlib.patches.Patch(
                facecolor=color,
                edgecolor="#89969b",
                label=label,
            )
            for color, label in zip(colors, labels)
        ]
        if self._outlook_metric == "Overall risk":
            handles[0].set_hatch("///")
        self.axes.legend(
            handles=handles,
            title=title,
            loc="lower right",
            bbox_to_anchor=(0.99, 0.02),
            facecolor="white",
            edgecolor="#9aa6aa",
            labelcolor="#263238",
            title_fontsize=8,
            fontsize=8,
            framealpha=0.96,
        )

    def _draw_map(self) -> None:
        self.figure.clear()
        self.axes = self.figure.add_subplot(111)
        outlook_mode = self.map_mode == STORMANALYZER_MAP
        map_background = "white" if outlook_mode else "#101b22"
        map_text = "#263238" if outlook_mode else MUTED
        self.figure.set_facecolor("white" if outlook_mode else PANEL)
        self.axes.set_facecolor(map_background)
        self.axes.set_xlim(self._map_xlim)
        self.axes.set_ylim(self._map_ylim)
        self.axes.set_xlabel("Longitude", color=map_text)
        self.axes.set_ylabel("Latitude", color=map_text)
        self.axes.tick_params(colors=map_text)
        self.axes.grid(color="#53636b", alpha=0.18 if outlook_mode else 0.28, linestyle=":")
        for spine in self.axes.spines.values():
            spine.set_color("#52636c" if not outlook_mode else "#68757a")

        self._basemap_artist = None
        if not outlook_mode and self._basemap_image is not None and self._basemap_extent is not None:
            self._basemap_artist = self.axes.imshow(
                self._basemap_image,
                extent=self._basemap_extent,
                origin="upper",
                interpolation="nearest",
                resample=False,
                aspect="auto",
                zorder=0,
            )

        if (
            self.map_mode == DWD_WARNING_MAP
            and self._warning_image is not None
            and self._warning_extent is not None
        ):
            self.axes.imshow(
                self._warning_image,
                extent=self._warning_extent,
                origin="upper",
                interpolation="nearest",
                resample=False,
                aspect="auto",
                zorder=2,
            )

        if outlook_mode:
            from matplotlib.patches import Polygon
            for outline in self.germany_outline:
                self.axes.add_patch(Polygon(outline, closed=True, facecolor="white", edgecolor="none", zorder=1))

        for outline in self.germany_outline:
            self.axes.plot(
                [point[0] for point in outline],
                [point[1] for point in outline],
                color="#27343a" if outlook_mode else "#e7eef2",
                linewidth=1.35 if outlook_mode else 1.15,
                alpha=0.95,
                zorder=4,
            )

        if outlook_mode and self._storm_outlook:
            day = self._outlook_day
            entries = [entry for entry in self._storm_outlook if entry["day"] == day]
            metric_key = {
                "Overall risk": "risk",
                "Tornado": "tornado",
                "Hail": "hail",
                "Wind": "wind",
                "STP": "stp",
                "SCP": "scp",
            }[self._outlook_metric]
            parameter_scale = OUTLOOK_PARAMETER_SCALES.get(self._outlook_metric)
            parameter_values: list[float] = []
            if parameter_scale is not None:
                available_entries = []
                for entry in entries:
                    environment = entry.get("environment")
                    if not isinstance(environment, dict):
                        continue
                    value = environment.get(metric_key)
                    try:
                        numeric_value = float(value)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(numeric_value):
                        available_entries.append(entry)
                        parameter_values.append(
                            float(np.clip(numeric_value, 0.0, parameter_scale))
                        )
                entries = available_entries
            if entries:
                longitudes = np.asarray([entry["longitude"] for entry in entries])
                latitudes = np.asarray([entry["latitude"] for entry in entries])
                scores = (
                    np.asarray(parameter_values, dtype=float)
                    if parameter_scale is not None
                    else np.asarray([entry[metric_key] for entry in entries], dtype=float)
                )
                if metric_key != "risk":
                    if parameter_scale is None:
                        composite_risk = np.asarray([entry["risk"] for entry in entries], dtype=float)
                        risk_factor = np.clip(composite_risk / 100.0, 0.0, 1.0)
                        if metric_key != "tornado":
                            scores = scores * risk_factor
                triangulation = Triangulation(longitudes, latitudes) if len(entries) >= 3 else None

                if parameter_scale is not None:
                    score_levels = tuple(np.linspace(0.0, parameter_scale, len(OUTLOOK_COLORS) + 1))
                    scores = np.minimum(scores, np.nextafter(parameter_scale, 0.0))
                    level_colors = OUTLOOK_COLORS
                    contour_options: dict[str, Any] = {}
                    minimum_to_render = score_levels[1]
                elif metric_key == "risk":
                    score_levels = (-1, *OVERALL_LEVELS, 101)
                    level_colors = ("#ffffff", *OUTLOOK_COLORS[1:])
                    contour_options: dict[str, Any] = {
                        "hatches": ("///", "", "", "", "", ""),
                    }
                    minimum_to_render = score_levels[0]
                else:
                    tiers = THREAT_PROBABILITIES[self._outlook_metric]
                    score_levels = tuple(score for score, _ in tiers) + (81,)
                    level_colors = OUTLOOK_COLORS
                    contour_options = {}
                    minimum_to_render = score_levels[0]

                if (
                    scores.size
                    and np.nanmax(scores) >= minimum_to_render
                    and triangulation is not None
                ):
                    refined_triangulation, refined_scores = UniformTriRefiner(triangulation).refine_field(scores, subdiv=4)
                    contour = self.axes.tricontourf(
                        refined_triangulation,
                        refined_scores,
                        levels=score_levels,
                        colors=level_colors,
                        **contour_options,
                        alpha=0.78,
                        zorder=3,
                    )
                elif len(entries) < 3:
                    self.axes.text(
                        0.5,
                        0.5,
                        f"Not enough valid grid points to render {self.outlook_day_var.get()}.",
                        transform=self.axes.transAxes,
                        ha="center",
                        va="center",
                        color="#263238",
                        bbox={"facecolor": "white", "edgecolor": "#9aa6aa", "alpha": 0.92},
                        zorder=8,
                    )
                elif metric_key != "risk":
                    self.axes.text(
                        0.5,
                        0.5,
                        f"No elevated {self._outlook_metric.lower()} tier forecast for "
                        f"{self.outlook_day_var.get()}.",
                        transform=self.axes.transAxes,
                        ha="center",
                        va="center",
                        color="#263238",
                        bbox={"facecolor": "white", "edgecolor": "#9aa6aa", "alpha": 0.92},
                        zorder=8,
                    )
            else:
                self.axes.text(
                    0.5,
                    0.5,
                    f"No complete forecast data available for {self.outlook_day_var.get()}.",
                    transform=self.axes.transAxes,
                    ha="center",
                    va="center",
                    color="#263238",
                    bbox={"facecolor": "white", "edgecolor": "#9aa6aa", "alpha": 0.92},
                    zorder=8,
                )
        if outlook_mode:
            for outline in self.outlook_boundaries:
                self.axes.plot(
                    [point[0] for point in outline],
                    [point[1] for point in outline],
                    color="#87969b",
                    linewidth=0.65,
                    alpha=0.9,
                    zorder=4,
                )
            for outline in self.germany_outline:
                self.axes.plot(
                    [point[0] for point in outline],
                    [point[1] for point in outline],
                    color="#27343a",
                    linewidth=1.2,
                    alpha=0.98,
                    zorder=5,
                )
            if self._outlook_location is not None:
                longitude, latitude = self._outlook_location
                self.axes.scatter(
                    [longitude],
                    [latitude],
                    marker="D",
                    s=52,
                    color="#1687ff",
                    edgecolors="white",
                    linewidths=1.2,
                    zorder=7,
                )
            self._draw_outlook_legend()
            if self._storm_outlook_loading:
                self.axes.text(
                    0.5,
                    0.5,
                    "Loading the two-day outlook grid.\nThis may take several minutes due to API rate limits.",
                    transform=self.axes.transAxes,
                    ha="center",
                    va="center",
                    color="#263238",
                    bbox={"facecolor": "white", "edgecolor": "#9aa6aa", "alpha": 0.92},
                    zorder=8,
                )
            elif self._storm_outlook_error:
                self.axes.text(
                    0.5,
                    0.5,
                    f"Outlook unavailable: {self._storm_outlook_error}",
                    transform=self.axes.transAxes,
                    ha="center",
                    va="center",
                    color="#8b1e1e",
                    wrap=True,
                    bbox={"facecolor": "white", "edgecolor": "#9aa6aa", "alpha": 0.96},
                    zorder=8,
                )
        elif outlook_mode:
            if self._storm_outlook_loading:
                message = (
                    "Loading the two-day outlook grid.\n"
                    "This may take several minutes due to API rate limits."
                )
                color = "#263238"
            elif self._storm_outlook_error:
                message = f"Outlook unavailable: {self._storm_outlook_error}"
                color = "#8b1e1e"
            elif self._storm_outlook_loaded_at is None:
                message = "Click Load map to fetch the two-day outlook."
                color = "#263238"
            else:
                message = "No complete forecast data is available for this outlook."
                color = "#263238"
            self.axes.text(
                0.5,
                0.5,
                message,
                transform=self.axes.transAxes,
                ha="center",
                va="center",
                color=color,
                wrap=True,
                bbox={"facecolor": "white", "edgecolor": "#9aa6aa", "alpha": 0.96},
                zorder=8,
            )
        radar_frame = (
            self.velocity_radar_frame
            if self.radar_product == VELOCITY_RADAR
            else self.radar_frame
        )
        if self.map_mode == LIVE_MAP:
            is_velocity = self.radar_product == VELOCITY_RADAR
            radar_cmap = "RdBu_r" if is_velocity else "turbo"
            radar_norm = (
                Normalize(vmin=-32, vmax=32)
                if is_velocity
                else Normalize(vmin=0, vmax=70)
            )
            if radar_frame is not None:
                self.axes.imshow(
                    np.ma.masked_invalid(radar_frame.geographic_values),
                    extent=radar_frame.extent,
                    origin="upper",
                    cmap=radar_cmap,
                    norm=radar_norm,
                    interpolation="nearest",
                    alpha=0.68,
                    aspect="auto",
                    zorder=2,
                )
            color_map = matplotlib.colormaps[radar_cmap]
            self.figure.colorbar(
                ScalarMappable(norm=radar_norm, cmap=color_map),
                ax=self.axes,
                orientation="horizontal",
                fraction=0.045,
                pad=0.09,
                label="Radial velocity (m/s)" if is_velocity else "Reflectivity (dBZ)",
                ticks=(-30, -15, 0, 15, 30) if is_velocity else (0, 20, 40, 60, 70),
            )
        if (
            self.map_mode == LIVE_MAP
            and self.radar_product == VELOCITY_RADAR
            and self.velocity_radar_site is not None
        ):
            site = self.velocity_radar_site
            self.axes.scatter(
                [site.longitude],
                [site.latitude],
                marker="^",
                s=85,
                color="#ff6b45",
                edgecolors="white",
                linewidths=1,
                label=f"{site.name} radar station",
                zorder=8,
            )

        motion_legend_drawn = False
        for cell in self.storm_update.cells if self.map_mode == LIVE_MAP else ():
            history = self.track_history.get(cell.identifier, [])

            path = [(longitude, latitude) for latitude, longitude in history]
            for latitude, longitude in cell.track:
                point = (longitude, latitude)
                if not path or path[-1] != point:
                    path.append(point)
            intensity = (
                cell.reflectivity_dbz
                if cell.reflectivity_dbz is not None
                else 35.0
            )
            color = matplotlib.colormaps["turbo"](
                Normalize(vmin=0, vmax=70)(intensity)
            )
            if len(path) > 1:
                self._draw_movement_track(path, color, linewidth=1.8, arrow=False)
            forecast_path = [position for _timestamp, position in cell.forecast]
            wind = self.cell_motion_winds.get(cell.identifier)
            if forecast_path:
                projected_path = forecast_path
            elif wind is not None:
                wind_speed, wind_direction = wind
                projected_path = project_cell_motion(
                    cell.latitude,
                    cell.longitude,
                    wind_direction,
                    wind_speed,
                )
            else:
                projected_path = []
            if projected_path:
                self.axes.plot(
                    [longitude for _latitude, longitude in projected_path],
                    [latitude for latitude, _longitude in projected_path],
                    color=color,
                    linewidth=1.6,
                    linestyle=(0, (4, 3)),
                    alpha=0.9,
                    zorder=4,
                    label=(
                        (
                            "KONRAD3D movement prediction"
                            if forecast_path
                            else "Estimated motion (700 hPa wind)"
                        )
                        if not motion_legend_drawn
                        else "_nolegend_"
                    ),
                )
                motion_legend_drawn = True
                self.axes.annotate(
                    "",
                    xy=(projected_path[-1][1], projected_path[-1][0]),
                    xytext=(projected_path[-2][1], projected_path[-2][0]),
                    arrowprops={
                        "arrowstyle": "-|>",
                        "color": color,
                        "linewidth": 1.8,
                        "mutation_scale": 13,
                    },
                    zorder=6,
                )
                self.axes.annotate(
                    "+60m",
                    (projected_path[-1][1], projected_path[-1][0]),
                    xytext=(4, 4),
                    textcoords="offset points",
                    color="white",
                    fontsize=7,
                    zorder=6,
                    bbox={"facecolor": "#263238", "edgecolor": "none", "alpha": 0.75, "pad": 1},
                )

        if self.map_mode == LIVE_MAP and self.storm_update.cells:
            reflectivity = [
                cell.reflectivity_dbz
                if cell.reflectivity_dbz is not None
                else 30.0
                for cell in self.storm_update.cells
            ]
            self._cell_artist = self.axes.scatter(
                [cell.longitude for cell in self.storm_update.cells],
                [cell.latitude for cell in self.storm_update.cells],
                c=reflectivity,
                cmap="turbo",
                norm=Normalize(vmin=0, vmax=70),
                edgecolors="white",
                linewidths=0.8,
                s=85,
                picker=8,
                zorder=5,
            )
        else:
            self._cell_artist = None

        for cell in self.rain_cells if self.map_mode == LIVE_MAP else ():
            if len(cell.track) > 1:
                projected_position = project_rain_cell_direction(
                    cell.track,
                    (cell.latitude, cell.longitude),
                )
                if projected_position is not None:
                    self.axes.annotate(
                        "",
                        xy=(projected_position[1], projected_position[0]),
                        xytext=(cell.longitude, cell.latitude),
                        arrowprops={
                            "arrowstyle": "-|>",
                            "color": "#72f1cf",
                            "linewidth": 1.5,
                            "mutation_scale": 11,
                            "shrinkA": 8,
                        },
                        zorder=6,
                    )

        if self.map_mode == LIVE_MAP and self.rain_cells:
            self._rain_artist = self.axes.scatter(
                [cell.longitude for cell in self.rain_cells],
                [cell.latitude for cell in self.rain_cells],
                marker="o",
                s=[
                    min(180, 34 + np.sqrt(cell.area_pixels) * 9)
                    for cell in self.rain_cells
                ],
                facecolors="#51d7b4",
                edgecolors="#e9fff8",
                linewidths=1.0,
                picker=8,
                zorder=5,
                label=f"Rain echoes ({RAIN_CELL_MIN_DBZ:.0f}–{RAIN_CELL_MAX_DBZ:.0f} dBZ)",
            )
        else:
            self._rain_artist = None

        if (
            self.map_mode == LIVE_MAP
            and self.radar_product == VELOCITY_RADAR
            and self.velocity_radar_site is not None
        ):
            level_colors = {
                1: "#22c55e",
                2: "#facc15",
                3: "#f97316",
                4: "#ef4444",
                5: "#d946ef",
            }
            level_markers = {1: "o", 2: "s", 3: "^", 4: "D", 5: "*"}
            labeled_levels: set[int] = set()
            for detection in self.velocity_rotation_detections.get(
                self.velocity_radar_site.site.value,
                (),
            ):
                # Only show DWD severity 3+ as a rotation symbol. DWD defines
                # level 3 as meeting its deep-mesocyclone criteria; lower
                # levels are weaker signatures, not confirmation of rotation.
                if detection.level < MESOCYCLONE_MIN_DISPLAY_LEVEL:
                    continue
                overlaps_konrad3d = any(
                    geographic_distance_km(
                        detection.latitude,
                        detection.longitude,
                        cell.latitude,
                        cell.longitude,
                    )
                    <= KONRAD3D_ROTATION_MATCH_RADIUS_KM
                    for cell in self.storm_update.cells
                )
                nearby_reflectivity = (
                    maximum_reflectivity_near(
                        self.radar_frame,
                        detection.latitude,
                        detection.longitude,
                    )
                    if self.radar_frame is not None
                    else None
                )
                if not (
                    overlaps_konrad3d
                    or (
                        nearby_reflectivity is not None
                        and nearby_reflectivity >= ROTATION_STORM_SUPPORT_MIN_DBZ
                    )
                ):
                    continue
                height = ""
                if detection.base_km is not None and detection.top_km is not None:
                    height = f" · {detection.base_km:.1f}–{detection.top_km:.1f} km"
                legend_label = (
                    f"DWD mesocyclone level {detection.level}{height}"
                    if detection.level not in labeled_levels
                    else "_nolegend_"
                )
                labeled_levels.add(detection.level)
                self.axes.scatter(
                    [detection.longitude],
                    [detection.latitude],
                    marker=level_markers.get(detection.level, "o"),
                    s=190 if detection.level == 5 else 125,
                    color=level_colors.get(detection.level, "#22c55e"),
                    edgecolors="#101820",
                    linewidths=1.1,
                    label=legend_label,
                    zorder=12,
                )
                self.axes.text(
                    detection.longitude,
                    detection.latitude,
                    "↻",
                    color="white",
                    fontsize=16,
                    fontweight="bold",
                    horizontalalignment="center",
                    verticalalignment="center",
                    path_effects=[
                        path_effects.withStroke(linewidth=3, foreground="#101820")
                    ],
                    zorder=12,
                )
                if detection.base_km is not None and detection.top_km is not None:
                    self.axes.annotate(
                        f"{detection.base_km:.1f}–{detection.top_km:.1f} km",
                        (detection.longitude, detection.latitude),
                        xytext=(0, -13),
                        textcoords="offset points",
                        color="white",
                        fontsize=7,
                        horizontalalignment="center",
                        path_effects=[
                            path_effects.withStroke(linewidth=2, foreground="#101820")
                        ],
                        zorder=12,
                    )

        if not outlook_mode and self.lightning_strikes:
            strikes = tuple(self.lightning_strikes.values())
            self.axes.scatter(
                [strike.longitude for strike in strikes],
                [strike.latitude for strike in strikes],
                marker="+",
                s=64,
                color="#ffe45c",
                linewidths=1.7,
                label="Blitzortung lightning (last 15 min)",
                zorder=7,
            )

        if self.location is not None and self.map_mode != STORMANALYZER_MAP:
            self.axes.scatter(
                [self.location.longitude],
                [self.location.latitude],
                marker="o",
                s=260,
                color="#1687ff",
                alpha=0.24,
                linewidths=0,
                zorder=6,
            )
            self.axes.scatter(
                [self.location.longitude],
                [self.location.latitude],
                marker="o",
                s=62,
                color="#1687ff",
                edgecolors="white",
                linewidths=1.5,
                zorder=7,
                label="Your location",
            )

        if self._weather_location is not None and not outlook_mode:
            longitude, latitude = self._weather_location
            self.axes.scatter(
                [longitude],
                [latitude],
                marker="D",
                s=50,
                color="#50e3ff",
                edgecolors="#101820",
                linewidths=0.8,
                label="Weather point",
                zorder=8,
            )

        if not outlook_mode and (
            self.location is not None
            or self._weather_location is not None
            or self.lightning_strikes
            or motion_legend_drawn
            or (
                self.radar_product == VELOCITY_RADAR
                and self.velocity_radar_site is not None
            )
        ):
            self.axes.legend(
                loc="upper right",
                facecolor=PANEL,
                edgecolor="#52636c",
                labelcolor=FOREGROUND,
            )

        attribution = "" if outlook_mode else ESRI_ATTRIBUTION
        if self.map_mode == DWD_WARNING_MAP:
            attribution = f"Warning data © DWD · {ESRI_ATTRIBUTION}"
        if not outlook_mode and self.lightning_strikes:
            attribution += " · Lightning data © Blitzortung.org / LightningMaps.org"
        if attribution:
            self.axes.text(
                0.01,
                0.01,
                attribution,
                transform=self.axes.transAxes,
                fontsize=6.5,
                color="white",
                zorder=8,
                bbox={"facecolor": "#101820", "alpha": 0.75, "edgecolor": "none", "pad": 2},
            )
        title = {
            LIVE_MAP: "",
            DWD_WARNING_MAP: "DWD official weather warnings",
            STORMANALYZER_MAP: "StormAnalyzer outlook map",
        }[self.map_mode]
        self.axes.set_title(
            title,
            color="#263238" if outlook_mode else FOREGROUND,
            pad=10,
        )
        self.figure.tight_layout()
        self.map_canvas.draw_idle()
        if not outlook_mode:
            self._schedule_basemap_refresh()
        if self.map_mode == DWD_WARNING_MAP:
            self._schedule_warning_refresh()

    def _draw_movement_track(
        self,
        points: list[tuple[float, float]],
        color: Any,
        linewidth: float,
        arrow: bool = True,
    ) -> None:
        for index, (start, end) in enumerate(zip(points, points[1:])):
            progress = (index + 1) / (len(points) - 1)
            self.axes.plot(
                [start[0], end[0]],
                [start[1], end[1]],
                color=color,
                linewidth=linewidth * (0.65 + 0.35 * progress),
                alpha=0.2 + 0.7 * progress,
                solid_capstyle="round",
                zorder=4,
            )
        if arrow and len(points) > 1:
            self.axes.annotate(
                "",
                xy=points[-1],
                xytext=points[-2],
                arrowprops={
                    "arrowstyle": "-|>",
                    "color": color,
                    "linewidth": linewidth,
                    "mutation_scale": 11,
                },
                zorder=5,
            )

    def _schedule_basemap_refresh(self) -> None:
        if self._closed:
            return
        widget = self.map_canvas.get_tk_widget()
        canvas_width = widget.winfo_width()
        canvas_height = widget.winfo_height()
        if canvas_width <= 1 or canvas_height <= 1:
            return
        width = min(max(canvas_width, 640), 1200)
        height = min(max(canvas_height, 480), 900)
        extent = (*self.axes.get_xlim(), *self.axes.get_ylim())
        key = (*extent, width, height)
        if key == self._basemap_requested_key:
            return
        self._basemap_requested_key = key
        if self._basemap_after_id is not None:
            self.root.after_cancel(self._basemap_after_id)
        self._basemap_after_id = self.root.after(
            150,
            lambda: self._start_basemap_refresh(key),
        )

    def _on_map_mode_change(self, *_args: Any) -> None:
        self.map_mode = self.map_mode_var.get()
        self.radar_product_combo.configure(
            state="readonly" if self.map_mode == LIVE_MAP else "disabled"
        )
        map_extent = self._map_base_extent()
        self._map_xlim = map_extent[:2]
        self._map_ylim = map_extent[2:]
        self._basemap_requested_key = None
        outlook_controls_state = "readonly" if self.map_mode == STORMANALYZER_MAP else "disabled"
        self.outlook_day_combo.configure(state=outlook_controls_state)
        self.outlook_metric_combo.configure(state=outlook_controls_state)
        self._update_outlook_load_button()
        if self.map_mode == STORMANALYZER_MAP:
            self.report_panel.configure(height=260)
            self.report_notebook.select(self.outlook_analysis_tab)
            if self._has_device_fix and self.location is not None:
                self._outlook_location_source = "device"
                self.outlook_location_status.set("Using current device location.")
                self.outlook_latitude_input.set(f"{self.location.latitude:.4f}")
                self.outlook_longitude_input.set(f"{self.location.longitude:.4f}")
                self._show_outlook_calculation(
                    self.location.latitude,
                    self.location.longitude,
                )
            elif self._outlook_location_source == "manual" and self._outlook_location is not None:
                longitude, latitude = self._outlook_location
                self._show_outlook_calculation(latitude, longitude)
            else:
                self._outlook_location_source = None
                self._clear_outlook_calculation(
                    "No device location set. Enter coordinates above to show a calculation."
                )
        else:
            self.report_panel.configure(height=150)
            if self.report_notebook.select() == str(self.outlook_analysis_tab):
                self.report_notebook.select(0)
        if self.map_mode != DWD_WARNING_MAP:
            self._warning_click_request_id += 1
            self._warning_requested_key = None
            self.warning_detail.set("")
            if self._warning_after_id is not None:
                self.root.after_cancel(self._warning_after_id)
                self._warning_after_id = None
        else:
            self.warning_detail.set("")
        self._draw_map()

    def _on_outlook_selection_change(self, *_args: Any) -> None:
        self._outlook_day = OUTLOOK_DAY_LABELS.index(self.outlook_day_var.get())
        self._outlook_metric = self.outlook_metric_var.get()
        if self._outlook_location is not None:
            self._show_outlook_calculation(
                self._outlook_location[1],
                self._outlook_location[0],
            )
        if self.map_mode == STORMANALYZER_MAP:
            self._draw_map()

    def _ensure_storm_outlook(self) -> None:
        if self._storm_outlook_loading:
            return
        self._storm_outlook_loading = True
        self._storm_outlook_error = None
        self._update_outlook_load_button()
        point_count = len(germany_grid_points(self.germany_outline))
        self.status.set(
            f"Loading the two-day StormAnalyzer outlook for {point_count} grid points; "
            "this may take several minutes."
        )
        self._draw_map()
        self.loop.create_task(self._load_storm_outlook())

    def _toggle_outlook_source(self) -> None:
        if self._storm_outlook_loading:
            return
        self._outlook_use_local_feed = not self._outlook_use_local_feed
        self._ensure_storm_outlook()

    async def _load_storm_outlook(self) -> None:
        try:
            outlook, issued_at = await self.loop.run_in_executor(
                self.executor,
                calculate_germany_outlook,
                self.germany_outline,
                None if self._outlook_use_local_feed else self.supabase,
            )
        except Exception as error:
            self._storm_outlook_loading = False
            self._storm_outlook_error = f"{type(error).__name__}: {error}"
            self._update_outlook_load_button()
            self.status.set(f"StormAnalyzer Germany outlook unavailable: {self._storm_outlook_error}")
            if self._outlook_location is not None:
                self.outlook_calculation_text.set(
                    f"StormAnalyzer outlook calculation unavailable: "
                    f"{self._storm_outlook_error}"
                )
            if self.map_mode == STORMANALYZER_MAP:
                self._draw_map()
            return
        self._storm_outlook = outlook
        self._storm_outlook_loaded_at = issued_at
        self._storm_outlook_loading = False
        self._storm_outlook_error = None
        self._update_outlook_load_button()
        self.status.set(f"StormAnalyzer Germany grid updated {issued_at:%H:%M} CET/CEST.")
        if self._outlook_location is not None:
            self._show_outlook_calculation(
                self._outlook_location[1],
                self._outlook_location[0],
            )
        if self.map_mode == STORMANALYZER_MAP:
            self._draw_map()

    def _update_outlook_load_button(self) -> None:
        if self._storm_outlook_loading:
            text = "Loading..."
        elif self._storm_outlook_error:
            text = "Retry load"
        elif self._storm_outlook_loaded_at is not None:
            text = "Refresh map"
        else:
            text = "Load map"

        state = (
            "normal"
            if self.map_mode == STORMANALYZER_MAP and not self._storm_outlook_loading
            else "disabled"
        )
        self.outlook_load_button.configure(text=text, state=state)
        source_text = "Use shared feed" if self._outlook_use_local_feed else "Use local feed"
        self.outlook_source_button.configure(text=source_text, state=state)

    def _schedule_warning_refresh(self) -> None:
        if self._closed or self.map_mode != DWD_WARNING_MAP:
            return
        widget = self.map_canvas.get_tk_widget()
        canvas_width = widget.winfo_width()
        canvas_height = widget.winfo_height()
        if canvas_width <= 1 or canvas_height <= 1:
            return
        width = min(max(canvas_width, 640), 1200)
        height = min(max(canvas_height, 480), 900)
        extent = (*self.axes.get_xlim(), *self.axes.get_ylim())
        key = (*extent, width, height)
        if key == self._warning_requested_key:
            return
        self._warning_requested_key = key
        if self._warning_after_id is not None:
            self.root.after_cancel(self._warning_after_id)
        self._warning_after_id = self.root.after(
            150,
            lambda: self._start_warning_refresh(key),
        )

    def radar_playback_button(self) -> None:
        if self._radar_playback_after_id is not None:
            self.root.after_cancel(self._radar_playback_after_id)
            self._radar_playback_after_id = None
            self.radar_playback_button.configure(text="▶ Play radar")
        else:
            self.radar_playback_button.configure(text="⏸ Pause radar")
            self._radar_playback_after_id = self.root.after(
                1000,
                self._advance_radar_frame,
            )
        playback_interval = 1000 if self._radar_playback_after_id is not None else 0
        if playback_interval > 0:
            self._radar_playback_after_id = self.root.after(
                playback_interval,
                self._advance_radar_frame,
            )

    def file_report_button(self) -> None:
        cell = self.selected_cell
        if not isinstance(cell, StormCell):
            messagebox.showinfo(
                "Select a storm cell",
                "Select a KONRAD3D storm cell before filing a report.",
                parent=self.root,
            )
            return
        if self.auth_user is None or self.auth_user.username is None:
            messagebox.showinfo(
                "Set a reporter username",
                "Set your username once to file a community storm report.",
                parent=self.root,
            )
            self._open_auth_dialog()
            return

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Report · {cell.identifier}")
        dialog.transient(self.root)
        dialog.geometry("440x300")
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=(
                f"Storm cell {cell.identifier} · "
                f"{cell.latitude:.3f}° N, {cell.longitude:.3f}° E"
            ),
            wraplength=410,
        ).pack(anchor="w", pady=(0, 8))
        report_text = tk.Text(frame, wrap="word", height=8)
        report_text.pack(fill="both", expand=True)
        status_text = tk.StringVar(value="Describe what you observed (up to 2000 characters).")
        ttk.Label(frame, textvariable=status_text, wraplength=410).pack(
            anchor="w",
            pady=(6, 4),
        )
        submit_button = ttk.Button(
            frame,
            text="Submit report",
            command=lambda: self._submit_report(
                cell,
                report_text.get("1.0", "end-1c"),
                dialog,
                submit_button,
                status_text,
            ),
        )
        submit_button.pack(anchor="e", pady=(4, 0))

    def _submit_report(
        self,
        cell: StormCell,
        report: str,
        dialog: tk.Toplevel,
        submit_button: ttk.Button,
        status_text: tk.StringVar,
    ) -> None:
        report = report.strip()
        if not report or len(report) > 2000:
            status_text.set("Enter a report between 1 and 2000 characters.")
            return
        if self.supabase is None:
            status_text.set("Supabase is required to submit a report.")
            return
        if self.auth_user is None or self.auth_user.username is None:
            status_text.set("Set your report username first.")
            self._open_auth_dialog()
            return
        submit_button.configure(state="disabled")
        dialog.protocol("WM_DELETE_WINDOW", lambda: None)
        status_text.set("Submitting report…")
        self.loop.create_task(
            self._submit_report_task(
                cell,
                report,
                dialog,
                submit_button,
                status_text,
            )
        )

    async def _submit_report_task(
        self,
        cell: StormCell,
        report: str,
        dialog: tk.Toplevel,
        submit_button: ttk.Button,
        status_text: tk.StringVar,
    ) -> None:
        client = self.supabase
        if client is None:
            return
        try:
            await self.loop.run_in_executor(
                self.executor,
                client.submit_report,
                cell,
                report,
            )
        except Exception as error:
            status_text.set(f"Report submission failed: {type(error).__name__}: {error}")
            self.status.set(f"Report submission failed: {type(error).__name__}: {error}")
            self._sync_auth_state()
            submit_button.configure(state="normal")
            dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
            return

        self.status.set("Community storm report submitted.")
        if dialog.winfo_exists():
            dialog.grab_release()
            dialog.destroy()
        try:
            self.community_reports = await self.loop.run_in_executor(
                self.executor,
                client.recent_reports,
            )
        except Exception as error:
            self.status.set(
                "Report submitted, but community reports could not refresh: "
                f"{type(error).__name__}: {error}"
            )
        self._render_community_reports()

    def _start_warning_refresh(
        self,
        key: tuple[float, float, float, float, int, int],
    ) -> None:
        self._warning_after_id = None
        if (
            self._closed
            or self.map_mode != DWD_WARNING_MAP
            or key != self._warning_requested_key
        ):
            return
        self.loop.create_task(
            self._load_warning_overlay(key, key[:4], key[4], key[5])
        )

    async def _load_warning_overlay(
        self,
        key: tuple[float, float, float, float, int, int],
        extent: tuple[float, float, float, float],
        width: int,
        height: int,
    ) -> None:
        try:
            image, image_extent = await self.loop.run_in_executor(
                self.executor,
                fetch_dwd_warning_overlay,
                extent,
                width,
                height,
            )
        except Exception as error:
            if not self._closed and key == self._warning_requested_key:
                self.status.set(
                    f"DWD warnings unavailable: {type(error).__name__}: {error}"
                )
            return
        if (
            self._closed
            or self.map_mode != DWD_WARNING_MAP
            or key != self._warning_requested_key
        ):
            return
        self._warning_image = image
        self._warning_extent = image_extent
        self._draw_map()

    def _start_basemap_refresh(
        self,
        key: tuple[float, float, float, float, int, int],
    ) -> None:
        self._basemap_after_id = None
        if self._closed or key != self._basemap_requested_key:
            return
        extent = key[:4]
        width, height = key[4:]
        self.loop.create_task(self._load_esri_imagery(key, extent, width, height))

    async def _load_esri_imagery(
        self,
        key: tuple[float, float, float, float, int, int],
        extent: tuple[float, float, float, float],
        width: int,
        height: int,
    ) -> None:
        try:
            image, image_extent = await self.loop.run_in_executor(
                self.executor,
                fetch_esri_imagery,
                extent,
                width,
                height,
            )
        except Exception as error:
            if not self._closed and key == self._basemap_requested_key:
                self.status.set(
                    f"Esri World Imagery unavailable: {type(error).__name__}: {error}"
                )
            return
        if self._closed or key != self._basemap_requested_key:
            return
        self._basemap_image = image
        self._basemap_extent = image_extent
        if self._basemap_artist is not None:
            self._basemap_artist.set_data(image)
            self._basemap_artist.set_extent(image_extent)
            self.map_canvas.draw_idle()
        else:
            self._draw_map()

    def _on_map_resize(self, _event: Any) -> None:
        self._schedule_basemap_refresh()
        if self.map_mode == DWD_WARNING_MAP:
            self._schedule_warning_refresh()

    def _change_zoom(
        self,
        scale_factor: float,
        anchor_x: float | None = None,
        anchor_y: float | None = None,
    ) -> None:
        current_xlim = self.axes.get_xlim()
        current_ylim = self.axes.get_ylim()
        if anchor_x is None:
            anchor_x = (current_xlim[0] + current_xlim[1]) / 2
        if anchor_y is None:
            anchor_y = (current_ylim[0] + current_ylim[1]) / 2
        map_extent = self._map_base_extent()
        self._map_xlim = zoom_axis_limits(
            current_xlim,
            map_extent[:2],
            anchor_x,
            scale_factor,
        )
        self._map_ylim = zoom_axis_limits(
            current_ylim,
            map_extent[2:],
            anchor_y,
            scale_factor,
        )
        self.axes.set_xlim(self._map_xlim)
        self.axes.set_ylim(self._map_ylim)
        self.map_canvas.draw_idle()
        self._schedule_basemap_refresh()
        if self.map_mode == DWD_WARNING_MAP:
            self._schedule_warning_refresh()

    def _on_map_scroll(self, event: Any) -> None:
        if event.inaxes is not self.axes or event.xdata is None or event.ydata is None:
            return
        now = time_module.monotonic()
        if now - self._last_map_scroll_at < 0.12:
            return
        step = getattr(event, "step", 0)
        if step:
            scale_factor = 1 / 1.2 if step > 0 else 1.2
        elif event.button == "up":
            scale_factor = 1 / 1.2
        elif event.button == "down":
            scale_factor = 1.2
        else:
            return
        self._last_map_scroll_at = now
        self._change_zoom(scale_factor, event.xdata, event.ydata)

    def _reset_map_zoom(self) -> None:
        map_extent = self._map_base_extent()
        self._map_xlim = map_extent[:2]
        self._map_ylim = map_extent[2:]
        self.axes.set_xlim(self._map_xlim)
        self.axes.set_ylim(self._map_ylim)
        self.map_canvas.draw_idle()
        self._schedule_basemap_refresh()
        if self.map_mode == DWD_WARNING_MAP:
            self._schedule_warning_refresh()

    def _map_base_extent(self) -> tuple[float, float, float, float]:
        if self.map_mode == STORMANALYZER_MAP:
            return OUTLOOK_EXTENT
        return GERMANY_EXTENT

    def _select_cell(self, index: int) -> None:
        if not 0 <= index < len(self._visible_cells):
            return
        self.selected_cell = self._visible_cells[index]
        cell = self.selected_cell
        details = [
            f"{cell.identifier}",
            f"{cell.latitude:.3f}° N, {cell.longitude:.3f}° E",
        ]
        if isinstance(cell, RainCell):
            details.extend(
                (
                    "Rain echo (20–30 dBZ; convection not independently classified)",
                    f"Maximum reflectivity: {cell.max_reflectivity_dbz:.1f} dBZ",
                    f"Echo area: {cell.area_pixels} radar pixels",
                )
            )
        else:
            if cell.reflectivity_dbz is not None:
                details.append(f"Reflectivity: {cell.reflectivity_dbz:.1f} dBZ")
            if cell.severity:
                details.append(f"Severity: {cell.severity}")
            track_points = max(
                len(cell.track),
                len(self.track_history.get(cell.identifier, [])),
            )
            details.append(
                f"Track points: {track_points}"
            )
        self.cell_detail.set("\n".join(details))
        self.cell_list.selection_clear(0, tk.END)
        self.cell_list.selection_set(index)
        self._refresh_auth_controls()
        self._draw_map()

    def _on_cell_list_select(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is self.cell_list:
            selected = self.cell_list.curselection()
            if selected:
                self._select_cell(selected[0])

    def _render_click_reports(self, *_args: Any) -> None:
        if not hasattr(self, "report_text"):
            return
        reports = [
            report
            for report in (self.weather_detail.get(), self.warning_detail.get())
            if report.strip()
        ]
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", tk.END)
        if reports:
            self.report_text.insert("1.0", "\n\n".join(reports))
            self.report_text.see(tk.END)
        self.report_text.configure(state="disabled")

    def _on_map_click(self, event: Any) -> None:
        if (
            event.button not in (1, "left")
            or event.inaxes is not self.axes
            or event.xdata is None
            or event.ydata is None
        ):
            return
        latitude = float(event.ydata)
        longitude = float(event.xdata)
        if self.map_mode == LIVE_MAP and self.radar_product == VELOCITY_RADAR:
            self._select_velocity_radar_site(
                nearest_velocity_radar_site(latitude, longitude)
            )
        self._weather_location = (longitude, latitude)
        self.weather_detail.set("Point nowcast is disabled to conserve API limits.")
        if self.map_mode == DWD_WARNING_MAP:
            self._warning_click_request_id += 1
            warning_request_id = self._warning_click_request_id
            self.warning_detail.set("Checking DWD warnings at the clicked coordinate…")
            self.loop.create_task(
                self._load_point_warning(warning_request_id, latitude, longitude)
            )
        self._draw_map()

    async def _load_point_nowcast(
        self,
        request_id: int,
        latitude: float,
        longitude: float,
    ) -> None:
        try:
            model, payload = await self.loop.run_in_executor(
                self.executor,
                fetch_openmeteo_nowcast,
                latitude,
                longitude,
            )
        except Exception as error:
            if not self._closed and request_id == self._weather_request_id:
                self.weather_detail.set(
                    f"Open-Meteo nowcast unavailable: {type(error).__name__}: {error}"
                )
            return
        if self._closed or request_id != self._weather_request_id:
            return
        self.weather_detail.set(
            self._format_nowcast(model, latitude, longitude, payload)
        )

    @staticmethod
    def _format_nowcast(
        model: str,
        latitude: float,
        longitude: float,
        payload: dict[str, Any],
    ) -> str:
        current = payload.get("current", {})
        units = payload.get("current_units", {})
        hourly = payload.get("hourly", {})
        hourly_units = payload.get("hourly_units", {})
        times = hourly.get("time", [])
        if not current and times:
            labels = (
                ("cape", "CAPE"), ("convective_inhibition", "CIN"),
                ("temperature_2m", "Temp"), ("dew_point_2m", "Dewpoint"),
                ("relative_humidity_2m", "Humidity"), ("precipitation", "Precip"),
                ("wind_speed_10m", "10 m wind"),
                ("wind_direction_10m", "10 m direction"),
                ("temperature_850hPa", "850 hPa temp"),
                ("temperature_700hPa", "700 hPa temp"),
                ("temperature_500hPa", "500 hPa temp"),
                ("wind_speed_925hPa", "925 hPa wind"),
                ("wind_direction_925hPa", "925 hPa direction"),
                ("wind_speed_850hPa", "850 hPa wind"),
                ("wind_direction_850hPa", "850 hPa direction"),
                ("wind_speed_700hPa", "700 hPa wind"),
                ("wind_direction_700hPa", "700 hPa direction"),
                ("wind_speed_500hPa", "500 hPa wind"),
                ("wind_direction_500hPa", "500 hPa direction"),
            )
            current = {
                key: values[0]
                for key, _label in labels
                if isinstance((values := hourly.get(key)), list) and values and values[0] is not None
            }
            units = hourly_units
            payload = {**payload, "nowcast_labels": {key: label for key, label in labels}}
        labels = (
            ("cape", "CAPE"),
            ("convective_inhibition", "CIN"),
            ("temperature_2m", "Temp"),
            ("dew_point_2m", "Dewpoint"),
            ("relative_humidity_2m", "Humidity"),
            ("precipitation", "Precip"),
            ("wind_speed_10m", "10 m wind"),
            ("wind_direction_10m", "10 m direction"),
            ("temperature_850hPa", "850 hPa temp"),
            ("temperature_700hPa", "700 hPa temp"),
            ("temperature_500hPa", "500 hPa temp"),
            ("wind_speed_925hPa", "925 hPa wind"),
            ("wind_direction_925hPa", "925 hPa direction"),
            ("wind_speed_850hPa", "850 hPa wind"),
            ("wind_direction_850hPa", "850 hPa direction"),
            ("wind_speed_700hPa", "700 hPa wind"),
            ("wind_direction_700hPa", "700 hPa direction"),
            ("wind_speed_500hPa", "500 hPa wind"),
            ("wind_direction_500hPa", "500 hPa direction"),
        )
        values = []
        for key, label in labels:
            value = current.get(key)
            if value is not None:
                display_label = payload.get("nowcast_labels", {}).get(key, label)
                values.append(f"{display_label} {value:g}{units.get(key, '')}")

        next_hours = []
        for index in range(min(4, len(times))):
            time = str(times[index])
            temperatures = hourly.get("temperature_2m", [])
            precipitation_values = hourly.get("precipitation", [])
            temperature = temperatures[index] if index < len(temperatures) else None
            precipitation = (
                precipitation_values[index]
                if index < len(precipitation_values)
                else None
            )
            if temperature is None and precipitation is None:
                continue
            parts = []
            if temperature is not None:
                parts.append(
                    f"{temperature:g}{hourly_units.get('temperature_2m', '')}"
                )
            if precipitation is not None:
                parts.append(
                    f"{precipitation:g}{hourly_units.get('precipitation', '')} precip"
                )
            next_hours.append(f"{time[-5:]} {' / '.join(parts)}")

        minutely_summary = ""
        minutely = payload.get("minutely_15", {})
        minutely_times = minutely.get("time", [])
        minutely_units = payload.get("minutely_15_units", {})
        if minutely_times:
            short_term = []
            for index in range(min(4, len(minutely_times))):
                precip_values = minutely.get("precipitation", [])
                cape_values = minutely.get("cape", [])
                lpi_values = minutely.get("lightning_potential", [])
                interval = []
                if index < len(precip_values) and precip_values[index] is not None:
                    interval.append(
                        f"{precip_values[index]:g}"
                        f"{minutely_units.get('precipitation', '')}"
                    )
                if index < len(cape_values) and cape_values[index] is not None:
                    interval.append(f"CAPE {cape_values[index]:g}")
                if index < len(lpi_values) and lpi_values[index] is not None:
                    interval.append(f"LPI {lpi_values[index]:g}")
                if interval:
                    short_term.append(
                        f"{str(minutely_times[index])[-5:]} {' / '.join(interval)}"
                    )
            if short_term:
                minutely_summary = " | 15-min: " + ", ".join(short_term)

        if minutely_summary:
            values.append(minutely_summary.lstrip(" |"))

        model_name = (
            "ICON-D2"
            if model == "icon_d2"
            else "ECMWF IFS 0.25° (ICON-D2 unavailable)"
        )
        observed_at = current.get("time", str(times[0]) if times else "now")
        summary = " · ".join(values) if values else "No current parameters returned"
        if next_hours:
            summary += " · Next: " + ", ".join(next_hours)
        return (
            f"{model_name} nowcast · {latitude:.4f}° N, {longitude:.4f}° E · "
            f"{observed_at} · {summary}"
        )

    async def _load_point_warning(
        self,
        request_id: int,
        latitude: float,
        longitude: float,
    ) -> None:
        try:
            features = await self.loop.run_in_executor(
                self.executor,
                fetch_dwd_warning_at,
                latitude,
                longitude,
            )
        except Exception as error:
            if (
                not self._closed
                and self.map_mode == DWD_WARNING_MAP
                and request_id == self._warning_click_request_id
            ):
                self.warning_detail.set(
                    f"DWD warning details unavailable: {type(error).__name__}: {error}"
                )
            return
        if (
            self._closed
            or self.map_mode != DWD_WARNING_MAP
            or request_id != self._warning_click_request_id
        ):
            return
        if not features:
            self.warning_detail.set(
                f"No active DWD municipality warning at {latitude:.4f}° N, "
                f"{longitude:.4f}° E."
            )
            return
        details: list[str] = []
        for feature in features[:4]:
            fields = (
                ("EVENT", "Warning"),
                ("HEADLINE", "Headline"),
                ("DESCRIPTION", "Details"),
                ("INSTRUCTION", "Advice"),
                ("SEVERITY", "Severity"),
                ("ONSET", "From"),
                ("EXPIRES", "Until"),
                ("AREADESC", "Area"),
            )
            lines = [
                f"{label}: {feature[key]}"
                for key, label in fields
                if feature.get(key)
            ]
            if lines:
                details.append(" | ".join(lines))
        self.warning_detail.set(
            "DWD warnings: "
            + (
                "  ||  ".join(details)
                if details
                else "Warning area found; no details returned."
            )
        )

    def _on_map_pick(self, event: Any) -> None:
        if not event.ind:
            return
        if event.artist is self._cell_artist:
            self._select_cell(int(event.ind[0]))
        elif event.artist is self._rain_artist:
            self._select_cell(len(self.storm_update.cells) + int(event.ind[0]))

    def set_manual_location(self) -> None:
        try:
            latitude = float(self.latitude_input.get())
            longitude = float(self.longitude_input.get())
        except ValueError:
            messagebox.showerror(
                "Invalid coordinates",
                "Enter a numeric latitude and longitude.",
                parent=self.root,
            )
            return
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            messagebox.showerror(
                "Invalid coordinates",
                "Latitude must be between -90 and 90; longitude between -180 and 180.",
                parent=self.root,
            )
            return
        self.location = LocationFix(latitude, longitude, 0)
        self._has_device_fix = False
        self.location_status.set(f"Manual location: {latitude:.4f}, {longitude:.4f}")
        self._center_on_location(self.location, initial=True)
        self._draw_map()

    def enable_location(self) -> None:
        if self._tracking_task is not None and not self._tracking_task.done():
            return
        accepted = messagebox.askyesno(
            "Enable device location?",
            "StormChase will ask Windows for device-location permission and update "
            "your position as Windows reports changes. Coordinates stay on this device and "
            "are not sent to DWD. Enable tracking?",
            parent=self.root,
        )
        if accepted:
            self.location_status.set("Requesting Windows location permission…")
            self._tracking_task = self.loop.create_task(self._start_location_tracking())

    async def _start_location_tracking(self) -> None:
        try:
            permitted = await request_location_access()
            if not permitted:
                raise PermissionError(
                    "Windows did not grant location access. Check Windows Location "
                    "privacy settings or enter coordinates manually."
                )
            self._geolocator = create_geolocator()
            self._subscribe_to_location_updates()
            self.location_status.set("Waiting for device location…")
            await self._poll_device_location()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.location_status.set(f"Location unavailable: {error}")
            self.status.set(f"Location error: {type(error).__name__}: {error}")
        finally:
            self._unsubscribe_from_location_updates()
            self._geolocator = None
            if self._tracking_task is asyncio.current_task():
                self._tracking_task = None

    def _subscribe_to_location_updates(self) -> None:
        if self._geolocator is None:
            raise RuntimeError("The Windows location provider was not initialized.")

        def on_position_changed(_sender: Any, event_args: Any) -> None:
            try:
                location = location_from_geoposition(event_args.position)
            except Exception as error:
                self.loop.call_soon_threadsafe(self._set_location_error, error)
            else:
                self.loop.call_soon_threadsafe(self._apply_device_location, location)

        self._location_changed_handler = on_position_changed
        self._location_event_token = self._geolocator.add_position_changed(
            on_position_changed
        )

    def _unsubscribe_from_location_updates(self) -> None:
        if self._geolocator is not None and self._location_event_token is not None:
            self._geolocator.remove_position_changed(self._location_event_token)
        self._location_event_token = None
        self._location_changed_handler = None

    def _set_location_error(self, error: Exception) -> None:
        if self._closed:
            return
        self.location_status.set(f"Location unavailable: {error}")

    async def _poll_device_location(self) -> None:
        if self._geolocator is None:
            raise RuntimeError("The Windows location provider was not initialized.")
        self._apply_device_location(await read_device_location(self._geolocator))

    def _apply_device_location(self, location: LocationFix) -> None:
        if self._closed:
            return
        initial = not self._has_device_fix
        self._has_device_fix = True
        self.location = location
        self.location_status.set(
            f"Tracking · ±{location.accuracy_meters:.0f} m · "
            f"{location.latitude:.4f}, {location.longitude:.4f}"
        )
        if (
            self.map_mode == STORMANALYZER_MAP
            and self._outlook_location_source in (None, "device")
        ):
            self._outlook_location_source = "device"
            self.outlook_location_status.set("Using current device location.")
            self.outlook_latitude_input.set(f"{location.latitude:.4f}")
            self.outlook_longitude_input.set(f"{location.longitude:.4f}")
            self._show_outlook_calculation(location.latitude, location.longitude)
        self._center_on_location(location, initial=initial)
        self._draw_map()

    def _center_on_location(self, location: LocationFix, initial: bool) -> None:
        center_x = location.longitude
        center_y = location.latitude
        if initial:
            half_width = 1.5
            half_height = 1.1
        else:
            half_width = (self._map_xlim[1] - self._map_xlim[0]) / 2
            half_height = (self._map_ylim[1] - self._map_ylim[0]) / 2
        self._map_xlim = (center_x - half_width, center_x + half_width)
        self._map_ylim = (center_y - half_height, center_y + half_height)

    def stop_location_tracking(self) -> None:
        if self._tracking_task is not None:
            self._tracking_task.cancel()
            self._tracking_task = None
        self._unsubscribe_from_location_updates()
        self._geolocator = None
        self.location_status.set("Location tracking stopped")
        self.location = None
        self._has_device_fix = False
        if self._outlook_location_source == "device":
            self._outlook_location_source = None
            self.outlook_location_status.set(
                "Device location stopped. Enter coordinates to show a calculation."
            )
            self._clear_outlook_calculation(
                "No device location set. Enter coordinates above to show a calculation."
            )
        self._draw_map()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._basemap_after_id is not None:
            self.root.after_cancel(self._basemap_after_id)
            self._basemap_after_id = None
        if self._warning_after_id is not None:
            self.root.after_cancel(self._warning_after_id)
            self._warning_after_id = None
        self._unsubscribe_from_location_updates()
        for task in asyncio.all_tasks(self.loop):
            task.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        self.loop.close()
        self.root.destroy()
