from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from tracking import StormCell, project_cell_motion


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _distance_bearing(
    first: tuple[float, float],
    second: tuple[float, float],
) -> tuple[float, float]:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    dlat = lat2 - lat1
    dlon = (lon2 - lon1 + math.pi) % (2.0 * math.pi) - math.pi
    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    distance = 2.0 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))
    bearing = math.degrees(math.atan2(
        math.sin(dlon) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon),
    )) % 360.0
    return distance, bearing


def _angle_error(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


class CaseAnalysisStore:
    """Local-only cell history, forecast verification, case comparisons and event timeline."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            root = Path(os.getenv("LOCALAPPDATA") or (Path.home() / ".stormchase"))
            root.mkdir(parents=True, exist_ok=True)
            path = root / "analysis.sqlite3"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cell_cases (
                    case_id TEXT PRIMARY KEY,
                    cell_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    observation_count INTEGER NOT NULL DEFAULT 0,
                    max_reflectivity_dbz REAL,
                    path_length_km REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS cell_cases_recent_idx
                    ON cell_cases(last_seen DESC);
                CREATE TABLE IF NOT EXISTS cell_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL REFERENCES cell_cases(case_id) ON DELETE CASCADE,
                    cell_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    reflectivity_dbz REAL,
                    severity TEXT,
                    UNIQUE(cell_id, observed_at)
                );
                CREATE INDEX IF NOT EXISTS cell_observations_recent_idx
                    ON cell_observations(cell_id, observed_at DESC);
                CREATE TABLE IF NOT EXISTS cell_forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cell_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    valid_at TEXT NOT NULL,
                    lead_minutes INTEGER NOT NULL,
                    origin_latitude REAL NOT NULL,
                    origin_longitude REAL NOT NULL,
                    forecast_latitude REAL NOT NULL,
                    forecast_longitude REAL NOT NULL,
                    verified_at TEXT,
                    position_error_km REAL,
                    heading_error_degrees REAL,
                    speed_error_kmh REAL,
                    UNIQUE(cell_id, model, issued_at, valid_at)
                );
                CREATE INDEX IF NOT EXISTS cell_forecasts_pending_idx
                    ON cell_forecasts(cell_id, verified_at, valid_at);
                CREATE TABLE IF NOT EXISTS storm_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    summary TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'StormChase',
                    UNIQUE(event_type, event_key, event_time)
                );
                CREATE INDEX IF NOT EXISTS storm_events_recent_idx
                    ON storm_events(event_time DESC);
                """
            )

    def record_event(
        self,
        event_type: str,
        event_key: str,
        event_time: datetime,
        summary: str,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        source: str = "StormChase",
    ) -> None:
        timestamp = _utc(event_time).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO storm_events
                    (event_type, event_key, event_time, latitude, longitude, summary, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_type, str(event_key), timestamp, latitude, longitude, summary[:500], source[:80]),
            )

    def record_cells(
        self,
        cells: tuple[StormCell, ...],
        observed_at: datetime | None,
        motion_winds: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        if observed_at is None:
            return
        stamp = _utc(observed_at)
        stamp_text = stamp.isoformat()
        winds = motion_winds or {}
        with self._connect() as connection:
            for cell in cells:
                previous = connection.execute(
                    """
                    SELECT case_id, observed_at, latitude, longitude, reflectivity_dbz
                    FROM cell_observations
                    WHERE cell_id = ? ORDER BY observed_at DESC LIMIT 1
                    """,
                    (cell.identifier,),
                ).fetchone()
                if previous is not None and previous["observed_at"] == stamp_text:
                    continue

                active_case = None
                if previous is not None:
                    previous_time = datetime.fromisoformat(previous["observed_at"])
                    if stamp - _utc(previous_time) <= timedelta(minutes=45):
                        active_case = connection.execute(
                            "SELECT * FROM cell_cases WHERE case_id = ?",
                            (previous["case_id"],),
                        ).fetchone()

                if active_case is None:
                    case_id = str(uuid4())
                    connection.execute(
                        """
                        INSERT INTO cell_cases
                            (case_id, cell_id, started_at, last_seen, observation_count, max_reflectivity_dbz)
                        VALUES (?, ?, ?, ?, 0, ?)
                        """,
                        (case_id, cell.identifier, stamp_text, stamp_text, cell.reflectivity_dbz),
                    )
                else:
                    case_id = str(active_case["case_id"])

                increment = 0.0
                if active_case is not None:
                    increment, _ = _distance_bearing(
                        (float(previous["latitude"]), float(previous["longitude"])),
                        (cell.latitude, cell.longitude),
                    )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO cell_observations
                        (case_id, cell_id, observed_at, latitude, longitude, reflectivity_dbz, severity)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case_id, cell.identifier, stamp_text, cell.latitude, cell.longitude,
                        cell.reflectivity_dbz, cell.severity,
                    ),
                )
                connection.execute(
                    """
                    UPDATE cell_cases
                    SET last_seen = ?,
                        observation_count = observation_count + 1,
                        max_reflectivity_dbz = CASE
                            WHEN ? IS NULL THEN max_reflectivity_dbz
                            WHEN max_reflectivity_dbz IS NULL THEN ?
                            ELSE MAX(max_reflectivity_dbz, ?) END,
                        path_length_km = path_length_km + ?
                    WHERE case_id = ?
                    """,
                    (stamp_text, cell.reflectivity_dbz, cell.reflectivity_dbz, cell.reflectivity_dbz, increment, case_id),
                )
                event_summary = (
                    f"{cell.identifier} observed"
                    + (f" · {cell.reflectivity_dbz:.1f} dBZ" if cell.reflectivity_dbz is not None else "")
                    + f" · {cell.latitude:.3f}, {cell.longitude:.3f}"
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO storm_events
                        (event_type, event_key, event_time, latitude, longitude, summary, source)
                    VALUES ('cell', ?, ?, ?, ?, ?, 'KONRAD3D')
                    """,
                    (f"{case_id}:{stamp_text}", stamp_text, cell.latitude, cell.longitude, event_summary),
                )

                if cell.forecast:
                    forecasts = (
                        (valid_at, position, "KONRAD3D")
                        for valid_at, position in cell.forecast
                    )
                elif cell.identifier in winds:
                    speed, direction = winds[cell.identifier]
                    projected = project_cell_motion(
                        cell.latitude, cell.longitude, direction, speed,
                        duration_minutes=60, step_minutes=15,
                    )
                    forecasts = (
                        (stamp + timedelta(minutes=15 * index), position, "ICON-D2 700 hPa wind")
                        for index, position in enumerate(projected[1:], start=1)
                    )
                else:
                    forecasts = ()

                for valid_at, position, model in forecasts:
                    valid_time = _utc(valid_at)
                    lead = int(round((valid_time - stamp).total_seconds() / 60))
                    if lead < 5 or lead > 180:
                        continue
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO cell_forecasts
                            (cell_id, model, issued_at, valid_at, lead_minutes,
                             origin_latitude, origin_longitude, forecast_latitude, forecast_longitude)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cell.identifier, model, stamp_text, valid_time.isoformat(), lead,
                            cell.latitude, cell.longitude, float(position[0]), float(position[1]),
                        ),
                    )

                self._verify_forecasts(connection, cell, stamp)

            # Keep this local analysis database bounded without deleting case summaries.
            retention = (stamp - timedelta(days=45)).isoformat()
            connection.execute("DELETE FROM cell_observations WHERE observed_at < ?", (retention,))
            connection.execute(
                "DELETE FROM cell_forecasts WHERE issued_at < ? AND verified_at IS NOT NULL",
                (retention,),
            )
            connection.execute("DELETE FROM storm_events WHERE event_time < ?", (retention,))

    @staticmethod
    def _verify_forecasts(
        connection: sqlite3.Connection,
        cell: StormCell,
        observed_at: datetime,
    ) -> None:
        lower = (observed_at - timedelta(minutes=6)).isoformat()
        upper = (observed_at + timedelta(minutes=6)).isoformat()
        rows = connection.execute(
            """
            SELECT * FROM cell_forecasts
            WHERE cell_id = ? AND verified_at IS NULL
              AND issued_at < ? AND valid_at BETWEEN ? AND ?
            ORDER BY ABS(julianday(valid_at) - julianday(?))
            """,
            (cell.identifier, observed_at.isoformat(), lower, upper, observed_at.isoformat()),
        ).fetchall()
        for forecast in rows:
            issued_at = datetime.fromisoformat(forecast["issued_at"])
            lead_seconds = max(1.0, (datetime.fromisoformat(forecast["valid_at"]) - issued_at).total_seconds())
            elapsed_seconds = max(1.0, (observed_at - issued_at).total_seconds())
            predicted_distance, predicted_heading = _distance_bearing(
                (forecast["origin_latitude"], forecast["origin_longitude"]),
                (forecast["forecast_latitude"], forecast["forecast_longitude"]),
            )
            actual_distance, actual_heading = _distance_bearing(
                (forecast["origin_latitude"], forecast["origin_longitude"]),
                (cell.latitude, cell.longitude),
            )
            position_error, _ = _distance_bearing(
                (forecast["forecast_latitude"], forecast["forecast_longitude"]),
                (cell.latitude, cell.longitude),
            )
            connection.execute(
                """
                UPDATE cell_forecasts
                SET verified_at = ?, position_error_km = ?,
                    heading_error_degrees = ?, speed_error_kmh = ?
                WHERE id = ? AND verified_at IS NULL
                """,
                (
                    observed_at.isoformat(),
                    position_error,
                    _angle_error(predicted_heading, actual_heading),
                    abs(predicted_distance / (lead_seconds / 3600.0) - actual_distance / (elapsed_seconds / 3600.0)),
                    forecast["id"],
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO storm_events
                    (event_type, event_key, event_time, latitude, longitude, summary, source)
                VALUES ('forecast_verification', ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(forecast["id"]), observed_at.isoformat(), cell.latitude, cell.longitude,
                    f"{forecast['model']} {forecast['lead_minutes']} min forecast verified · "
                    f"{position_error:.1f} km position error",
                    "Local verification",
                ),
            )

    def history(self, cell_id: str, limit: int = 24) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT case_id, observed_at, latitude, longitude, reflectivity_dbz, severity
                FROM cell_observations
                WHERE case_id = (
                    SELECT case_id FROM cell_cases WHERE cell_id = ?
                    ORDER BY last_seen DESC LIMIT 1
                )
                ORDER BY observed_at DESC LIMIT ?
                """,
                (cell_id, max(1, min(limit, 100))),
            ).fetchall()
        return tuple(dict(row) for row in reversed(rows))

    def compare_cases(self, cell_id: str, limit: int = 5) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM cell_cases WHERE cell_id = ? ORDER BY last_seen DESC LIMIT 1",
                (cell_id,),
            ).fetchone()
            if current is None:
                return ()
            current_started = datetime.fromisoformat(current["started_at"])
            current_duration = max(0.0, (datetime.fromisoformat(current["last_seen"]) - current_started).total_seconds() / 60)
            candidates = connection.execute(
                """
                SELECT * FROM cell_cases
                WHERE case_id <> ? AND observation_count >= 2 AND max_reflectivity_dbz IS NOT NULL
                ORDER BY last_seen DESC LIMIT 100
                """,
                (current["case_id"],),
            ).fetchall()
        current_reflectivity = float(current["max_reflectivity_dbz"] or 0.0)
        current_path = float(current["path_length_km"] or 0.0)
        scored = []
        for row in candidates:
            started = datetime.fromisoformat(row["started_at"])
            duration = max(0.0, (datetime.fromisoformat(row["last_seen"]) - started).total_seconds() / 60)
            reflectivity = float(row["max_reflectivity_dbz"] or 0.0)
            path_length = float(row["path_length_km"] or 0.0)
            score = (
                abs(current_reflectivity - reflectivity) / 20.0
                + abs(current_path - path_length) / 50.0
                + abs(current_duration - duration) / 90.0
            )
            scored.append({
                "cell_id": row["cell_id"], "started_at": row["started_at"],
                "last_seen": row["last_seen"], "observations": row["observation_count"],
                "max_reflectivity_dbz": reflectivity, "path_length_km": path_length,
                "duration_minutes": duration, "similarity_score": score,
            })
        scored.sort(key=lambda item: item["similarity_score"])
        return tuple(scored[:max(1, min(limit, 10))])

    def forecast_skill(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT model, lead_minutes, COUNT(*) AS samples,
                       AVG(position_error_km) AS mean_position_error_km,
                       AVG(heading_error_degrees) AS mean_heading_error_degrees,
                       AVG(speed_error_kmh) AS mean_speed_error_kmh
                FROM cell_forecasts
                WHERE verified_at IS NOT NULL
                GROUP BY model, lead_minutes
                ORDER BY lead_minutes, model
                """
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def timeline(self, limit: int = 60) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type, event_time, latitude, longitude, summary, source
                FROM storm_events ORDER BY event_time DESC, id DESC LIMIT ?
                """,
                (max(1, min(limit, 250)),),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def case_statistics(self, cell_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cell_cases WHERE cell_id = ? ORDER BY last_seen DESC LIMIT 1",
                (cell_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        duration = datetime.fromisoformat(result["last_seen"]) - datetime.fromisoformat(result["started_at"])
        result["duration_minutes"] = max(0.0, duration.total_seconds() / 60.0)
        return result
