
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
from matplotlib.patches import Ellipse
import matplotlib.patheffects as path_effects
from matplotlib.tri import Triangulation, UniformTriRefiner
import numpy as np
from PIL import Image, ImageDraw
import customtkinter as ctk

from case_analysis import CaseAnalysisStore
from warning_system import (
    WARNING_CATEGORY_LABELS,
    WARNING_COLORS,
    WARNING_LEVELS,
    beam_height_contour_range_km,
    validate_warning_payload,
)
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
    REFLECTIVITY_PRODUCT,
    ROTATION_STORM_SUPPORT_MIN_DBZ,
    RainCellTracker,
    RainCell,
    RadarFrame,
    RotationDetection,
    VelocityRadarSite,
    VELOCITY_PRODUCT,
    detect_rain_cells,
    fetch_latest_radar,
    fetch_latest_mesocyclone_detections,
    fetch_latest_velocity_radar,
    geographic_distance_km,
    maximum_reflectivity_near,
    nearest_velocity_radar_site,
    project_rain_cell_direction,
    radar_playback,
)
from supabase_client import AuthUser, SupabaseClient, SupabaseConfig, SupabaseError
from tracking import StormCell, StormUpdate, fetch_latest_storm_cells, project_cell_motion
from calculations import (
    NO_LFC_SENTINEL_M,
    calculate_best_day_window,
    calculate_manual_storm,
    nearest_outlook_entry,
)
from stormanalyzer import analyze_general_weather, summarize_daily_outlook_environment


BACKGROUND = "#11191e"
PANEL = "#1a252b"
FOREGROUND = "#e8edef"
MUTED = "#a4b0b5"
ACCENT = "#73b6a5"
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
GENERAL_WEATHER_MAP_METRIC = "Precip chance"
GENERAL_WEATHER_MAP_SCALES = {
    "Precip chance": ("precipitation_probability_max_percent", 0.0, 100.0, "%"),
    "Total precip": ("precipitation_sum_mm", 0.0, 30.0, "mm"),
    "Rainfall": ("rain_sum_mm", 0.0, 30.0, "mm"),
    "High temp": ("temperature_max_c", -20.0, 40.0, "°C"),
    "Low temp": ("temperature_min_c", -30.0, 30.0, "°C"),
    "Wind max": ("wind_speed_max_kmh", 0.0, 100.0, "km/h"),
    "UV index": ("uv_index_max", 0.0, 12.0, ""),
}


class AppButton(ctk.CTkButton):
    """Shared muted button styling for the app's CustomTkinter controls."""

    def __init__(self, master: Any, **kwargs: Any) -> None:
        kwargs.setdefault("fg_color", "#293941")
        kwargs.setdefault("hover_color", "#3a5057")
        kwargs.setdefault("text_color", FOREGROUND)
        kwargs.setdefault("corner_radius", 8)
        kwargs.setdefault("height", 34)
        kwargs.setdefault("font", ("Segoe UI", 10))
        super().__init__(master, **kwargs)


class AppComboBox(ctk.CTkComboBox):
    """Muted, rounded dropdown styling shared by the map controls."""

    def __init__(self, master: Any, **kwargs: Any) -> None:
        kwargs.setdefault("fg_color", "#202d33")
        kwargs.setdefault("border_color", "#35454b")
        kwargs.setdefault("button_color", "#293941")
        kwargs.setdefault("button_hover_color", "#3a5057")
        kwargs.setdefault("dropdown_fg_color", "#1a252b")
        kwargs.setdefault("dropdown_hover_color", "#2b3b41")
        kwargs.setdefault("text_color", FOREGROUND)
        kwargs.setdefault("text_color_disabled", MUTED)
        kwargs.setdefault("corner_radius", 8)
        kwargs.setdefault("height", 32)
        kwargs.setdefault("font", ("Segoe UI", 10))
        kwargs.setdefault("dropdown_font", ("Segoe UI", 10))
        super().__init__(master, **kwargs)


def _terminal_progress(area: str, message: str) -> None:
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{area}] {message}", flush=True)


def _fill_grid_gaps_from_neighbors(
    target_points: list[tuple[float, float]],
    source_points: list[tuple[float, float]],
    source_values: list[float],
    neighbor_count: int = 4,
) -> np.ndarray:
    if not source_points or len(source_points) != len(source_values):
        raise ValueError("At least one matching source point and value is required.")

    targets = np.asarray(target_points, dtype=float)
    sources = np.asarray(source_points, dtype=float)
    values = np.asarray(source_values, dtype=float)
    latitude_delta_km = (targets[:, None, 0] - sources[None, :, 0]) * 111.0
    longitude_scale = np.cos(np.radians(targets[:, 0]))[:, None]
    longitude_delta_km = (
        (targets[:, None, 1] - sources[None, :, 1])
        * 111.0
        * longitude_scale
    )
    distances = np.hypot(latitude_delta_km, longitude_delta_km)
    count = min(max(int(neighbor_count), 1), len(sources))
    nearest = np.argpartition(distances, count - 1, axis=1)[:, :count]
    nearest_distances = np.take_along_axis(distances, nearest, axis=1)
    nearest_order = np.argsort(nearest_distances, axis=1)
    nearest = np.take_along_axis(nearest, nearest_order, axis=1)
    nearest_distances = np.take_along_axis(nearest_distances, nearest_order, axis=1)
    nearest_values = values[nearest]
    exact_matches = nearest_distances[:, 0] <= 1e-6
    weights = 1.0 / np.maximum(nearest_distances, 1e-6) ** 2
    interpolated = np.sum(nearest_values * weights, axis=1) / np.sum(weights, axis=1)
    interpolated[exact_matches] = nearest_values[exact_matches, 0]
    return interpolated


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
    use_shared_outlook: bool = True,
    general_weather_results: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], datetime]:
    from weather_api import (
        OPTIONAL_STORMANALYZER_HOURLY_FIELDS,
        STORMANALYZER_HOURLY_FIELDS,
        _validate_hourly,
        get_germany_grid_weather,
    )

    points = germany_grid_points(outlines)
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    calculation_started = time_module.perf_counter()
    _terminal_progress(
        "Outlook",
        f"Starting a two-day calculation for {len(points)} locations.",
    )
    cached_outlook = None
    general_weather_grid = None
    if supabase is not None:
        if use_shared_outlook:
            _terminal_progress("Outlook", "Loading the shared ICON-D2 grid from Supabase.")
            cached_outlook = supabase.daily_outlook_grid(now.date())
            _terminal_progress(
                "Outlook",
                "Shared ICON-D2 grid loaded."
                if cached_outlook is not None
                else "No shared ICON-D2 grid was available.",
            )
        _terminal_progress("Outlook", "Loading published general-weather data from Supabase.")
        try:
            general_weather_grid = supabase.daily_general_weather_grid(now.date())
            _terminal_progress(
                "Outlook",
                f"Loaded {len(general_weather_grid)} general-weather locations."
                if general_weather_grid is not None
                else "No published general-weather grid was available.",
            )
        except SupabaseError as error:
            _terminal_progress("Outlook", f"WARNING: General-weather data unavailable: {error}")

    if cached_outlook is None:
        _terminal_progress(
            "Outlook",
            f"Fetching ICON-D2 environment locally for {len(points)} locations.",
        )
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
    _terminal_progress(
        "Outlook",
        f"Validated {len(weather)} environment profiles; calculating daily storm and weather scores.",
    )

    outlook = []
    for point_index, ((latitude, longitude), location) in enumerate(zip(points, weather)):
        if point_index % 100 == 0:
            _terminal_progress(
                "Outlook",
                f"Analyzing location {point_index + 1}/{len(points)}.",
            )
        best_by_day: dict[int, dict[str, Any]] = {}
        hourly = location.get("icon_d2")
        for day in range(2):
            result = calculate_best_day_window(
                hourly,
                now.date() + timedelta(days=day),
                calculation_time,
            )
            local_weather = None
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
                    try:
                        local_environment = summarize_daily_outlook_environment(
                            hourly,
                            target_date,
                        )
                    except ValueError as error:
                        print(
                            "WARNING: Could not summarize ICON-D2 environment "
                            f"for local weather analysis: {error}",
                            flush=True,
                        )
                        local_environment = {}
                    local_weather = dict(general_day)
                    local_weather["environment"] = local_environment
                    local_weather["analysis"] = analyze_general_weather(
                        local_environment,
                        local_weather,
                    )
                    local_weather["weather_description"] = (
                        local_weather["analysis"]["condition"]
                    )
                    if general_weather_results is not None:
                        general_weather_results.append(
                            {
                                "day": day,
                                "latitude": latitude,
                                "longitude": longitude,
                                "general_weather": local_weather,
                            }
                        )
            if result is not None:
                outlook_entry = {
                    **result,
                    "latitude": latitude,
                    "longitude": longitude,
                }
                if local_weather is not None:
                    outlook_entry["general_weather"] = local_weather
                best_by_day[day] = outlook_entry
        outlook.extend({**entry, "day": day} for day, entry in best_by_day.items())
    if general_weather_results is not None:
        _terminal_progress(
            "Outlook",
            f"Collected {len(general_weather_results)} general-weather map points independently "
            "of storm-analysis results.",
        )
    _terminal_progress(
        "Outlook",
        f"Analysis complete: {len(outlook)} day/location results in "
        f"{time_module.perf_counter() - calculation_started:.1f}s.",
    )
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


    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        _terminal_progress("App", "Starting StormChase and initializing local feeds.")
        self.root.title("StormChase | Live Storm Radar")
        self.root.geometry("1280x820")
        self.root.minsize(1000, 680)
        self.root.configure(fg_color=BACKGROUND)

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
        self.active_community_warnings: tuple[dict[str, Any], ...] = ()
        self._is_stormchase_admin = False
        self._warning_polygon_draft: list[list[float]] = []
        self._warning_drawing_enabled = False
        self._admin_warning_rows: tuple[dict[str, Any], ...] = ()
        self._admin_report_rows: tuple[dict[str, Any], ...] = ()
        self.case_analysis = CaseAnalysisStore()
        self._geolocator: Any | None = None
        self._location_event_token: Any | None = None
        self._location_changed_handler: Any | None = None
        self._has_device_fix = False
        self.radar_frame: RadarFrame | None = None
        self._radar_playback_frames: tuple[RadarFrame, ...] = ()
        self._radar_playback_index: int | None = None
        self._radar_playback_after_id: str | None = None
        self._radar_playback_loading = False
        self._radar_playback_paused = False
        self._radar_playback_request_id = 0
        self._radar_playback_product: str | None = None
        self.radar_replay_button: AppButton | None = None
        self.radar_replay_pause_button: AppButton | None = None
        self.radar_replay_slider: ctk.CTkSlider | None = None
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
        self._general_weather_outlook: list[dict[str, Any]] = []
        self._storm_outlook_loading = False
        self._storm_outlook_error: str | None = None
        self._storm_outlook_loaded_at: datetime | None = None
        self._outlook_use_local_feed = False
        self._outlook_day = 0
        self._outlook_metric = "Overall risk"
        self._outlook_location: tuple[float, float] | None = None
        self._outlook_location_source: str | None = None
        self._map_draw_started_at: float | None = None
        self._map_draw_label: str | None = None
        self._last_map_progress_at = 0.0
        self._session_started_at = datetime.now(timezone.utc)
        self._session_storm_ids: set[str] = set()
        self._session_rain_ids: set[str] = set()
        self._session_lightning_ids: set[str] = set()
        self._session_peak_storm_cells = 0
        self._session_peak_rain_echoes = 0
        self._session_refresh_cycles = 0
        self._source_last_success_at: dict[str, datetime] = {}
        self._source_observed_at: dict[str, datetime] = {}
        self._source_errors: dict[str, str] = {}
        self._source_extra: dict[str, str] = {}
        self._measurement_enabled = False
        self._measurement_points: list[tuple[float, float]] = []

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
                self._on_lightning_status,
            )
        )
        _terminal_progress("App", "Startup complete; background refreshes are running.")

    def _build_ui(self) -> None:
        ctk.set_appearance_mode("dark")
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
            bordercolor="#2b3940",
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Panel.TLabelframe.Label",
            background=PANEL,
            foreground=ACCENT,
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
            background="#293941",
            foreground=FOREGROUND,
            padding=(10, 6),
            borderwidth=0,
            focusthickness=0,
        )
        style.map(
            "TButton",
            background=[("disabled", "#202b30"), ("pressed", "#31454b"), ("active", "#35484e")],
            foreground=[("disabled", "#77858a")],
        )
        style.configure(
            "TEntry",
            fieldbackground="#111a1f",
            foreground=FOREGROUND,
            insertcolor=FOREGROUND,
            bordercolor="#35454b",
            lightcolor="#35454b",
            darkcolor="#35454b",
            padding=(7, 5),
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", ACCENT)],
            lightcolor=[("focus", ACCENT)],
            darkcolor=[("focus", ACCENT)],
        )
        style.configure(
            "TCombobox",
            fieldbackground="#202d33",
            background="#293941",
            foreground=FOREGROUND,
            arrowcolor=FOREGROUND,
            bordercolor="#35454b",
            lightcolor="#35454b",
            darkcolor="#35454b",
            padding=(7, 4),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#202d33"), ("disabled", "#1c272c")],
            foreground=[("readonly", FOREGROUND), ("disabled", MUTED)],
            selectbackground=[("readonly", "#202d33")],
            selectforeground=[("readonly", FOREGROUND)],
            bordercolor=[("focus", ACCENT)],
        )
        style.configure(
            "TNotebook",
            background=PANEL,
            borderwidth=0,
            tabmargins=(3, 3, 3, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background="#202d33",
            foreground=MUTED,
            padding=(12, 7),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#2b3b41"), ("active", "#26363c")],
            foreground=[("selected", FOREGROUND), ("active", FOREGROUND)],
        )
        style.configure(
            "TScrollbar",
            background="#35454b",
            troughcolor="#141e23",
            bordercolor="#141e23",
            arrowcolor=MUTED,
            gripcount=0,
        )

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
        self.measure_status = tk.StringVar(value="Click two map points to measure distance")

        header = ctk.CTkFrame(
            self.root,
            fg_color="transparent",
            corner_radius=0,
        )
        header.pack(fill="x", padx=22, pady=(16, 10))
        ttk.Label(header, text="STORMCHASE", style="Title.TLabel").pack(side="left")
        self.auth_button = AppButton(
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
        AppButton(header, text="Refresh data", command=self.refresh_data).pack(
            side="right", pady=(5, 0)
        )
        ttk.Label(
            header,
            textvariable=self.lightning_status,
            style="Status.TLabel",
        ).pack(side="right", padx=(0, 12), pady=(5, 0))

        body = ctk.CTkFrame(
            self.root,
            fg_color="transparent",
            corner_radius=0,
        )
        body.pack(fill="both", expand=True, padx=18, pady=(4, 10))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(
            body,
            fg_color=PANEL,
            corner_radius=12,
            width=270,
        )
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        sidebar.grid_propagate(False)
        sidebar_content = ttk.Frame(sidebar, style="Panel.TFrame", padding=14)
        sidebar_content.pack(fill="both", expand=True)
        ttk.Label(sidebar_content, text="LOCATION", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            sidebar_content,
            text="Device location is used only after you opt in. It stays on this device.",
            style="Panel.TLabel",
            wraplength=230,
            justify="left",
        ).pack(anchor="w", pady=(8, 10))
        AppButton(
            sidebar_content,
            text="Enable device location",
            command=self.enable_location,
        ).pack(fill="x")
        AppButton(
            sidebar_content,
            text="Stop location tracking",
            command=self.stop_location_tracking,
        ).pack(fill="x", pady=(6, 8))
        ttk.Label(sidebar_content, text="USE COORDINATES INSTEAD", style="Section.TLabel").pack(
            anchor="w"
        )
        coordinate_row = ttk.Frame(sidebar_content, style="Panel.TFrame")
        coordinate_row.pack(fill="x", pady=(8, 6))
        ttk.Entry(coordinate_row, textvariable=self.latitude_input, width=10).pack(
            side="left"
        )
        ttk.Entry(coordinate_row, textvariable=self.longitude_input, width=10).pack(
            side="left", padx=(6, 0)
        )
        AppButton(
            sidebar_content,
            text="Set map location",
            command=self.set_manual_location,
        ).pack(fill="x")
        self.measure_button = AppButton(
            sidebar_content,
            text="Measure distance",
            command=self._toggle_measurement,
        )
        self.measure_button.pack(fill="x", pady=(6, 0))
        ttk.Label(
            sidebar_content,
            textvariable=self.measure_status,
            style="Status.TLabel",
            wraplength=230,
            justify="left",
        ).pack(anchor="w", pady=(3, 0))

        ttk.Label(sidebar_content, text="STORM CELLS", style="Section.TLabel").pack(
            anchor="w", pady=(20, 7)
        )
        self.cell_list = tk.Listbox(
            sidebar_content,
            height=12,
            bg="#131d22",
            fg=FOREGROUND,
            selectbackground="#35584f",
            selectforeground="white",
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=("Segoe UI", 9),
            borderwidth=0,
            highlightbackground="#2b3940",
        )
        self.cell_list.pack(fill="both", expand=True)
        self.cell_list.bind("<<ListboxSelect>>", self._on_cell_list_select)
        ttk.Label(
            sidebar_content,
            textvariable=self.cell_detail,
            style="Panel.TLabel",
            wraplength=230,
            justify="left",
        ).pack(fill="x", pady=(8, 4))
        self.file_report_button_widget = AppButton(
            sidebar_content,
            text="File report for selected cell",
            command=self.file_report_button,
            state="disabled",
        )
        self.file_report_button_widget.pack(fill="x", pady=(4, 0))
        self.copy_cell_button = AppButton(
            sidebar_content,
            text="Copy cell summary",
            command=self.copy_selected_cell_summary,
            state="disabled",
        )
        self.copy_cell_button.pack(fill="x", pady=(6, 0))

        map_panel = ctk.CTkFrame(
            body,
            fg_color=PANEL,
            corner_radius=12,
        )
        map_panel.grid(row=0, column=1, sticky="nsew")
        map_content = ttk.Frame(map_panel, style="Panel.TFrame", padding=10)
        map_content.pack(fill="both", expand=True)
        map_content.rowconfigure(2, weight=1)
        map_content.columnconfigure(0, weight=1)
        map_toolbar = ttk.Frame(map_content, style="Panel.TFrame")
        map_toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(2, 6))
        self.map_mode_var = tk.StringVar(value=LIVE_MAP)
        AppComboBox(
            map_toolbar,
            variable=self.map_mode_var,
            values=(LIVE_MAP, DWD_WARNING_MAP, STORMANALYZER_MAP),
            state="readonly",
            width=210,
        ).pack(side="left", padx=(12, 0))
        self.map_mode_var.trace_add("write", self._on_map_mode_change)
        self.radar_product_var = tk.StringVar(value=REFLECTIVITY_RADAR)
        self.radar_product_combo = AppComboBox(
            map_toolbar,
            variable=self.radar_product_var,
            values=(REFLECTIVITY_RADAR, VELOCITY_RADAR),
            state="readonly",
            width=138,
        )
        self.radar_product_combo.pack(side="left", padx=(8, 0))
        self.radar_product_var.trace_add("write", self._on_radar_product_change)
        self.radar_replay_button = AppButton(
            map_toolbar,
            text="Replay",
            width=82,
            command=self._toggle_radar_playback,
        )
        self.radar_replay_button.pack(side="left", padx=(6, 0))
        self.radar_product_status = tk.StringVar(value="Reflectivity: waiting for data")
        ttk.Label(
            map_toolbar,
            textvariable=self.radar_product_status,
            style="Status.TLabel",
        ).pack(side="left", padx=(8, 0))
        self.outlook_day_var = tk.StringVar(value="Day 1")
        self.outlook_metric_var = tk.StringVar(value="Overall risk")
        self.outlook_day_combo = AppComboBox(
            map_toolbar, variable=self.outlook_day_var,
            values=OUTLOOK_DAY_LABELS, state="readonly", width=92,
        )
        self.outlook_day_combo.pack(side="left", padx=(8, 0))
        self.outlook_day_var.trace_add("write", self._on_outlook_selection_change)
        self.outlook_metric_combo = AppComboBox(
            map_toolbar, variable=self.outlook_metric_var,
            values=(
                "Overall risk", "Tornado", "Hail", "Wind", "STP", "SCP",
                *GENERAL_WEATHER_MAP_SCALES,
            ),
            state="readonly", width=148,
        )
        self.outlook_metric_combo.pack(side="left", padx=(5, 0))
        self.outlook_metric_var.trace_add("write", self._on_outlook_selection_change)
        self.outlook_day_combo.configure(state="disabled")
        self.outlook_metric_combo.configure(state="disabled")
        self.outlook_load_button = AppButton(
            map_toolbar,
            text="Load map",
            width=92,
            command=self._ensure_storm_outlook,
            state="disabled",
        )
        self.outlook_load_button.pack(side="left", padx=(8, 0))
        self.outlook_source_button = AppButton(
            map_toolbar,
            text="Use local feed",
            width=132,
            command=self._toggle_outlook_source,
            state="disabled",
        )
        self.outlook_source_button.pack(side="left", padx=(5, 0))
        AppButton(
            map_toolbar,
            text="Zoom +",
            width=72,
            command=lambda: self._change_zoom(1 / 1.2),
        ).pack(side="right", padx=(5, 0))
        AppButton(
            map_toolbar,
            text="Zoom −",
            width=72,
            command=lambda: self._change_zoom(1.2),
        ).pack(side="right", padx=(5, 0))
        AppButton(
            map_toolbar,
            text="Reset",
            width=64,
            command=self._reset_map_zoom,
        ).pack(side="right")
        replay_controls = ttk.Frame(map_content, style="Panel.TFrame")
        replay_controls.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        ttk.Label(
            replay_controls,
            text="RADAR REPLAY",
            style="Status.TLabel",
        ).pack(side="left", padx=(4, 10))
        self.radar_replay_slider = ctk.CTkSlider(
            replay_controls,
            from_=0,
            to=1,
            number_of_steps=1,
            width=320,
            height=16,
            fg_color="#28373d",
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color="#93c9bb",
            state="disabled",
            command=self._on_radar_replay_seek,
        )
        self.radar_replay_slider.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.radar_replay_pause_button = AppButton(
            replay_controls,
            text="Pause",
            width=88,
            command=self._toggle_radar_replay_pause,
            state="disabled",
        )
        self.radar_replay_pause_button.pack(side="right", padx=(0, 4))
        self.figure = Figure(figsize=(8, 6), dpi=100, facecolor=PANEL)
        self.axes = self.figure.add_subplot(111)
        self.map_canvas = FigureCanvasTkAgg(self.figure, master=map_content)
        self.map_canvas.get_tk_widget().grid(row=2, column=0, sticky="nsew")
        self.map_canvas.mpl_connect("pick_event", self._on_map_pick)
        self.map_canvas.mpl_connect("button_press_event", self._on_map_click)
        self.map_canvas.mpl_connect("scroll_event", self._on_map_scroll)
        self.map_canvas.mpl_connect("resize_event", self._on_map_resize)
        self.map_canvas.mpl_connect("draw_event", self._on_map_draw_complete)
        report_panel = ttk.LabelFrame(
            map_content,
            text="Details and community reports",
            style="Panel.TLabelframe",
            padding=(6, 4),
            height=150,
        )
        self.report_panel = report_panel
        report_panel.grid(row=3, column=0, sticky="ew", padx=8, pady=(8, 2))
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
        self.case_analysis_tab = ttk.Frame(self.report_notebook)
        self.case_analysis_tab.columnconfigure(0, weight=1)
        self.case_analysis_tab.rowconfigure(0, weight=1)
        self.report_notebook.add(self.case_analysis_tab, text="Case analysis")
        self.case_analysis_text = tk.Text(
            self.case_analysis_tab,
            height=5,
            wrap="word",
            state="disabled",
            bg="#111b22",
            fg=FOREGROUND,
            relief="flat",
            highlightthickness=0,
            font=("Consolas", 8),
        )
        self.case_analysis_text.grid(row=0, column=0, sticky="nsew")
        case_scrollbar = ttk.Scrollbar(
            self.case_analysis_tab,
            orient="vertical",
            command=self.case_analysis_text.yview,
        )
        case_scrollbar.grid(row=0, column=1, sticky="ns")
        self.case_analysis_text.configure(yscrollcommand=case_scrollbar.set)
        self.admin_tab = ttk.Frame(self.report_notebook)
        self.report_notebook.add(self.admin_tab, text="Admin")
        self._build_admin_tab()
        self.diagnostics_tab = ttk.Frame(self.report_notebook)
        self.diagnostics_tab.columnconfigure(0, weight=1)
        self.diagnostics_tab.rowconfigure(0, weight=1)
        self.report_notebook.add(self.diagnostics_tab, text="Data status")
        self.diagnostics_text = tk.Text(
            self.diagnostics_tab,
            height=5,
            wrap="word",
            state="disabled",
            bg="#111b22",
            fg=FOREGROUND,
            relief="flat",
            highlightthickness=0,
            font=("Consolas", 8),
        )
        self.diagnostics_text.grid(row=0, column=0, sticky="nsew")
        diagnostics_scrollbar = ttk.Scrollbar(
            self.diagnostics_tab,
            orient="vertical",
            command=self.diagnostics_text.yview,
        )
        diagnostics_scrollbar.grid(row=0, column=1, sticky="ns")
        self.diagnostics_text.configure(yscrollcommand=diagnostics_scrollbar.set)
        self.outlook_analysis_tab = ttk.Frame(self.report_notebook)
        self.report_notebook.add(self.outlook_analysis_tab, text="Outlook analysis")
        self._build_outlook_analysis_tab()
        self._refresh_case_analysis_panel()
        self._refresh_admin_controls()
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
        self._refresh_diagnostics()



    def _build_admin_tab(self) -> None:
        self.admin_tab.columnconfigure(0, weight=1)
        self.admin_tab.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            self.admin_tab,
            bg=PANEL,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            self.admin_tab,
            orient="vertical",
            command=canvas.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)
        outer = ttk.Frame(canvas, padding=8)
        canvas_window = canvas.create_window((0, 0), window=outer, anchor="nw")

        def update_scroll_region(_event: Any = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def update_content_width(event: tk.Event[tk.Misc]) -> None:
            canvas.itemconfigure(canvas_window, width=event.width)

        outer.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", update_content_width)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        self.admin_status = tk.StringVar(value="Checking administrator authorization…")
        ttk.Label(
            outer,
            textvariable=self.admin_status,
            style="Status.TLabel",
            wraplength=800,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(
            outer,
            text=(
                "UNOFFICIAL COMMUNITY WARNINGS. These polygons are created by StormChase "
                "administrators and are not DWD/NWS warnings. Always check WarnWetter, "
                "official warning channels and local authorities."
            ),
            wraplength=800,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 8))

        warning_frame = ttk.LabelFrame(
            outer,
            text="Issue a polygon warning",
            style="Panel.TLabelframe",
            padding=8,
        )
        warning_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        warning_frame.columnconfigure(1, weight=1)

        self.admin_category_var = tk.StringVar(value="Damaging wind")
        self.admin_level_var = tk.StringVar(value="1")
        self.admin_headline_var = tk.StringVar()
        self.admin_expiry_var = tk.StringVar(value="3")
        self.admin_catastrophic_var = tk.BooleanVar(value=False)
        self.admin_level_info = tk.StringVar()
        self.admin_polygon_status = tk.StringVar(value="No polygon drawn.")
        self.admin_description = tk.Text(
            warning_frame,
            height=3,
            wrap="word",
            bg="#111b22",
            fg=FOREGROUND,
            insertbackground=FOREGROUND,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#34454d",
        )

        ttk.Label(warning_frame, text="Hazard").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        self.admin_category_combo = ttk.Combobox(
            warning_frame,
            textvariable=self.admin_category_var,
            values=tuple(WARNING_CATEGORY_LABELS.values()),
            state="readonly",
            width=22,
        )
        self.admin_category_combo.grid(row=0, column=1, sticky="ew", padx=4, pady=3)
        self.admin_category_combo.bind("<<ComboboxSelected>>", self._update_warning_level_description)

        ttk.Label(warning_frame, text="Level").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        self.admin_level_combo = ttk.Combobox(
            warning_frame,
            textvariable=self.admin_level_var,
            values=("1", "2", "3"),
            state="readonly",
            width=8,
        )
        self.admin_level_combo.grid(row=1, column=1, sticky="w", padx=4, pady=3)
        self.admin_level_combo.bind("<<ComboboxSelected>>", self._update_warning_level_description)

        ttk.Label(
            warning_frame,
            textvariable=self.admin_level_info,
            wraplength=760,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 4))

        ttk.Label(warning_frame, text="Headline").grid(row=3, column=0, sticky="w", padx=4, pady=3)
        self.admin_headline_entry = ttk.Entry(warning_frame, textvariable=self.admin_headline_var)
        self.admin_headline_entry.grid(row=3, column=1, sticky="ew", padx=4, pady=3)

        ttk.Label(warning_frame, text="Details / evidence").grid(row=4, column=0, sticky="nw", padx=4, pady=3)
        self.admin_description.grid(row=4, column=1, sticky="ew", padx=4, pady=3)

        expiry_frame = ttk.Frame(warning_frame)
        expiry_frame.grid(row=5, column=1, sticky="w", padx=4, pady=3)
        ttk.Label(warning_frame, text="Valid for").grid(row=5, column=0, sticky="w", padx=4, pady=3)
        self.admin_expiry_spin = ttk.Spinbox(
            expiry_frame,
            from_=1,
            to=72,
            width=5,
            textvariable=self.admin_expiry_var,
        )
        self.admin_expiry_spin.pack(side="left")
        ttk.Label(expiry_frame, text="hours (maximum 72)").pack(side="left", padx=5)

        self.admin_catastrophic_check = ttk.Checkbutton(
            warning_frame,
            text="I have reliable evidence and the catastrophic-impact criteria are met, including independent supervisor/team review where applicable",
            variable=self.admin_catastrophic_var,
        )
        self.admin_catastrophic_check.grid(row=6, column=0, columnspan=2, sticky="w", padx=4, pady=3)

        polygon_actions = ttk.Frame(warning_frame)
        polygon_actions.grid(row=7, column=0, columnspan=2, sticky="ew", padx=4, pady=4)
        self.admin_draw_button = AppButton(
            polygon_actions,
            text="Draw polygon on map",
            command=self._begin_warning_polygon,
            width=150,
        )
        self.admin_draw_button.pack(side="left", padx=(0, 6))
        self.admin_clear_polygon_button = AppButton(
            polygon_actions,
            text="Clear draft",
            command=self._cancel_warning_polygon,
            width=100,
        )
        self.admin_clear_polygon_button.pack(side="left", padx=(0, 6))
        self.admin_publish_button = AppButton(
            polygon_actions,
            text="Publish warning",
            command=self._publish_admin_warning,
            width=130,
        )
        self.admin_publish_button.pack(side="left")
        ttk.Label(
            warning_frame,
            textvariable=self.admin_polygon_status,
            wraplength=760,
        ).grid(row=8, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 3))

        active_frame = ttk.LabelFrame(
            outer,
            text="Active community warnings",
            style="Panel.TLabelframe",
            padding=6,
        )
        active_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        active_frame.columnconfigure(0, weight=1)
        active_frame.rowconfigure(0, weight=1)
        self.admin_warning_list = tk.Listbox(
            active_frame,
            height=4,
            bg="#111b22",
            fg=FOREGROUND,
            selectbackground="#31564d",
            relief="flat",
            highlightthickness=0,
            font=("Consolas", 8),
        )
        self.admin_warning_list.grid(row=0, column=0, sticky="nsew")
        active_scroll = ttk.Scrollbar(active_frame, orient="vertical", command=self.admin_warning_list.yview)
        active_scroll.grid(row=0, column=1, sticky="ns")
        self.admin_warning_list.configure(yscrollcommand=active_scroll.set)
        self.admin_cancel_button = AppButton(
            active_frame,
            text="Cancel selected warning",
            command=self._cancel_selected_community_warning,
            width=170,
        )
        self.admin_cancel_button.grid(row=1, column=0, sticky="w", pady=(5, 0))

        review_frame = ttk.LabelFrame(
            outer,
            text="Community report review",
            style="Panel.TLabelframe",
            padding=6,
        )
        review_frame.grid(row=4, column=0, sticky="nsew")
        review_frame.columnconfigure(0, weight=1)
        self.admin_report_list = tk.Listbox(
            review_frame,
            height=4,
            bg="#111b22",
            fg=FOREGROUND,
            selectbackground="#31564d",
            relief="flat",
            highlightthickness=0,
            font=("Consolas", 8),
        )
        self.admin_report_list.grid(row=0, column=0, sticky="ew")
        review_scroll = ttk.Scrollbar(review_frame, orient="vertical", command=self.admin_report_list.yview)
        review_scroll.grid(row=0, column=1, sticky="ns")
        self.admin_report_list.configure(yscrollcommand=review_scroll.set)
        self.admin_report_status_var = tk.StringVar(value="unverified")
        self.admin_review_note_var = tk.StringVar()
        review_controls = ttk.Frame(review_frame)
        review_controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.admin_report_status_combo = ttk.Combobox(
            review_controls,
            textvariable=self.admin_report_status_var,
            values=("unverified", "under_review", "corroborated", "rejected"),
            state="readonly",
            width=17,
        )
        self.admin_report_status_combo.pack(side="left", padx=(0, 5))
        self.admin_review_note_entry = ttk.Entry(
            review_controls,
            textvariable=self.admin_review_note_var,
            width=32,
        )
        self.admin_review_note_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.admin_moderate_button = AppButton(
            review_controls,
            text="Save review",
            command=self._moderate_selected_report,
            width=110,
        )
        self.admin_moderate_button.pack(side="left")
        self.admin_editor_widgets = [
            self.admin_category_combo,
            self.admin_level_combo,
            self.admin_headline_entry,
            self.admin_description,
            self.admin_expiry_spin,
            self.admin_catastrophic_check,
        ]
        self._update_warning_level_description()
        self._refresh_admin_controls()
        self._populate_admin_warning_list()
        self._populate_admin_report_list()

    def _update_warning_level_description(self, *_args: Any) -> None:
        if not hasattr(self, "admin_level_info"):
            return
        category = next(
            (
                key for key, label in WARNING_CATEGORY_LABELS.items()
                if label == self.admin_category_var.get()
            ),
            "damaging_wind",
        )
        try:
            level = int(self.admin_level_var.get())
            tier = WARNING_LEVELS[category][level - 1]
        except (ValueError, IndexError):
            self.admin_level_info.set("Choose a valid hazard level.")
            return
        threshold = f" · {tier.threshold}" if tier.threshold else ""
        self.admin_level_info.set(
            f"{tier.tag} · {tier.title}{threshold}\n{tier.description}"
        )

    def _refresh_admin_controls(self) -> None:
        if not hasattr(self, "admin_status"):
            return
        is_admin = bool(self._is_stormchase_admin and self.auth_user is not None)
        if is_admin:
            self.admin_status.set(
                f"Administrator authorization active · {self.auth_user.username or 'authenticated account'}"
            )
        elif self.auth_user is None:
            self.admin_status.set(
                "Not authorized: restore/set a report identity, then have the project owner grant admin membership."
            )
        else:
            self.admin_status.set(
                "This account is not an administrator. Membership is granted in Supabase by the project owner."
            )
        for widget in getattr(self, "admin_editor_widgets", ()):
            widget.configure(state="normal" if is_admin else "disabled")
        if hasattr(self, "admin_category_combo"):
            self.admin_category_combo.configure(state="readonly" if is_admin else "disabled")
            self.admin_level_combo.configure(state="readonly" if is_admin else "disabled")
        if hasattr(self, "admin_report_status_combo"):
            self.admin_report_status_combo.configure(state="readonly" if is_admin else "disabled")
        for widget_name in (
            "admin_draw_button",
            "admin_clear_polygon_button",
            "admin_publish_button",
            "admin_cancel_button",
            "admin_report_status_combo",
            "admin_review_note_entry",
            "admin_moderate_button",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.configure(state="normal" if is_admin else "disabled")
        if not is_admin and self._warning_drawing_enabled:
            self._warning_drawing_enabled = False
            self._warning_polygon_draft.clear()
        self._populate_admin_warning_list()
        self._populate_admin_report_list()

    def _populate_admin_warning_list(self) -> None:
        if not hasattr(self, "admin_warning_list"):
            return
        now = datetime.now(timezone.utc)
        visible_warnings = []
        for warning in self.active_community_warnings:
            try:
                expiry = datetime.fromisoformat(
                    str(warning.get("expires_at", "")).replace("Z", "+00:00")
                )
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if expiry > now:
                visible_warnings.append(warning)
        self._admin_warning_rows = tuple(visible_warnings)
        self.admin_warning_list.delete(0, tk.END)
        for warning in self._admin_warning_rows:
            category = WARNING_CATEGORY_LABELS.get(warning.get("category"), "Warning")
            level = warning.get("level", "?")
            headline = warning.get("headline", "Untitled warning")
            expires = warning.get("expires_at", "expiry unavailable")
            self.admin_warning_list.insert(
                tk.END,
                f"L{level} {category} · {headline} · expires {expires}",
            )

    def _populate_admin_report_list(self) -> None:
        if not hasattr(self, "admin_report_list"):
            return
        self._admin_report_rows = tuple(self.community_reports)
        self.admin_report_list.delete(0, tk.END)
        for report in self._admin_report_rows:
            status = report.get("verification_status", "unverified")
            self.admin_report_list.insert(
                tk.END,
                f"{status.upper():13} · {report.get('reporter_username', 'unknown')} · "
                f"{report.get('cell_id', 'unknown cell')} · {report.get('created_at', '')}",
            )

    def _begin_warning_polygon(self) -> None:
        if not self._is_stormchase_admin:
            messagebox.showerror("Admin only", "This account cannot issue community warnings.", parent=self.root)
            return
        if self.map_mode == STORMANALYZER_MAP:
            messagebox.showinfo(
                "Switch map mode",
                "Switch to the Live map or DWD warning map before drawing a warning polygon.",
                parent=self.root,
            )
            return
        self._warning_polygon_draft.clear()
        self._warning_drawing_enabled = True
        self.admin_polygon_status.set("Polygon drawing active: click at least 3 map vertices, then publish.")
        self.status.set("Community warning polygon drawing active.")
        self._draw_map()

    def _cancel_warning_polygon(self) -> None:
        self._warning_drawing_enabled = False
        self._warning_polygon_draft.clear()
        if hasattr(self, "admin_polygon_status"):
            self.admin_polygon_status.set("Polygon draft cleared.")
        self._draw_map()

    def _publish_admin_warning(self) -> None:
        if not self._is_stormchase_admin or self.supabase is None:
            messagebox.showerror("Admin only", "A database-authorized administrator account is required.", parent=self.root)
            return
        category = next(
            (
                key for key, label in WARNING_CATEGORY_LABELS.items()
                if label == self.admin_category_var.get()
            ),
            "",
        )
        try:
            level = int(self.admin_level_var.get())
            hours = int(self.admin_expiry_var.get())
            if not 1 <= hours <= 72:
                raise ValueError("Warning duration must be between 1 and 72 hours.")
            expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
            description = self.admin_description.get("1.0", "end-1c").strip()
            validate_warning_payload(
                category,
                level,
                self._warning_polygon_draft,
                self.admin_headline_var.get(),
                description,
                expires_at,
                catastrophic_confirmed=self.admin_catastrophic_var.get(),
            )
        except (ValueError, TypeError) as error:
            messagebox.showerror("Invalid warning", str(error), parent=self.root)
            return
        tier = WARNING_LEVELS[category][level - 1]
        confirmed = messagebox.askyesno(
            "Publish unofficial warning?",
            f"{tier.title}\n\n{self.admin_headline_var.get().strip()}\n\n"
            f"This is an unofficial StormChase community warning, not a DWD/NWS warning. "
            f"It will remain active for {hours} hours. Publish?",
            parent=self.root,
        )
        if not confirmed:
            return
        self.admin_publish_button.configure(state="disabled")
        self.loop.create_task(
            self._issue_community_warning_task(
                category,
                level,
                [point[:] for point in self._warning_polygon_draft],
                self.admin_headline_var.get().strip(),
                description,
                expires_at,
                self.admin_catastrophic_var.get(),
            )
        )

    async def _issue_community_warning_task(
        self,
        category: str,
        level: int,
        polygon: list[list[float]],
        headline: str,
        description: str,
        expires_at: datetime,
        catastrophic_confirmed: bool,
    ) -> None:
        client = self.supabase
        try:
            if client is None:
                raise SupabaseError("Supabase is not configured.")
            await self.loop.run_in_executor(
                self.executor,
                lambda: client.issue_community_warning(
                    category, level, polygon, headline, description, expires_at,
                    catastrophic_confirmed=catastrophic_confirmed,
                ),
            )
            self.active_community_warnings = await self.loop.run_in_executor(
                self.executor, client.active_community_warnings
            )
        except Exception as error:
            self.status.set(f"Warning was not published: {type(error).__name__}: {error}")
            messagebox.showerror("Warning not published", str(error), parent=self.root)
        else:
            self._warning_drawing_enabled = False
            self._warning_polygon_draft.clear()
            self.admin_headline_var.set("")
            self.admin_description.delete("1.0", tk.END)
            self.admin_catastrophic_var.set(False)
            self.admin_polygon_status.set("Warning published to the shared community feed.")
            self.status.set("Unofficial community warning published.")
            self._populate_admin_warning_list()
            self._draw_map()
            self._refresh_case_analysis_panel()
        finally:
            if hasattr(self, "admin_publish_button"):
                self.admin_publish_button.configure(
                    state="normal" if self._is_stormchase_admin else "disabled"
                )

    def _cancel_selected_community_warning(self) -> None:
        if not self._is_stormchase_admin or self.supabase is None:
            return
        selected = self.admin_warning_list.curselection()
        if not selected or selected[0] >= len(self._admin_warning_rows):
            return
        warning = self._admin_warning_rows[selected[0]]
        if not messagebox.askyesno(
            "Cancel community warning",
            f"Cancel '{warning.get('headline', 'warning')}'?",
            parent=self.root,
        ):
            return
        self.loop.create_task(self._cancel_community_warning_task(str(warning["id"])))

    async def _cancel_community_warning_task(self, warning_id: str) -> None:
        client = self.supabase
        try:
            if client is None:
                raise SupabaseError("Supabase is not configured.")
            await self.loop.run_in_executor(
                self.executor, client.cancel_community_warning, warning_id
            )
            self.active_community_warnings = await self.loop.run_in_executor(
                self.executor, client.active_community_warnings
            )
        except Exception as error:
            messagebox.showerror("Could not cancel warning", str(error), parent=self.root)
        else:
            self._populate_admin_warning_list()
            self._draw_map()
            self.status.set("Community warning cancelled.")

    def _moderate_selected_report(self) -> None:
        if not self._is_stormchase_admin or self.supabase is None:
            return
        selected = self.admin_report_list.curselection()
        if not selected or selected[0] >= len(self._admin_report_rows):
            messagebox.showinfo("Select a report", "Choose a community report first.", parent=self.root)
            return
        report = self._admin_report_rows[selected[0]]
        self.loop.create_task(
            self._moderate_report_task(
                str(report["id"]),
                self.admin_report_status_var.get(),
                self.admin_review_note_var.get(),
            )
        )

    async def _moderate_report_task(
        self,
        report_id: str,
        status: str,
        note: str,
    ) -> None:
        client = self.supabase
        try:
            if client is None:
                raise SupabaseError("Supabase is not configured.")
            await self.loop.run_in_executor(
                self.executor, client.moderate_storm_report, report_id, status, note
            )
            self.community_reports = await self.loop.run_in_executor(
                self.executor, client.recent_reports
            )
        except Exception as error:
            messagebox.showerror("Report review failed", str(error), parent=self.root)
        else:
            self._render_community_reports()
            self.status.set(f"Community report marked {status}.")

    def _refresh_case_analysis_panel(self) -> None:
        if not hasattr(self, "case_analysis_text"):
            return
        lines = [
            "LOCAL CASE ANALYSIS",
            "Cell history, forecast verification and historical comparisons are stored locally in SQLite.",
            "No additional weather API requests are made by this panel.",
            "",
        ]
        cell = self.selected_cell
        if isinstance(cell, StormCell):
            stats = self.case_analysis.case_statistics(cell.identifier)
            history = self.case_analysis.history(cell.identifier, limit=24)
            lines.append(f"SELECTED CELL · {cell.identifier}")
            if stats:
                lines.extend(
                    (
                        f"Case age: {stats['duration_minutes']:.0f} min",
                        f"Observations: {stats['observation_count']}",
                        f"Maximum observed reflectivity: {stats['max_reflectivity_dbz'] if stats['max_reflectivity_dbz'] is not None else 'n/a'} dBZ",
                        f"Accumulated track length: {stats['path_length_km']:.1f} km",
                    )
                )
            if history:
                lines.append("EVOLUTION (oldest → newest)")
                for item in history[-12:]:
                    reflectivity = (
                        f"{item['reflectivity_dbz']:.1f} dBZ"
                        if item["reflectivity_dbz"] is not None else "dBZ n/a"
                    )
                    lines.append(
                        f"{item['observed_at']} · {item['latitude']:.3f}, "
                        f"{item['longitude']:.3f} · {reflectivity}"
                    )
                if len(history) >= 2:
                    first, latest = history[0], history[-1]
                    minutes = max(
                        1.0,
                        (
                            datetime.fromisoformat(latest["observed_at"])
                            - datetime.fromisoformat(first["observed_at"])
                        ).total_seconds() / 60.0,
                    )
                    distance = self._distance_km(
                        (first["latitude"], first["longitude"]),
                        (latest["latitude"], latest["longitude"]),
                    )
                    lines.append(
                        f"Retained displacement: {distance:.1f} km · "
                        f"mean speed {distance / (minutes / 60.0):.1f} km/h"
                    )
            comparisons = self.case_analysis.compare_cases(cell.identifier)
            if comparisons:
                lines.extend(("", "SIMILAR LOCAL CASES (not a forecast or analogue probability)"))
                for item in comparisons:
                    lines.append(
                        f"{item['cell_id']} · score {item['similarity_score']:.2f} · "
                        f"{item['max_reflectivity_dbz']:.1f} dBZ · "
                        f"{item['path_length_km']:.1f} km path"
                    )
        elif isinstance(cell, RainCell):
            lines.extend(
                (
                    f"SELECTED RAIN ECHO · {cell.identifier}",
                    f"Maximum reflectivity: {cell.max_reflectivity_dbz:.1f} dBZ",
                    f"Area: {cell.area_pixels} radar pixels",
                    f"Track points retained: {len(cell.track)}",
                    "Rain echoes are a 20–30 dBZ reflectivity grouping, not a precipitation-rate product.",
                )
            )
        else:
            lines.append("Select a KONRAD3D cell to inspect its local evolution and compare historical cases.")

        skill = self.case_analysis.forecast_skill()
        lines.extend(("", "FORECAST VERIFICATION"))
        if not skill:
            lines.append("No matched forecast/observation pairs have been verified yet.")
        else:
            for item in skill:
                lines.append(
                    f"{item['model']} · +{item['lead_minutes']} min · "
                    f"n={item['samples']} · mean position error "
                    f"{item['mean_position_error_km']:.1f} km · "
                    f"heading error {item['mean_heading_error_degrees']:.0f}° · "
                    f"speed error {item['mean_speed_error_kmh']:.1f} km/h"
                )

        lines.extend(("", "STORM EVENT TIMELINE"))
        timeline = self.case_analysis.timeline(30)
        if not timeline:
            lines.append("No local storm events recorded yet.")
        else:
            for event in timeline:
                location = (
                    f" · {event['latitude']:.3f}, {event['longitude']:.3f}"
                    if event["latitude"] is not None and event["longitude"] is not None
                    else ""
                )
                lines.append(
                    f"{event['event_time']} · {event['event_type']} · "
                    f"{event['summary']}{location} [{event['source']}]"
                )

        self.case_analysis_text.configure(state="normal")
        self.case_analysis_text.delete("1.0", tk.END)
        self.case_analysis_text.insert("1.0", "\n".join(lines))
        self.case_analysis_text.configure(state="disabled")

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
        AppButton(
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

        general_weather_box = ttk.LabelFrame(
            content,
            text="General weather",
            style="Panel.TLabelframe",
            padding=8,
        )
        general_weather_box.pack(fill="x", pady=(0, 10))
        self.general_weather_text = tk.StringVar(
            value="General-weather forecast will appear here when available."
        )
        ttk.Label(
            general_weather_box,
            textvariable=self.general_weather_text,
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
        AppButton(
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
        _terminal_progress(
            "Outlook",
            f"Using manually selected location {latitude:.4f}, {longitude:.4f}.",
        )
        self.outlook_location_status.set(
            f"Using manual coordinates: {latitude:.4f}, {longitude:.4f}"
        )
        self._show_outlook_calculation(latitude, longitude)
        if self.map_mode == STORMANALYZER_MAP:
            self._draw_map()

    def _clear_outlook_calculation(self, message: str) -> None:
        self._outlook_location = None
        self.outlook_calculation_text.set(message)
        self.general_weather_text.set(
            "Enter coordinates or enable device location to show the general-weather forecast."
        )

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

    def _set_general_weather_summary(self, general_weather: Any) -> None:
        analysis = (
            general_weather.get("analysis")
            if isinstance(general_weather, dict)
            else None
        )
        if not isinstance(analysis, dict):
            self.general_weather_text.set(
                "The published general-weather forecast is unavailable for this point and day."
            )
            return

        probability = analysis.get("precipitation_probability_percent")
        probability_text = (
            f"{probability:.0f}%"
            if isinstance(probability, (int, float))
            else "unavailable"
        )
        rain_text = "Rain likely" if analysis.get("rain_likely") else "Rain not indicated"
        if analysis.get("snow_likely"):
            frozen_text = "Snow likely"
        elif analysis.get("frozen_precipitation_likely"):
            frozen_text = "Frozen precipitation likely"
        else:
            frozen_text = "No frozen precipitation indicated"
        self.general_weather_text.set(
            f"{analysis.get('headline', analysis.get('condition', 'Unavailable'))} · "
            f"{analysis.get('condition', 'Unavailable')}\n"
            f"Precipitation chance: {probability_text} · {rain_text} · {frozen_text}\n"
            f"High / low: {_format_optional_parameter(general_weather.get('temperature_max_c'))} / "
            f"{_format_optional_parameter(general_weather.get('temperature_min_c'))} °C · "
            f"Total precipitation: {_format_optional_parameter(general_weather.get('precipitation_sum_mm'))} mm · "
            f"Rain: {_format_optional_parameter(general_weather.get('rain_sum_mm'))} mm · "
            f"Wind max: {_format_optional_parameter(general_weather.get('wind_speed_max_kmh'))} km/h"
        )

    def _show_outlook_calculation(self, latitude: float, longitude: float) -> None:
        self._outlook_location = (longitude, latitude)
        entry = nearest_outlook_entry(
            self._storm_outlook,
            self._outlook_day,
            latitude,
            longitude,
        )
        weather_candidates = [
            candidate
            for candidate in self._general_weather_outlook
            if candidate.get("day") == self._outlook_day
            and isinstance(candidate.get("general_weather"), dict)
        ]
        nearest_weather = min(
            weather_candidates,
            key=lambda candidate: (
                (candidate["latitude"] - latitude) ** 2
                + (
                    (candidate["longitude"] - longitude)
                    * math.cos(math.radians(latitude))
                )
                ** 2
            ),
            default=None,
        )
        general_weather = (
            entry.get("general_weather")
            if entry is not None
            else None
        )
        if not isinstance(general_weather, dict) and nearest_weather is not None:
            general_weather = nearest_weather["general_weather"]
        self._set_general_weather_summary(general_weather)
        if entry is None:
            self.outlook_calculation_text.set(
                f"No calculation is available for Day {self._outlook_day + 1} at "
                f"{latitude:.2f}° N, {longitude:.2f}° E yet."
            )
            if self._storm_outlook_loaded_at is None and not self._storm_outlook_loading:
                self._ensure_storm_outlook()
            return

        environment = entry["environment"]
        threats = ", ".join(entry["threats"]) or "No specific severe-weather threats indicated"
        criticism = "\n".join(entry["criticism"]) or "No additional assessment notes."
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
        self._stop_radar_playback(redraw=False)
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
        if (
            self.velocity_radar_site is not None
            and self.velocity_radar_site.site != site.site
        ):
            self._stop_radar_playback(redraw=False)
        self.velocity_radar_site = site
        self.velocity_radar_frame = self.velocity_radar_frames.get(site.site.value)
        if self.velocity_radar_frame is None:
            self._request_velocity_radar(site)
        else:
            self.radar_product_status.set(
                self._velocity_site_status(site, self.velocity_radar_frame)
            )
        self._draw_map()

    def _update_radar_replay_button(self) -> None:
        button = self.radar_replay_button
        if button is None:
            return
        if self._radar_playback_loading:
            text = "Cancel load"
        elif self._radar_playback_index is not None:
            text = "Stop replay"
        else:
            text = "Replay"
        state = (
            "normal"
            if self.map_mode == LIVE_MAP
            else "disabled"
        )
        button.configure(text=text, state=state)
        active = (
            self.map_mode == LIVE_MAP
            and self._radar_playback_index is not None
            and bool(self._radar_playback_frames)
        )
        if self.radar_replay_slider is not None:
            frame_count = len(self._radar_playback_frames)
            self.radar_replay_slider.configure(
                to=max(frame_count - 1, 1),
                number_of_steps=max(frame_count - 1, 1),
                state="normal" if active and frame_count > 1 else "disabled",
            )
            if active and self._radar_playback_index is not None:
                self.radar_replay_slider.set(self._radar_playback_index)
            else:
                self.radar_replay_slider.set(0)
        if self.radar_replay_pause_button is not None:
            self.radar_replay_pause_button.configure(
                text="Resume" if self._radar_playback_paused else "Pause",
                state="normal" if active else "disabled",
            )

    def _stop_radar_playback(self, *, redraw: bool = True) -> None:
        was_active = (
            self._radar_playback_loading
            or self._radar_playback_after_id is not None
            or self._radar_playback_index is not None
        )
        self._radar_playback_request_id += 1
        if self._radar_playback_after_id is not None:
            self.root.after_cancel(self._radar_playback_after_id)
        self._radar_playback_after_id = None
        self._radar_playback_frames = ()
        self._radar_playback_index = None
        self._radar_playback_loading = False
        self._radar_playback_paused = False
        self._radar_playback_product = None
        self._update_radar_replay_button()
        if self.radar_product == VELOCITY_RADAR:
            self.radar_product_status.set(
                self._velocity_site_status(
                    self.velocity_radar_site,
                    self.velocity_radar_frame,
                )
                if self.velocity_radar_site is not None
                else self._radar_status_text(VELOCITY_RADAR, None)
            )
        else:
            self.radar_product_status.set(
                self._radar_status_text(REFLECTIVITY_RADAR, self.radar_frame)
            )
        if redraw and was_active and not self._closed:
            self._draw_map()

    def _toggle_radar_playback(self) -> None:
        if (
            self._radar_playback_loading
            or self._radar_playback_index is not None
        ):
            self._stop_radar_playback()
            return
        if self.map_mode != LIVE_MAP:
            return

        product = self.radar_product
        site = self.velocity_radar_site if product == VELOCITY_RADAR else None
        if product == VELOCITY_RADAR and site is None:
            self.radar_product_status.set("Choose a velocity radar site first.")
            return

        current_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        end = current_time.replace(
            minute=current_time.minute - current_time.minute % 5,
            second=0,
            microsecond=0,
        )
        start = end - timedelta(minutes=30)
        self._radar_playback_request_id += 1
        request_id = self._radar_playback_request_id
        self._radar_playback_product = product
        self._radar_playback_loading = True
        self._radar_playback_paused = False
        self.radar_product_status.set(f"Loading 30-minute {product.lower()} replay…")
        _terminal_progress(
            "Radar replay",
            f"Fetching {product.lower()} frames from {self._format_time(start)} "
            f"through {self._format_time(end)}.",
        )
        self._update_radar_replay_button()
        self.loop.create_task(
            self._load_radar_playback(request_id, product, site, start, end)
        )

    async def _load_radar_playback(
        self,
        request_id: int,
        product: str,
        site: VelocityRadarSite | None,
        start: datetime,
        end: datetime,
    ) -> None:
        parameter = (
            VELOCITY_PRODUCT if product == VELOCITY_RADAR else REFLECTIVITY_PRODUCT
        )
        try:
            frames = await self.loop.run_in_executor(
                self.executor,
                radar_playback,
                start,
                end,
                timedelta(minutes=5),
                parameter,
                site.site if site is not None else None,
            )
        except Exception as error:
            if request_id != self._radar_playback_request_id or self._closed:
                return
            self._radar_playback_loading = False
            self._radar_playback_product = None
            self.radar_product_status.set(
                f"Replay unavailable: {type(error).__name__}: {error}"
            )
            _terminal_progress(
                "Radar replay",
                f"Replay load failed: {type(error).__name__}: {error}",
            )
            self._update_radar_replay_button()
            return

        if request_id != self._radar_playback_request_id or self._closed:
            return
        self._radar_playback_loading = False
        self._radar_playback_frames = frames
        if not frames:
            self._radar_playback_product = None
            self.radar_product_status.set(
                "No archived radar frames were available for that time range."
            )
            _terminal_progress("Radar replay", "No radar frames were returned.")
            self._update_radar_replay_button()
            return

        self._radar_playback_index = 0
        self.radar_product_status.set(self._radar_playback_status())
        _terminal_progress(
            "Radar replay",
            f"Loaded {len(frames)} {product.lower()} frames; starting playback.",
        )
        self._update_radar_replay_button()
        self._draw_map()
        self._schedule_next_radar_frame()

    def _radar_playback_status(self) -> str:
        index = self._radar_playback_index
        if index is None or not self._radar_playback_frames:
            return "Radar replay unavailable"
        frame = self._radar_playback_frames[index]
        product = self._radar_playback_product or self.radar_product
        timestamp = self._format_time(frame.timestamp)
        if product == VELOCITY_RADAR and self.velocity_radar_site is not None:
            product = f"Velocity Â· {self.velocity_radar_site.name}"
        return (
            f"{product} replay Â· {timestamp} Â· "
            f"{index + 1}/{len(self._radar_playback_frames)}"
        )

    def _on_radar_replay_seek(self, value: float) -> None:
        if self._radar_playback_index is None or not self._radar_playback_frames:
            return
        self._radar_playback_index = min(
            max(int(round(float(value))), 0),
            len(self._radar_playback_frames) - 1,
        )
        self.radar_product_status.set(self._radar_playback_status())
        self._draw_map()

    def _toggle_radar_replay_pause(self) -> None:
        if self._radar_playback_index is None or not self._radar_playback_frames:
            return
        if self._radar_playback_paused:
            self._radar_playback_paused = False
            self.radar_product_status.set(self._radar_playback_status())
            self._schedule_next_radar_frame()
        else:
            if self._radar_playback_after_id is not None:
                self.root.after_cancel(self._radar_playback_after_id)
                self._radar_playback_after_id = None
            self._radar_playback_paused = True
            self.radar_product_status.set(self._radar_playback_status())
            self._update_radar_replay_button()

    def _schedule_next_radar_frame(self) -> None:
        if (
            self._radar_playback_index is None
            or self._radar_playback_paused
            or self._closed
        ):
            return
        self._radar_playback_after_id = self.root.after(
            900,
            self._advance_radar_frame,
        )
        self._update_radar_replay_button()

    def _advance_radar_frame(self) -> None:
        self._radar_playback_after_id = None
        if not self._radar_playback_frames or self._closed:
            return
        if self.map_mode != LIVE_MAP or self._radar_playback_product != self.radar_product:
            self._stop_radar_playback(redraw=False)
            return
        count = len(self._radar_playback_frames)
        self._radar_playback_index = (
            (self._radar_playback_index or 0) + 1
        ) % count
        self.radar_product_status.set(self._radar_playback_status())
        self._draw_map()
        self._schedule_next_radar_frame()

    def _request_velocity_radar(self, site: VelocityRadarSite) -> None:
        site_code = site.site.value
        self.radar_product_status.set(f"Velocity · {site.name}: loading…")
        if site_code in self._velocity_loading_sites:
            return
        _terminal_progress("Radar", f"Requesting Doppler velocity for {site.name} ({site_code}).")
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
        _terminal_progress("Refresh", "Starting radar, storm-tracking, and report refresh.")
        self.loop.create_task(self._load_weather_data())
        if self.radar_product == VELOCITY_RADAR and self.velocity_radar_site is not None:
            self._request_velocity_radar(self.velocity_radar_site)

    async def _load_weather_data(self) -> None:
        refresh_started = time_module.perf_counter()
        reports_future = None
        warnings_future = None
        try:
            client = self.supabase
            warnings_future = (
                self.loop.run_in_executor(self.executor, client.active_community_warnings)
                if client is not None else None
            )
            _terminal_progress("Refresh", "Requesting the latest local DWD radar frame.")
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
            if reports_future is not None:
                _terminal_progress("Refresh", "Requesting community reports in parallel.")

            storm_error: BaseException | None = None
            if client is not None:
                try:
                    _terminal_progress("Refresh", "Checking Supabase for the latest storm tracks.")
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
                    _terminal_progress("Refresh", "Fetching storm tracks from the local DWD feed.")
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
            elif not isinstance(storm_result, BaseException):
                _terminal_progress(
                    "Refresh",
                    f"Loaded {len(storm_result.cells)} storm tracks from Supabase.",
                )

            radar_result = await asyncio.gather(radar_future, return_exceptions=True)
            radar_result = radar_result[0]
            errors: list[BaseException] = []
            wind_error: Exception | None = None

            if isinstance(radar_result, BaseException):
                self._record_source_error("DWD reflectivity", radar_result)
                errors.append(radar_result)
                _terminal_progress(
                    "Refresh",
                    f"Radar request failed: {type(radar_result).__name__}: {radar_result}",
                )
            elif radar_result is None:
                self._record_source_error(
                    "DWD reflectivity",
                    "No latest radar frame was returned",
                )
            else:
                radar_frame = radar_result
                self.radar_frame = radar_frame
                if radar_frame.timestamp is not None:
                    self.case_analysis.record_event(
                        "radar_scan",
                        f"reflectivity:{radar_frame.timestamp.isoformat()}",
                        radar_frame.timestamp,
                        "DWD reflectivity volume received",
                        source="DWD radar",
                    )
                self._record_source_success(
                    "DWD reflectivity",
                    observed_at=radar_frame.timestamp,
                )
                _terminal_progress(
                    "Refresh",
                    f"Radar frame received at {self._format_time(radar_frame.timestamp)}; detecting rain echoes.",
                )
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
                    _terminal_progress(
                        "Refresh",
                        f"Rain-echo analysis complete: {len(self.rain_cells)} active cells.",
                    )
                if self.radar_product == REFLECTIVITY_RADAR:
                    self.radar_product_status.set(
                        self._radar_status_text(REFLECTIVITY_RADAR, radar_frame)
                    )

            if isinstance(storm_result, BaseException):
                self._record_source_error("KONRAD3D tracking", storm_result)
                errors.append(storm_result)
                _terminal_progress(
                    "Refresh",
                    f"Storm-track request failed: {type(storm_result).__name__}: {storm_result}",
                )
            elif storm_result is not None:
                self.storm_update = storm_result
                self._record_source_success(
                    "KONRAD3D tracking",
                    observed_at=storm_result.timestamp,
                    extra=f"{len(storm_result.cells)} active cells",
                )
                self._remember_tracks(storm_result.cells)
                try:
                    cells_without_forecast = tuple(
                        cell for cell in storm_result.cells if not cell.forecast
                    )
                    _terminal_progress(
                        "Refresh",
                        f"Projecting motion for {len(cells_without_forecast)} storm cells without forecasts.",
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
                    self._record_source_error("Supabase reports", reports_result)
                    errors.append(reports_result)
                    self._sync_auth_state()
                else:
                    self.community_reports = reports_result
                    self._record_source_success(
                        "Supabase reports",
                        extra=f"{len(reports_result)} reports",
                    )
                    self._render_community_reports()
                    _terminal_progress(
                        "Refresh",
                        f"Loaded {len(reports_result)} community reports.",
                    )
            else:
                self._render_community_reports()

            if warnings_future is not None:
                warnings_result = await asyncio.gather(
                    warnings_future,
                    return_exceptions=True,
                )
                warnings_result = warnings_result[0]
                if isinstance(warnings_result, BaseException):
                    self._record_source_error("Community warnings", warnings_result)
                    _terminal_progress(
                        "Warnings",
                        f"Community warning feed unavailable: {type(warnings_result).__name__}: {warnings_result}",
                    )
                else:
                    self.active_community_warnings = tuple(warnings_result)
                    self._record_source_success(
                        "Community warnings",
                        extra=f"{len(self.active_community_warnings)} active polygons",
                    )
                    for warning in self.active_community_warnings:
                        try:
                            warning_time = datetime.fromisoformat(
                                str(warning.get("created_at", datetime.now(timezone.utc).isoformat())).replace("Z", "+00:00")
                            )
                            self.case_analysis.record_event(
                                "community_warning",
                                str(warning.get("id", "")),
                                warning_time,
                                f"{warning.get('headline', 'Community warning')} · "
                                f"{warning.get('category', 'hazard')} level {warning.get('level', '?')}",
                                source="StormChase community warning",
                            )
                        except (TypeError, ValueError):
                            pass
                    self._populate_admin_warning_list()
            if storm_result is not None and not isinstance(storm_result, BaseException):
                try:
                    await self.loop.run_in_executor(
                        self.executor,
                        self.case_analysis.record_cells,
                        self.storm_update.cells,
                        self.storm_update.timestamp,
                        self.cell_motion_winds,
                    )
                except Exception as error:
                    _terminal_progress(
                        "Case analysis",
                        f"Local case history could not be saved: {type(error).__name__}: {error}",
                    )

            self._record_session_snapshot()
            self._populate_cell_list()
            self._update_alert_status()
            self._refresh_case_analysis_panel()
            self._refresh_admin_controls()
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
                _terminal_progress(
                    "Refresh",
                    "Completed with errors: "
                    + "; ".join(f"{type(error).__name__}: {error}" for error in errors),
                )
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
                _terminal_progress(
                    "Refresh",
                    f"Refresh complete in {time_module.perf_counter() - refresh_started:.1f}s; "
                    f"{len(self.storm_update.cells)} storm cells and {len(self.rain_cells)} rain echoes.",
                )
            self._draw_map()
        except Exception as error:
            _terminal_progress(
                "Refresh",
                f"Refresh failed: {type(error).__name__}: {error}",
            )
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

    def _record_source_success(
        self,
        name: str,
        observed_at: datetime | None = None,
        extra: str | None = None,
        *,
        refresh: bool = True,
    ) -> None:
        self._source_last_success_at[name] = datetime.now(timezone.utc)
        if observed_at is not None:
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)
            self._source_observed_at[name] = observed_at.astimezone(timezone.utc)
        self._source_errors.pop(name, None)
        self._source_extra[name] = extra or ""
        if refresh:
            self._refresh_diagnostics()

    def _record_source_error(self, name: str, error: BaseException | str) -> None:
        if isinstance(error, BaseException):
            detail = f"{type(error).__name__}: {error}"
        else:
            detail = str(error)
        self._source_errors[name] = detail
        self._refresh_diagnostics()

    @staticmethod
    def _format_age(value: datetime, now: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        seconds = max(0, int((now - value.astimezone(timezone.utc)).total_seconds()))
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 48:
            return f"{hours}h {minutes % 60}m ago"
        return f"{hours // 24}d {hours % 24}h ago"

    def _record_session_snapshot(self) -> None:
        self._session_storm_ids.update(
            str(cell.identifier) for cell in self.storm_update.cells
        )
        self._session_rain_ids.update(
            str(cell.identifier) for cell in self.rain_cells
        )
        self._session_peak_storm_cells = max(
            self._session_peak_storm_cells,
            len(self.storm_update.cells),
        )
        self._session_peak_rain_echoes = max(
            self._session_peak_rain_echoes,
            len(self.rain_cells),
        )
        self._session_refresh_cycles += 1
        self._refresh_diagnostics()

    def _refresh_diagnostics(self) -> None:
        if not hasattr(self, "diagnostics_text"):
            return

        now = datetime.now(timezone.utc)
        freshness_limits = {
            "DWD reflectivity": timedelta(minutes=12),
            "Community warnings": timedelta(minutes=15),
            "KONRAD3D tracking": timedelta(minutes=12),
            "DWD velocity": timedelta(minutes=12),
            "DWD mesocyclone detections": timedelta(minutes=12),
            "Blitzortung lightning": timedelta(minutes=15),
            "ICON-D2 outlook": timedelta(hours=30),
            "Supabase reports": None,
        }
        lines = ["DATA SOURCES"]
        stale_core_sources: list[str] = []
        core_names = ("DWD reflectivity", "KONRAD3D tracking")

        for name, stale_after in freshness_limits.items():
            observed_at = self._source_observed_at.get(name)
            received_at = self._source_last_success_at.get(name)
            reference_time = observed_at or received_at
            error = self._source_errors.get(name)
            extra = self._source_extra.get(name, "")

            if reference_time is None:
                state = "unavailable" if error else "not received yet"
            else:
                age_text = self._format_age(reference_time, now)
                age_seconds = max(
                    0,
                    (now - reference_time.astimezone(timezone.utc)).total_seconds(),
                )
                if stale_after is not None and age_seconds > stale_after.total_seconds():
                    state = f"STALE / cached · {age_text}"
                    if name in core_names:
                        stale_core_sources.append(name)
                elif stale_after is None:
                    state = f"loaded · {age_text}"
                else:
                    state = f"fresh · {age_text}"

            if name in core_names and reference_time is None:
                stale_core_sources.append(name)
            if extra:
                state += f" · {extra}"
            if error:
                state += f" · last attempt failed: {error}"
            lines.append(f"{name}: {state}")

        if not stale_core_sources:
            core_state = "Core observations are within the 12-minute freshness window."
        elif all(
            self._source_observed_at.get(name) is None
            for name in core_names
        ):
            core_state = "No core observations have been loaded yet."
        else:
            core_state = (
                "Showing the last successful core observations where available; "
                "one or more feeds are stale or unavailable."
            )

        elapsed = max(
            0,
            int((now - self._session_started_at).total_seconds()),
        )
        hours, remainder = divmod(elapsed, 3600)
        minutes = remainder // 60
        lines.extend(
            (
                "",
                "DATA MODE",
                core_state,
                "A stale timestamp means old/cached observations; it does not by itself prove a network outage.",
                "",
                "THIS SESSION",
                f"Runtime: {hours}h {minutes}m",
                f"Refresh cycles completed: {self._session_refresh_cycles}",
                f"Unique KONRAD3D cells seen: {len(self._session_storm_ids)}",
                f"Unique rain echoes seen: {len(self._session_rain_ids)}",
                f"Unique lightning strokes received: {len(self._session_lightning_ids)}",
                f"Peak simultaneous cells / rain echoes: {self._session_peak_storm_cells} / {self._session_peak_rain_echoes}",
            )
        )

        self.diagnostics_text.configure(state="normal")
        self.diagnostics_text.delete("1.0", tk.END)
        self.diagnostics_text.insert("1.0", "\n".join(lines))
        self.diagnostics_text.configure(state="disabled")

    def copy_selected_cell_summary(self) -> None:
        cell = self.selected_cell
        if cell is None:
            messagebox.showinfo(
                "Select a cell",
                "Select a storm cell or rain echo first.",
                parent=self.root,
            )
            return

        lines = [
            f"StormChase cell summary · {cell.identifier}",
            f"Position: {cell.latitude:.4f}° N, {cell.longitude:.4f}° E",
            f"Observed data time: {self._format_time(self.storm_update.timestamp)}",
        ]
        if isinstance(cell, RainCell):
            lines.extend(
                (
                    "Type: 20–30 dBZ rain echo",
                    f"Maximum reflectivity: {cell.max_reflectivity_dbz:.1f} dBZ",
                    f"Area: {cell.area_pixels} radar pixels",
                    f"Track points: {len(cell.track)}",
                )
            )
        else:
            lines.append("Type: KONRAD3D convective cell")
            if cell.reflectivity_dbz is not None:
                lines.append(f"Reflectivity: {cell.reflectivity_dbz:.1f} dBZ")
            if cell.severity:
                lines.append(f"Severity: {cell.severity}")
            history = self.track_history.get(cell.identifier, [])
            track_points = max(len(cell.track), len(history))
            lines.append(f"Track points: {track_points}")
            if history:
                lines.append(
                    f"First retained position: {history[0][0]:.4f}, {history[0][1]:.4f}"
                )
                lines.append(
                    f"Latest retained position: {history[-1][0]:.4f}, {history[-1][1]:.4f}"
                )

        summary = "\n".join(lines)
        self.root.clipboard_clear()
        self.root.clipboard_append(summary)
        self.root.update_idletasks()
        self.status.set(f"Copied {cell.identifier} summary to the clipboard.")

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
            self._record_source_error("DWD velocity", error)
            _terminal_progress(
                "Radar",
                f"Velocity request for {site.name} failed: {type(error).__name__}: {error}",
            )
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
            except Exception as error:
                # Keep the velocity image available when the optional volume
                # mesocyclone product is delayed or unavailable.
                self._record_source_error("DWD mesocyclone detections", error)
                rotations = ()
            else:
                if frame is not None:
                    self._record_source_success(
                        "DWD mesocyclone detections",
                        extra=f"{len(rotations)} detections",
                    )
            if frame is None:
                self._record_source_error(
                    "DWD velocity",
                    f"No velocity frame returned for {site.name}",
                )
                _terminal_progress("Radar", f"No velocity frame is currently available for {site.name}.")
            else:
                self._record_source_success(
                    "DWD velocity",
                    observed_at=frame.timestamp,
                    extra=site.name,
                )
                _terminal_progress(
                    "Radar",
                    f"Velocity frame loaded for {site.name}; found {len(rotations)} supported rotation detections.",
                )
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
            _terminal_progress("Account", "Restoring the saved reporter session.")
            user = await self.loop.run_in_executor(
                self.executor,
                client.restore_session,
            )
        except Exception as error:
            _terminal_progress(
                "Account",
                f"Session restore failed: {type(error).__name__}: {error}",
            )
            self.status.set(
                f"Could not restore reporter identity: {type(error).__name__}: {error}"
            )
            self.auth_user = None
            restore_warning = f"Identity restore failed: {type(error).__name__}"
        else:
            self.auth_user = user
            _terminal_progress(
                "Account",
                f"Session restore complete ({user.username or 'no reporter name'}).",
            )
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
        self._is_stormchase_admin = False
        if self.auth_user is not None and client.current_user is not None:
            try:
                self._is_stormchase_admin = await self.loop.run_in_executor(
                    self.executor,
                    client.is_stormchase_admin,
                )
            except Exception as error:
                self._is_stormchase_admin = False
                _terminal_progress(
                    "Admin",
                    f"Administrator authorization check unavailable: {type(error).__name__}: {error}",
                )
        self._refresh_admin_controls()
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
        if hasattr(self, "copy_cell_button"):
            self.copy_cell_button.configure(
                state="normal" if self.selected_cell is not None else "disabled"
            )
        self._refresh_admin_controls()

    def _sync_auth_state(self) -> None:
        if (
            self.auth_user is not None
            and self.supabase is not None
            and self.supabase.current_user is None
        ):
            self.auth_user = None
            self._is_stormchase_admin = False
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

        save_button = AppButton(
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
        save_button: AppButton,
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
        self._is_stormchase_admin = False
        try:
            self._is_stormchase_admin = await self.loop.run_in_executor(
                self.executor,
                client.is_stormchase_admin,
            )
        except Exception as error:
            _terminal_progress(
                "Admin",
                f"Administrator authorization check unavailable: {type(error).__name__}: {error}",
            )
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
                verification = report.get("verification_status", "unverified")
                review_note = report.get("review_note")
                review_suffix = f"\nModerator note: {review_note}" if review_note else ""
                entries.append(f"{username} · {created_at} · {cell_id} · {verification.upper()}\n{body}{review_suffix}")
            content = "\n\n".join(entries)
        self.community_report_text.configure(state="normal")
        self.community_report_text.delete("1.0", tk.END)
        self.community_report_text.insert("1.0", content)
        self.community_report_text.configure(state="disabled")
        self._populate_admin_report_list()

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
        self._refresh_diagnostics()
        self.root.after(60_000, self._expire_lightning_strikes)

    def _on_lightning_strike(self, strike: LightningStrike) -> None:
        if self._closed:
            return
        self.lightning_strikes[strike.identifier] = strike
        self.case_analysis.record_event(
            "lightning",
            str(strike.identifier),
            strike.timestamp,
            f"Community lightning stroke · {strike.latitude:.3f}, {strike.longitude:.3f}",
            latitude=strike.latitude,
            longitude=strike.longitude,
            source="Blitzortung community feed",
        )
        self._session_lightning_ids.add(str(strike.identifier))
        self._record_source_success(
            "Blitzortung lightning",
            observed_at=strike.timestamp,
            extra=f"{len(self.lightning_strikes)} strokes in the rolling 15-minute window",
            refresh=False,
        )
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
        if len(self.lightning_strikes) == 1 or len(self.lightning_strikes) % 10 == 0:
            _terminal_progress(
                "Lightning",
                f"Received a strike at {strike.latitude:.3f}, {strike.longitude:.3f}; "
                f"{len(self.lightning_strikes)} recent strokes tracked.",
            )
        self._draw_map()

    def _on_lightning_status(self, status: str) -> None:
        self.lightning_status.set(status)
        _terminal_progress("Lightning", status)

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
        if self._outlook_metric in GENERAL_WEATHER_MAP_SCALES:
            _field, minimum, maximum, unit = GENERAL_WEATHER_MAP_SCALES[
                self._outlook_metric
            ]
            levels = np.linspace(minimum, maximum, len(OUTLOOK_COLORS) + 1)
            labels = [
                f"{levels[index]:.0f}–<{levels[index + 1]:.0f} {unit}".strip()
                for index in range(len(levels) - 2)
            ]
            labels.append(f"{levels[-2]:.0f}–{maximum:.0f} {unit}".strip())
            labels.append("No forecast data")
            colors = (*OUTLOOK_COLORS, "#ffffff")
            title = f"{self.outlook_day_var.get()} · General weather · {self._outlook_metric}"
        elif self._outlook_metric in OUTLOOK_PARAMETER_SCALES:
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
        elif self._outlook_metric in GENERAL_WEATHER_MAP_SCALES:
            handles[-1].set_hatch("///")
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
        draw_started = time_module.perf_counter()
        draw_label = self.map_mode
        if self.map_mode == STORMANALYZER_MAP:
            draw_label = (
                f"{self.outlook_day_var.get()} / {self._outlook_metric} outlook"
            )
        log_map_progress = (
            self.map_mode == STORMANALYZER_MAP
            or draw_started - self._last_map_progress_at >= 2.0
        )
        if log_map_progress:
            self._last_map_progress_at = draw_started
            _terminal_progress("Map", f"Building {draw_label} layers.")
        self.figure.clear()
        self.axes = self.figure.add_subplot(111)
        outlook_mode = self.map_mode == STORMANALYZER_MAP
        radar_replay_active = (
            self.map_mode == LIVE_MAP
            and self._radar_playback_index is not None
            and self._radar_playback_product == self.radar_product
        )
        live_observation_overlays = (
            self.map_mode == LIVE_MAP and not radar_replay_active
        )
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

        if not outlook_mode:
            for warning in self.active_community_warnings:
                category = warning.get("category")
                level = warning.get("level")
                if category not in WARNING_COLORS:
                    continue
                try:
                    expiry = datetime.fromisoformat(
                        str(warning.get("expires_at", "")).replace("Z", "+00:00")
                    )
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    if expiry <= datetime.now(timezone.utc):
                        continue
                    vertices = warning.get("polygon")
                    if not isinstance(vertices, list) or len(vertices) < 4:
                        continue
                    longitudes = [float(point[0]) for point in vertices]
                    latitudes = [float(point[1]) for point in vertices]
                    color = WARNING_COLORS[category][int(level) - 1]
                except (TypeError, ValueError, IndexError, KeyError):
                    continue
                self.axes.fill(
                    longitudes,
                    latitudes,
                    facecolor=color,
                    edgecolor=color,
                    alpha=0.16,
                    linewidth=1.8,
                    zorder=3,
                )
                self.axes.plot(
                    longitudes,
                    latitudes,
                    color=color,
                    linewidth=1.8,
                    linestyle="-",
                    alpha=0.95,
                    zorder=3,
                    label=(
                        f"Community warning · {WARNING_CATEGORY_LABELS[category]} L{level}"
                    ),
                )
                self.axes.text(
                    longitudes[0],
                    latitudes[0],
                    f"UNOFFICIAL · {warning.get('headline', 'Community warning')}",
                    color="white",
                    fontsize=7,
                    zorder=4,
                    path_effects=[
                        path_effects.withStroke(linewidth=2.4, foreground="#101820")
                    ],
                )
            if self._warning_polygon_draft:
                draft_lon = [point[0] for point in self._warning_polygon_draft]
                draft_lat = [point[1] for point in self._warning_polygon_draft]
                self.axes.plot(
                    draft_lon,
                    draft_lat,
                    color="#ffffff",
                    linewidth=2.0,
                    linestyle="--",
                    marker="o",
                    markersize=4,
                    label="Warning polygon draft",
                    zorder=14,
                )
                if len(self._warning_polygon_draft) >= 3:
                    self.axes.plot(
                        [draft_lon[-1], draft_lon[0]],
                        [draft_lat[-1], draft_lat[0]],
                        color="#ffffff",
                        linewidth=1.3,
                        linestyle=":",
                        alpha=0.8,
                        zorder=14,
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

        has_general_weather_map = (
            self._outlook_metric in GENERAL_WEATHER_MAP_SCALES
            and bool(self._general_weather_outlook)
        )
        if outlook_mode and (self._storm_outlook or has_general_weather_map):
            day = self._outlook_day
            general_weather_scale = GENERAL_WEATHER_MAP_SCALES.get(self._outlook_metric)
            general_weather_metric = general_weather_scale is not None
            source_grid = (
                self._general_weather_outlook
                if general_weather_metric
                else self._storm_outlook
            )
            entries = [entry for entry in source_grid if entry["day"] == day]
            metric_key = {
                "Overall risk": "risk",
                "Tornado": "tornado",
                "Hail": "hail",
                "Wind": "wind",
                "STP": "stp",
                "SCP": "scp",
            }.get(self._outlook_metric)
            parameter_minimum = (
                general_weather_scale[1] if general_weather_scale is not None else 0.0
            )
            parameter_scale = (
                general_weather_scale[2]
                if general_weather_scale is not None
                else OUTLOOK_PARAMETER_SCALES.get(self._outlook_metric)
            )
            parameter_values: list[float] = []
            weather_grid_points: list[tuple[float, float]] | None = None
            if parameter_scale is not None:
                available_entries = []
                for entry in entries:
                    source = (
                        entry.get("general_weather")
                        if general_weather_metric
                        else entry.get("environment")
                    )
                    if not isinstance(source, dict):
                        continue
                    value_key = general_weather_scale[0] if general_weather_scale else metric_key
                    value = source.get(value_key)
                    try:
                        numeric_value = float(value)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(numeric_value):
                        available_entries.append(entry)
                        parameter_values.append(
                            float(np.clip(numeric_value, parameter_minimum, parameter_scale))
                        )
                entries = available_entries
            if general_weather_metric and entries:
                source_points = [
                    (entry["latitude"], entry["longitude"])
                    for entry in entries
                ]
                target_points = germany_grid_points(self.germany_outline)
                weather_grid_points = target_points
                known_locations = len(set(source_points))
                missing_count = max(0, len(target_points) - known_locations)
                _terminal_progress(
                    "Map",
                    f"Matching {len(source_points)} valid source values to the "
                    f"{len(target_points)} map cells; filling {missing_count} gaps "
                    "from the nearest valid grid points.",
                )
                parameter_values = _fill_grid_gaps_from_neighbors(
                    target_points,
                    source_points,
                    parameter_values,
                ).tolist()
                entries = [
                    {"latitude": latitude, "longitude": longitude}
                    for latitude, longitude in target_points
                ]
            _terminal_progress(
                "Map",
                f"Preparing {len(entries)} valid grid values for {self._outlook_metric}.",
            )
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
                triangulation = (
                    Triangulation(longitudes, latitudes)
                    if len(entries) >= 3 and not general_weather_metric
                    else None
                )

                if parameter_scale is not None:
                    score_levels = tuple(
                        np.linspace(
                            parameter_minimum,
                            parameter_scale,
                            len(OUTLOOK_COLORS) + 1,
                        )
                    )
                    scores = np.minimum(
                        scores,
                        np.nextafter(parameter_scale, parameter_minimum),
                    )
                    level_colors = OUTLOOK_COLORS
                    contour_options: dict[str, Any] = {}
                    minimum_to_render = (
                        parameter_minimum if general_weather_metric else score_levels[1]
                    )
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
                    and (
                        general_weather_metric
                        or triangulation is not None
                    )
                ):
                    if general_weather_metric:
                        grid_points = weather_grid_points or germany_grid_points(
                            self.germany_outline
                        )
                        grid_latitudes = sorted({latitude for latitude, _ in grid_points})
                        grid_longitudes = sorted({longitude for _, longitude in grid_points})
                        weather_grid = scores.reshape(
                            len(grid_latitudes),
                            len(grid_longitudes),
                        )
                        self.axes.contourf(
                            grid_longitudes,
                            grid_latitudes,
                            weather_grid,
                            levels=score_levels,
                            colors=level_colors,
                            corner_mask=False,
                            alpha=0.9,
                            zorder=3,
                        )
                    else:
                        refined_triangulation, refined_scores = UniformTriRefiner(
                            triangulation
                        ).refine_field(scores, subdiv=4)
                        self.axes.tricontourf(
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
            self._radar_playback_frames[self._radar_playback_index]
            if (
                self._radar_playback_index is not None
                and self._radar_playback_frames
                and self._radar_playback_product == self.radar_product
            )
            else (
                self.velocity_radar_frame
                if self.radar_product == VELOCITY_RADAR
                else self.radar_frame
            )
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

        if (
            self.map_mode == LIVE_MAP
            and self.radar_product == VELOCITY_RADAR
            and self.velocity_radar_site is not None
            and not radar_replay_active
        ):
            frame = self.velocity_radar_frame
            site = self.velocity_radar_site
            elevation = frame.elevation_angle_degrees if frame is not None else None
            radar_latitude = (
                frame.site_latitude
                if frame is not None and frame.site_latitude is not None
                else site.latitude
            )
            radar_longitude = (
                frame.site_longitude
                if frame is not None and frame.site_longitude is not None
                else site.longitude
            )
            if elevation is not None:
                for beam_height_km in (0.5, 1.0, 2.0, 3.0):
                    try:
                        radius_km = beam_height_contour_range_km(
                            beam_height_km,
                            elevation,
                        )
                    except ValueError:
                        continue
                    if radius_km > site.max_range_km:
                        continue
                    latitude_scale_km = 111.32
                    longitude_scale_km = max(
                        111.32 * abs(math.cos(math.radians(radar_latitude))),
                        1.0,
                    )
                    ring = Ellipse(
                        (radar_longitude, radar_latitude),
                        width=2.0 * radius_km / longitude_scale_km,
                        height=2.0 * radius_km / latitude_scale_km,
                        fill=False,
                        edgecolor="#d6e7f0",
                        linewidth=0.9,
                        linestyle=(0, (4, 3)),
                        alpha=0.72,
                        zorder=8,
                        label=(
                            f"Beam centre +{beam_height_km:g} km "
                            "(4/3-Earth approximation)"
                            if beam_height_km == 0.5 else "_nolegend_"
                        ),
                    )
                    self.axes.add_patch(ring)
                    self.axes.text(
                        radar_longitude,
                        radar_latitude + radius_km / latitude_scale_km,
                        f"{beam_height_km:g} km",
                        color="#e7f2f7",
                        fontsize=7,
                        ha="center",
                        va="bottom",
                        zorder=9,
                        path_effects=[
                            path_effects.withStroke(linewidth=2.0, foreground="#101820")
                        ],
                    )
            else:
                _terminal_progress(
                    "Radar",
                    "Beam-height overlay unavailable: ODIM elevation-angle metadata is missing.",
                )

        motion_legend_drawn = False
        for cell in self.storm_update.cells if live_observation_overlays else ():
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

        if live_observation_overlays and self.storm_update.cells:
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

        for cell in self.rain_cells if live_observation_overlays else ():
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

        if live_observation_overlays and self.rain_cells:
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
            and not radar_replay_active
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

        if not outlook_mode and not radar_replay_active and self.lightning_strikes:
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
            or (self.lightning_strikes and not radar_replay_active)
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

        if self._measurement_points:
            latitudes = [point[0] for point in self._measurement_points]
            longitudes = [point[1] for point in self._measurement_points]
            self.axes.plot(
                longitudes,
                latitudes,
                color="#52e4c8",
                linewidth=2.0,
                linestyle="--",
                zorder=20,
            )
            self.axes.scatter(
                longitudes,
                latitudes,
                color="#52e4c8",
                edgecolors="#102027",
                linewidths=1.0,
                s=54,
                zorder=21,
            )
            if len(self._measurement_points) == 2:
                first, second = self._measurement_points
                distance = self._distance_km(first, second)
                midpoint = (
                    (first[1] + second[1]) / 2,
                    (first[0] + second[0]) / 2,
                )
                self.axes.annotate(
                    f"{distance:.1f} km",
                    midpoint,
                    xytext=(7, 7),
                    textcoords="offset points",
                    color="white",
                    fontsize=9,
                    fontweight="bold",
                    bbox={
                        "facecolor": "#102027",
                        "edgecolor": "#52e4c8",
                        "alpha": 0.92,
                        "boxstyle": "round,pad=0.35",
                    },
                    zorder=22,
                )

        attribution = "" if outlook_mode else ESRI_ATTRIBUTION
        if self.map_mode == DWD_WARNING_MAP:
            attribution = f"Warning data © DWD · {ESRI_ATTRIBUTION}"
        if not outlook_mode and not radar_replay_active and self.lightning_strikes:
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
        self._map_draw_started_at = draw_started if log_map_progress else None
        self._map_draw_label = draw_label if log_map_progress else None
        if log_map_progress:
            _terminal_progress("Map", f"Layers assembled for {draw_label}; painting the canvas.")
        self.map_canvas.draw_idle()
        if not outlook_mode:
            self._schedule_basemap_refresh()
        if self.map_mode == DWD_WARNING_MAP:
            self._schedule_warning_refresh()

    def _on_map_draw_complete(self, _event: Any) -> None:
        if self._map_draw_started_at is None or self._map_draw_label is None:
            return
        elapsed = time_module.perf_counter() - self._map_draw_started_at
        _terminal_progress(
            "Map",
            f"Canvas paint finished for {self._map_draw_label} in {elapsed:.2f}s.",
        )
        self._map_draw_started_at = None
        self._map_draw_label = None

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
        if self.map_mode != LIVE_MAP:
            self._stop_radar_playback(redraw=False)
        self._update_radar_replay_button()
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
            _terminal_progress("Outlook", "A load is already running; keeping it in progress.")
            return
        self._storm_outlook_loading = True
        self._storm_outlook_error = None
        self._update_outlook_load_button()
        point_count = len(germany_grid_points(self.germany_outline))
        _terminal_progress(
            "Outlook",
            f"Starting grid load for {point_count} locations using "
            f"{'the local ICON-D2 feed' if self._outlook_use_local_feed else 'the shared database feed'}.",
        )
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
        _terminal_progress(
            "Outlook",
            "Switching to the local ICON-D2 feed."
            if self._outlook_use_local_feed
            else "Switching to the shared database ICON-D2 feed.",
        )
        self._ensure_storm_outlook()

    async def _load_storm_outlook(self) -> None:
        load_started = time_module.perf_counter()
        general_weather_outlook: list[dict[str, Any]] = []
        try:
            outlook, issued_at = await self.loop.run_in_executor(
                self.executor,
                calculate_germany_outlook,
                self.germany_outline,
                self.supabase,
                not self._outlook_use_local_feed,
                general_weather_outlook,
            )
        except Exception as error:
            self._record_source_error("ICON-D2 outlook", error)
            self._storm_outlook_loading = False
            self._storm_outlook_error = f"{type(error).__name__}: {error}"
            _terminal_progress("Outlook", f"Load failed: {self._storm_outlook_error}")
            self._update_outlook_load_button()
            self.status.set(f"StormAnalyzer Germany outlook unavailable: {self._storm_outlook_error}")
            if self._outlook_location is not None:
                self.outlook_calculation_text.set(
                    f"StormAnalyzer outlook calculation unavailable: "
                    f"{self._storm_outlook_error}"
                )
                self.general_weather_text.set(
                    f"General-weather outlook unavailable: {self._storm_outlook_error}"
                )
            if self.map_mode == STORMANALYZER_MAP:
                self._draw_map()
            return
        self._storm_outlook = outlook
        self._general_weather_outlook = general_weather_outlook
        self._storm_outlook_loaded_at = issued_at
        self._record_source_success(
            "ICON-D2 outlook",
            observed_at=issued_at,
            extra=f"{len(outlook)} StormAnalyzer results",
        )
        self._storm_outlook_loading = False
        self._storm_outlook_error = None
        _terminal_progress(
            "Outlook",
            f"Loaded {len(outlook)} forecast results in "
            f"{time_module.perf_counter() - load_started:.1f}s; issued {issued_at.isoformat()}.",
        )
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
        submit_button = AppButton(
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
        submit_button: AppButton,
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
        submit_button: AppButton,
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
            _terminal_progress(
                "Warnings",
                f"Requesting a DWD warning overlay for {width}×{height} map pixels.",
            )
            image, image_extent = await self.loop.run_in_executor(
                self.executor,
                fetch_dwd_warning_overlay,
                extent,
                width,
                height,
            )
        except Exception as error:
            _terminal_progress(
                "Warnings",
                f"Overlay request failed: {type(error).__name__}: {error}",
            )
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
        _terminal_progress("Warnings", "Overlay received; redrawing the map.")
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
            _terminal_progress(
                "Basemap",
                f"Requesting Esri imagery ({width}×{height}) for the current extent.",
            )
            image, image_extent = await self.loop.run_in_executor(
                self.executor,
                fetch_esri_imagery,
                extent,
                width,
                height,
            )
        except Exception as error:
            _terminal_progress(
                "Basemap",
                f"Imagery request failed: {type(error).__name__}: {error}",
            )
            if not self._closed and key == self._basemap_requested_key:
                self.status.set(
                    f"Esri World Imagery unavailable: {type(error).__name__}: {error}"
                )
            return
        if self._closed or key != self._basemap_requested_key:
            return
        self._basemap_image = image
        self._basemap_extent = image_extent
        _terminal_progress("Basemap", "Imagery received and cached for the current extent.")
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
        self._refresh_case_analysis_panel()
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

    def _toggle_measurement(self) -> None:
        if self._measurement_enabled:
            self._measurement_enabled = False
            self.measure_status.set("Measurement cancelled")
            self.measure_button.configure(text="Measure distance")
        else:
            self._measurement_points.clear()
            self._measurement_enabled = True
            self.measure_status.set("Click the first point on the map")
            self.measure_button.configure(text="Cancel measure")
        self._draw_map()

    @staticmethod
    def _distance_km(
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> float:
        """Calculate great-circle distance between (latitude, longitude) pairs."""
        latitude_1, longitude_1 = map(math.radians, first)
        latitude_2, longitude_2 = map(math.radians, second)
        latitude_delta = latitude_2 - latitude_1
        longitude_delta = longitude_2 - longitude_1
        haversine = (
            math.sin(latitude_delta / 2) ** 2
            + math.cos(latitude_1)
            * math.cos(latitude_2)
            * math.sin(longitude_delta / 2) ** 2
        )
        return 6371.0088 * 2 * math.asin(math.sqrt(min(1.0, haversine)))

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
        if self._warning_drawing_enabled:
            if self.map_mode == STORMANALYZER_MAP:
                self.status.set("Switch away from the outlook map to draw a warning polygon.")
                return
            self._warning_polygon_draft.append([longitude, latitude])
            self.admin_polygon_status.set(
                f"Polygon vertices: {len(self._warning_polygon_draft)} · "
                "click more map points, then publish"
            )
            self._draw_map()
            return
        if self._measurement_enabled:
            self._measurement_points.append((latitude, longitude))
            if len(self._measurement_points) == 1:
                self.measure_status.set(
                    f"Start: {latitude:.4f}, {longitude:.4f} · choose endpoint"
                )
            else:
                first, second = self._measurement_points
                distance = self._distance_km(first, second)
                self.measure_status.set(f"Distance: {distance:.1f} km")
                self._measurement_enabled = False
                self.measure_button.configure(text="Measure distance")
            self._draw_map()
            return
        _terminal_progress(
            "Map",
            f"Selected map point {latitude:.4f}, {longitude:.4f} in {self.map_mode}.",
        )
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
            _terminal_progress(
                "Nowcast",
                f"Requesting point forecast for {latitude:.3f}, {longitude:.3f}.",
            )
            model, payload = await self.loop.run_in_executor(
                self.executor,
                fetch_openmeteo_nowcast,
                latitude,
                longitude,
            )
        except Exception as error:
            _terminal_progress(
                "Nowcast",
                f"Point forecast failed: {type(error).__name__}: {error}",
            )
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
        _terminal_progress("Nowcast", f"Point forecast loaded using {model}.")

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
            _terminal_progress(
                "Warnings",
                f"Checking active DWD warnings at {latitude:.3f}, {longitude:.3f}.",
            )
            features = await self.loop.run_in_executor(
                self.executor,
                fetch_dwd_warning_at,
                latitude,
                longitude,
            )
        except Exception as error:
            _terminal_progress(
                "Warnings",
                f"Point warning lookup failed: {type(error).__name__}: {error}",
            )
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
            _terminal_progress("Warnings", "No active warning covers the selected point.")
            self.warning_detail.set(
                f"No active DWD municipality warning at {latitude:.4f}° N, "
                f"{longitude:.4f}° E."
            )
            return
        _terminal_progress("Warnings", f"Found {len(features)} warning feature(s) at the selected point.")
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
        if self._measurement_enabled or not event.ind:
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
            _terminal_progress("Location", "Requesting device-location permission.")
            permitted = await request_location_access()
            if not permitted:
                raise PermissionError(
                    "Windows did not grant location access. Check Windows Location "
                    "privacy settings or enter coordinates manually."
                )
            self._geolocator = create_geolocator()
            self._subscribe_to_location_updates()
            self.location_status.set("Waiting for device location…")
            _terminal_progress("Location", "Permission granted; waiting for a device fix.")
            await self._poll_device_location()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _terminal_progress(
                "Location",
                f"Location unavailable: {type(error).__name__}: {error}",
            )
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
        if initial:
            _terminal_progress(
                "Location",
                f"First device fix: {location.latitude:.4f}, {location.longitude:.4f} "
                f"(±{location.accuracy_meters:.0f} m).",
            )
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
        _terminal_progress("Location", "Stopping device-location tracking.")
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
        _terminal_progress("App", "Stopping background tasks and closing StormChase.")
        self._stop_radar_playback(redraw=False)
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
