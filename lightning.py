from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json


GERMANY_EXTENT = (5.0, 16.0, 47.0, 56.0)


@dataclass(frozen=True)
class LightningStrike:
    identifier: str
    latitude: float
    longitude: float
    timestamp: datetime
    quality: str | None = None
    uncertainty_km: float | None = None


def parse_blitzortung_message(message: str | bytes) -> tuple[LightningStrike, ...]:
    """Parse located strokes from one LightningMaps live-feed message."""
    try:
        payload = json.loads(message)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, dict) or not isinstance(payload.get("strokes"), list):
        return ()

    west, east, south, north = GERMANY_EXTENT
    strikes = []
    for stroke in payload["strokes"]:
        if not isinstance(stroke, dict):
            continue
        try:
            latitude = float(stroke["lat"])
            longitude = float(stroke["lon"])
            timestamp_ms = int(stroke["time"])
            source_id = int(stroke["src"])
            stroke_id = int(stroke["id"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (
            not south <= latitude <= north
            or not west <= longitude <= east
            or timestamp_ms <= 0
            or stroke_id < 0
        ):
            continue
        try:
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            continue
        try:
            uncertainty_km = float(stroke["dev"]) / 1000 if stroke.get("dev") is not None else None
        except (TypeError, ValueError):
            uncertainty_km = None
        strikes.append(
            LightningStrike(
                identifier=f"{source_id}/{stroke_id}",
                latitude=latitude,
                longitude=longitude,
                timestamp=timestamp,
                uncertainty_km=uncertainty_km,
            )
        )
    return tuple(strikes)


def build_blitzortung_request(last_ids: dict[str, int]) -> str:
    """Build the live-feed subscription request for the Germany map bounds."""
    west, east, south, north = GERMANY_EXTENT
    return json.dumps(
        {
            "v": 24,
            "i": last_ids,
            "s": False,
            "x": 0,
            "w": 0,
            "tx": 0,
            "tw": 0,
            "a": 4,
            "z": 5,
            "b": True,
            "h": "",
            "l": 0,
            "t": 0,
            "from_lightningmaps_org": True,
            "p": [north, east, south, west],
            "r": "feed",
        },
        separators=(",", ":"),
    )
