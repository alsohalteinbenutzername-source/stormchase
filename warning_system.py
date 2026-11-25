from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any


@dataclass(frozen=True)
class WarningLevel:
    level: int
    tag: str
    title: str
    description: str
    threshold: str | None = None


WARNING_LEVELS: dict[str, tuple[WarningLevel, ...]] = {
    "damaging_wind": (
        WarningLevel(1, "BASE", "Severe damaging wind", "NWS base severe-thunderstorm wind criterion.", "≥58 mph / 93 km/h"),
        WarningLevel(2, "CONSIDERABLE", "Considerable wind damage threat", "NWS considerable damage-threat tag.", "≥70 mph / 113 km/h"),
        WarningLevel(3, "DESTRUCTIVE", "Destructive wind damage threat", "NWS destructive damage-threat tag.", "≥80 mph / 129 km/h"),
    ),
    "tornado": (
        WarningLevel(1, "BASE", "Tornado warning", "Radar-indicated or reliably reported tornado/developing tornado."),
        WarningLevel(2, "CONSIDERABLE / PDS", "Considerable tornado threat", "Rare PDS-level situation: strong, ongoing tornado and considerable threat to life and property."),
        WarningLevel(3, "CATASTROPHIC / TORNADO EMERGENCY", "Catastrophic tornado emergency", "Exceedingly rare: violent tornado, catastrophic damage imminent/ongoing, and reliable confirmation."),
    ),
    "hail": (
        WarningLevel(1, "BASE", "Severe hail", "NWS base severe-thunderstorm hail criterion.", "≥1.00 in / 25 mm"),
        WarningLevel(2, "CONSIDERABLE", "Considerable hail damage threat", "NWS considerable damage-threat tag.", "≥1.75 in / 44 mm"),
        WarningLevel(3, "DESTRUCTIVE", "Destructive hail damage threat", "NWS destructive damage-threat tag.", "≥2.75 in / 70 mm"),
    ),
    "heavy_rain": (
        WarningLevel(1, "DWD LEVEL 2", "Heavy rain · Markante Wetterwarnung", "DWD Starkregen: 15–25 mm in 1 h or 20–35 mm in 6 h.", "15–25 mm/1 h or 20–35 mm/6 h"),
        WarningLevel(2, "DWD LEVEL 3", "Very heavy rain · Unwetterwarnung", "DWD heftiger Starkregen: >25–40 mm in 1 h or >35–60 mm in 6 h.", ">25–40 mm/1 h or >35–60 mm/6 h"),
        WarningLevel(3, "DWD LEVEL 4", "Extreme heavy rain · Extremes Unwetter", "DWD extrem heftiger Starkregen: >40 mm in 1 h or >60 mm in 6 h.", ">40 mm/1 h or >60 mm/6 h"),
    ),
}

WARNING_COLORS = {
    "damaging_wind": ("#eab308", "#f97316", "#dc2626"),
    "tornado": ("#ef4444", "#b91c1c", "#7f1d1d"),
    "hail": ("#38bdf8", "#2563eb", "#7c3aed"),
    "heavy_rain": ("#f59e0b", "#ea580c", "#991b1b"),
}

WARNING_CATEGORY_LABELS = {
    "damaging_wind": "Damaging wind",
    "tornado": "Tornado",
    "hail": "Hail",
    "heavy_rain": "Heavy rain",
}


def warning_level(category: str, level: int) -> WarningLevel:
    if category not in WARNING_LEVELS:
        raise ValueError(f"Unsupported warning category: {category}")
    if isinstance(level, bool) or not isinstance(level, int) or level not in (1, 2, 3):
        raise ValueError("Warning level must be 1, 2, or 3.")
    return WARNING_LEVELS[category][level - 1]


def validate_polygon(points: Any) -> list[list[float]]:
    if not isinstance(points, (list, tuple)) or not 3 <= len(points) <= 250:
        raise ValueError("A warning polygon needs between 3 and 250 vertices.")
    ring: list[list[float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Each polygon vertex must be [longitude, latitude].")
        longitude, latitude = float(point[0]), float(point[1])
        if not math.isfinite(longitude) or not math.isfinite(latitude):
            raise ValueError("Polygon coordinates must be finite.")
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ValueError("Polygon coordinates are outside valid geographic ranges.")
        if not ring or (longitude, latitude) != tuple(ring[-1]):
            ring.append([longitude, latitude])
    if len(ring) < 3:
        raise ValueError("A warning polygon needs at least 3 distinct vertices.")
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    if len(ring) < 4:
        raise ValueError("A warning polygon needs at least 3 distinct vertices.")
    area_twice = sum(
        ring[index][0] * ring[index + 1][1] - ring[index + 1][0] * ring[index][1]
        for index in range(len(ring) - 1)
    )
    if abs(area_twice) < 1e-8:
        raise ValueError("The polygon area is too small or its vertices are collinear.")
    return ring


def validate_warning_payload(
    category: str,
    level: int,
    polygon: Any,
    headline: str,
    description: str,
    expires_at: datetime,
    *,
    catastrophic_confirmed: bool = False,
) -> dict[str, Any]:
    tier = warning_level(category, level)
    title = headline.strip()
    body = description.strip()
    if not title or len(title) > 120:
        raise ValueError("Headline must contain 1–120 characters.")
    if not body or len(body) > 2000:
        raise ValueError("Description must contain 1–2000 characters.")
    if category == "tornado" and level == 3 and not catastrophic_confirmed:
        raise ValueError(
            "Catastrophic tornado warnings require an explicit reliable-confirmation acknowledgement."
        )
    now = datetime.now(timezone.utc)
    expiry = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at.astimezone(timezone.utc)
    if expiry <= now + timedelta(minutes=5):
        raise ValueError("Warning expiry must be at least 5 minutes in the future.")
    if expiry > now + timedelta(hours=72):
        raise ValueError("Community warnings may not remain active longer than 72 hours.")
    return {
        "category": category,
        "level": level,
        "level_tag": tier.tag,
        "level_title": tier.title,
        "threshold": tier.threshold,
        "headline": title,
        "description": body,
        "polygon": validate_polygon(polygon),
        "expires_at": expiry.isoformat(),
        "status": "active",
        "is_official": False,
    }


def radar_beam_height_km(range_km: float, elevation_angle_degrees: float) -> float:
    """Approximate beam-centre height above radar level using a 4/3-Earth model."""
    if not math.isfinite(range_km) or range_km < 0:
        raise ValueError("Radar range must be a finite nonnegative value.")
    if not math.isfinite(elevation_angle_degrees) or not -5 <= elevation_angle_degrees <= 90:
        raise ValueError("Radar elevation angle is outside valid bounds.")
    effective_earth_km = 6371.0 * (4.0 / 3.0)
    elevation = math.radians(elevation_angle_degrees)
    height = math.sqrt(
        range_km * range_km
        + effective_earth_km * effective_earth_km
        + 2.0 * range_km * effective_earth_km * math.sin(elevation)
    ) - effective_earth_km
    return height


def beam_height_contour_range_km(
    target_height_km: float,
    elevation_angle_degrees: float,
) -> float:
    """Invert the 4/3-Earth beam model to find range at a target beam-centre height."""
    if not math.isfinite(target_height_km) or target_height_km <= 0:
        raise ValueError("Target beam height must be positive and finite.")
    if not math.isfinite(elevation_angle_degrees) or not -5 <= elevation_angle_degrees <= 90:
        raise ValueError("Radar elevation angle is outside valid bounds.")
    effective_earth_km = 6371.0 * (4.0 / 3.0)
    elevation = math.radians(elevation_angle_degrees)
    radius_at_height = effective_earth_km + target_height_km
    discriminant = radius_at_height * radius_at_height - effective_earth_km**2 * math.cos(elevation)**2
    if discriminant <= 0:
        raise ValueError("Target height cannot be represented by this radar elevation.")
    distance = -effective_earth_km * math.sin(elevation) + math.sqrt(discriminant)
    if distance <= 0 or not math.isfinite(distance):
        raise ValueError("Target height is below the radar beam's starting height.")
    return distance
