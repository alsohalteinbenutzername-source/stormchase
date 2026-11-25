from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
import re
from threading import RLock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

import keyring

from config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL
from lightning import LightningStrike
from radar import (
    RadarFrame,
    RainCell,
    RainTrackPoint,
    RotationDetection,
    decode_radar_file,
)
from tracking import StormCell, StormUpdate


RADAR_BUCKET = "stormchase-radar"
DAILY_OUTLOOK_GRID_POINT_COUNT = 744
DAILY_OUTLOOK_GRID_PAGE_SIZE = 100
KEYRING_SERVICE = "StormChase Supabase"
USERNAME_PATTERN = re.compile(r"[a-z0-9_]{3,24}\Z")


def _normalized_username(username: str) -> str:
    normalized = username.strip().lower()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Username must be 3–24 characters and contain only letters, numbers, or underscores."
        )
    return normalized


class SupabaseError(RuntimeError):
    """Raised when Supabase configuration, authentication, or data access fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _anonymous_auth_setup_required(error: SupabaseError) -> bool:
    message = str(error).lower().replace("-", " ")
    return any(
        detail in message
        for detail in (
            "anonymous sign ins are disabled",
            "anonymous sign in is disabled",
            "anonymous signups are disabled",
            "anonymous sign up is disabled",
            "signups are disabled",
            "signups not allowed",
        )
    )


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    api_key: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        local_http = parsed.scheme == "http" and parsed.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if parsed.scheme != "https" and not local_http:
            raise ValueError("Supabase URL must use HTTPS (HTTP is allowed for localhost).")
        if not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Supabase URL must be an origin without embedded credentials.")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Supabase URL must be a project origin without a path or query.")
        if not self.api_key.strip():
            raise ValueError("Supabase public API key cannot be empty.")

    @classmethod
    def from_environment(cls) -> SupabaseConfig:
        url = (
            os.environ.get("STORMCHASE_SUPABASE_URL", "").strip()
            or SUPABASE_URL.strip()
        ).rstrip("/")
        api_key = (
            os.environ.get("STORMCHASE_SUPABASE_PUBLISHABLE_KEY", "").strip()
            or os.environ.get("STORMCHASE_SUPABASE_ANON_KEY", "").strip()
            or SUPABASE_PUBLISHABLE_KEY.strip()
        )
        missing = [
            name
            for name, value in (
                ("STORMCHASE_SUPABASE_URL", url),
                (
                    "STORMCHASE_SUPABASE_PUBLISHABLE_KEY (or legacy "
                    "STORMCHASE_SUPABASE_ANON_KEY)",
                    api_key,
                ),
            )
            if not value
        ]
        if missing:
            raise SupabaseError(
                "Missing Supabase configuration: " + ", ".join(missing)
            )
        try:
            return cls(url=url, api_key=api_key)
        except ValueError as error:
            raise SupabaseError(f"Invalid Supabase configuration: {error}") from error


@dataclass(frozen=True)
class AuthUser:
    identifier: str
    username: str | None


@dataclass(frozen=True)
class _AuthSession:
    access_token: str
    refresh_token: str
    user: AuthUser
    expires_at: datetime


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SupabaseError(f"Supabase returned an invalid {field} timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SupabaseError(f"Supabase returned an invalid {field} timestamp.") from error
    if parsed.tzinfo is None:
        raise SupabaseError(f"Supabase returned a timezone-naive {field} timestamp.")
    return parsed.astimezone(timezone.utc)


class SupabaseClient:
    """Small typed REST/Auth client using only a public Supabase API key."""

    def __init__(self, config: SupabaseConfig, timeout: float = 20.0) -> None:
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError("Supabase request timeout must be a positive finite value.")
        self.config = config
        self.timeout = timeout
        self._session: _AuthSession | None = None
        self._session_lock = RLock()
        self.session_persistence_error: str | None = None
        self.session_restore_notice: str | None = None

    @property
    def current_user(self) -> AuthUser | None:
        with self._session_lock:
            return self._session.user if self._session is not None else None

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        access_token: str | None = None,
    ) -> Any:
        url = f"{self.config.url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {
            "apikey": self.config.api_key,
            "Accept": "application/json",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        elif not self.config.api_key.startswith("sb_publishable_"):
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                contents = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(detail)
            except json.JSONDecodeError:
                message = detail or str(error)
            if isinstance(message, dict):
                message = (
                    message.get("msg")
                    or message.get("message")
                    or message.get("error_description")
                    or message.get("error")
                    or str(message)
                )
            raise SupabaseError(
                f"Supabase request failed ({error.code}): {message}",
                status_code=error.code,
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise SupabaseError(
                f"Unable to reach Supabase: {type(error).__name__}: {error}"
            ) from error
        if not contents:
            return None
        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type.lower():
            return contents
        try:
            return json.loads(contents)
        except json.JSONDecodeError as error:
            raise SupabaseError("Supabase returned malformed JSON.") from error

    def _session_payload(
        self,
        payload: Any,
        *,
        username: str | None = None,
    ) -> _AuthSession:
        if not isinstance(payload, dict):
            raise SupabaseError("Supabase returned an invalid authentication response.")
        user_payload = payload.get("user")
        if not isinstance(user_payload, dict):
            raise SupabaseError("Supabase did not return a user for this session.")
        identifier = user_payload.get("id")
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        expires_in = payload.get("expires_in")
        if (
            not isinstance(identifier, str)
            or not identifier
            or not isinstance(access_token, str)
            or not isinstance(refresh_token, str)
            or not isinstance(expires_in, (int, float))
        ):
            raise SupabaseError("Supabase returned incomplete authentication data.")
        return _AuthSession(
            access_token=access_token,
            refresh_token=refresh_token,
            user=AuthUser(identifier, username),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )

    def _save_session(self, session: _AuthSession) -> None:
        value = json.dumps(
            {
                "refresh_token": session.refresh_token,
                "user_id": session.user.identifier,
                "username": session.user.username,
            },
            separators=(",", ":"),
        )
        try:
            keyring.set_password(KEYRING_SERVICE, self.config.url, value)
        except Exception as error:
            self.session_persistence_error = (
                f"Windows secure session storage failed: "
                f"{type(error).__name__}: {error}"
            )
        else:
            self.session_persistence_error = None

    def _remove_saved_session(self) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, self.config.url)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as error:
            self.session_persistence_error = (
                f"Could not remove the saved report identity: "
                f"{type(error).__name__}: {error}"
            )

    def _clear_session(self, notice: str) -> None:
        with self._session_lock:
            self._session = None
        self._remove_saved_session()
        self.session_restore_notice = notice

    def _install_session(self, payload: Any) -> AuthUser:
        session = self._session_payload(payload)
        with self._session_lock:
            self._session = session
        self.session_restore_notice = None
        self._save_session(session)
        return session.user

    def _set_session_user(self, user: AuthUser) -> None:
        with self._session_lock:
            session = self._session
            if session is None or session.user.identifier != user.identifier:
                raise SupabaseError("The report identity changed during the request.")
            updated_session = _AuthSession(
                access_token=session.access_token,
                refresh_token=session.refresh_token,
                user=user,
                expires_at=session.expires_at,
            )
            self._session = updated_session
        self._save_session(updated_session)

    def _load_reporter_profile(self, user: AuthUser) -> AuthUser:
        try:
            rows = self._table_rows(
                "stormchase_profiles",
                {
                    "select": "username",
                    "user_id": f"eq.{user.identifier}",
                    "limit": "1",
                },
                authenticated=True,
            )
        except SupabaseError as error:
            if error.status_code == 401:
                self._clear_session(
                    "The saved report identity expired. Without a linked sign-in "
                    "method, it cannot be recovered."
                )
            raise
        if not rows:
            updated_user = AuthUser(user.identifier, None)
        else:
            username = rows[0].get("username")
            if (
                not isinstance(username, str)
                or not USERNAME_PATTERN.fullmatch(username)
                or username != username.lower()
            ):
                raise SupabaseError("Supabase returned an invalid reporter username.")
            updated_user = AuthUser(user.identifier, username)
        self._set_session_user(updated_user)
        return updated_user

    def ensure_anonymous_session(self) -> AuthUser:
        user = self.current_user
        if user is None:
            try:
                payload = self._request(
                    "POST",
                    "/auth/v1/signup",
                    payload={"data": {}},
                )
            except SupabaseError as error:
                if _anonymous_auth_setup_required(error):
                    raise SupabaseError(
                        "StormChase report identities require Anonymous Sign-Ins "
                        "to be enabled in Supabase Auth."
                    ) from error
                raise
            user = self._install_session(payload)
        else:
            self._access_token()
            user = self.current_user
            if user is None:
                raise SupabaseError("The report identity is no longer available.")
        return self._load_reporter_profile(user)

    def set_reporter_username(self, username: str) -> AuthUser:
        normalized = _normalized_username(username)
        user = self.ensure_anonymous_session()
        if user.username is not None:
            if user.username == normalized:
                return user
            raise ValueError("A reporter username can only be set once.")
        token = self._access_token()
        try:
            self._request(
                "POST",
                "/rest/v1/stormchase_profiles",
                payload={"user_id": user.identifier, "username": normalized},
                access_token=token,
            )
        except SupabaseError as error:
            message = str(error).lower()
            if error.status_code == 409 or "duplicate key" in message:
                existing_user = self._load_reporter_profile(user)
                if existing_user.username == normalized:
                    return existing_user
                raise SupabaseError("That reporter username is already in use.") from error
            if error.status_code == 401:
                self._clear_session(
                    "The saved report identity expired. Choose a new username."
                )
            raise
        updated_user = AuthUser(user.identifier, normalized)
        self._set_session_user(updated_user)
        return updated_user

    def restore_session(self) -> AuthUser | None:
        try:
            saved = keyring.get_password(KEYRING_SERVICE, self.config.url)
        except Exception as error:
            raise SupabaseError(
                f"Unable to read the saved Windows report identity: "
                f"{type(error).__name__}: {error}"
            ) from error
        if not saved:
            return None
        try:
            saved_payload = json.loads(saved)
            refresh_token = saved_payload["refresh_token"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            self._remove_saved_session()
            raise SupabaseError("The saved report identity is invalid.") from error
        if not isinstance(refresh_token, str) or not refresh_token:
            self._remove_saved_session()
            raise SupabaseError("The saved report identity has no refresh token.")
        try:
            payload = self._request(
                "POST",
                "/auth/v1/token",
                query={"grant_type": "refresh_token"},
                payload={"refresh_token": refresh_token},
            )
            user = self._install_session(payload)
            return self._load_reporter_profile(user)
        except SupabaseError as error:
            if error.status_code in {400, 401}:
                self._clear_session(
                    "The saved report identity expired. Without a linked sign-in method, "
                    "it cannot be recovered."
                )
                return None
            raise

    def _access_token(self) -> str:
        with self._session_lock:
            session = self._session
            if session is None:
                raise SupabaseError("A report identity is required for this operation.")
            if session.expires_at <= datetime.now(timezone.utc) + timedelta(seconds=60):
                try:
                    payload = self._request(
                        "POST",
                        "/auth/v1/token",
                        query={"grant_type": "refresh_token"},
                        payload={"refresh_token": session.refresh_token},
                    )
                except SupabaseError as error:
                    if error.status_code in {400, 401}:
                        self._clear_session(
                            "The saved report identity expired. Without a linked sign-in "
                            "method, it cannot be recovered."
                        )
                    raise
                session = self._session_payload(
                    payload,
                    username=session.user.username,
                )
                self._session = session
                self._save_session(session)
            return session.access_token

    def _table_rows(
        self,
        table: str,
        query: dict[str, str],
        *,
        authenticated: bool = False,
    ) -> list[dict[str, Any]]:
        token = self._access_token() if authenticated else None
        result = self._request(
            "GET",
            f"/rest/v1/{table}",
            query=query,
            access_token=token,
        )
        if not isinstance(result, list) or not all(
            isinstance(row, dict) for row in result
        ):
            raise SupabaseError(f"Supabase returned an invalid {table} response.")
        return result

    def latest_storm_update(self) -> StormUpdate | None:
        rows = self._table_rows(
            "live_snapshots",
            {
                "select": "observed_at,payload",
                "product": "eq.konrad3d",
                "order": "observed_at.desc",
                "limit": "1",
            },
        )
        if not rows:
            return None
        row = rows[0]
        observed_at = _parse_datetime(row.get("observed_at"), "KONRAD3D")
        payload = row.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("cells"), list):
            raise SupabaseError("KONRAD3D snapshot payload must contain a cells array.")
        cells: list[StormCell] = []
        for item in payload["cells"]:
            if not isinstance(item, dict):
                raise SupabaseError("KONRAD3D snapshot contains an invalid cell.")
            try:
                identifier = str(item["identifier"])
                latitude = float(item["latitude"])
                longitude = float(item["longitude"])
                reflectivity = item.get("reflectivity_dbz")
                if reflectivity is not None:
                    reflectivity = float(reflectivity)
                severity = item.get("severity")
                if severity is not None:
                    severity = str(severity)
                track_payload = item.get("track", [[latitude, longitude]])
                track = tuple(
                    (float(point[0]), float(point[1]))
                    for point in track_payload
                )
                forecast_payload = item.get("forecast", [])
                if not isinstance(forecast_payload, list) or not all(
                    isinstance(point, dict) for point in forecast_payload
                ):
                    raise TypeError("forecast must be an array")
                forecast = tuple(
                    (
                        _parse_datetime(point.get("timestamp"), "KONRAD3D forecast"),
                        (
                            float(point["latitude"]),
                            float(point["longitude"]),
                        ),
                    )
                    for point in forecast_payload
                )
            except (KeyError, TypeError, ValueError, IndexError) as error:
                raise SupabaseError("KONRAD3D snapshot contains invalid cell data.") from error
            if (
                not identifier
                or not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or (
                    reflectivity is not None
                    and not math.isfinite(reflectivity)
                )
                or not track
                or any(
                    not -90 <= point[0] <= 90
                    or not -180 <= point[1] <= 180
                    for point in track
                )
                or any(
                    not math.isfinite(position[0])
                    or not math.isfinite(position[1])
                    or not -90 <= position[0] <= 90
                    or not -180 <= position[1] <= 180
                    for _timestamp, position in forecast
                )
            ):
                raise SupabaseError("KONRAD3D snapshot contains out-of-range coordinates.")
            cells.append(
                StormCell(
                    identifier=identifier,
                    latitude=latitude,
                    longitude=longitude,
                    track=track,
                    reflectivity_dbz=reflectivity,
                    severity=severity,
                    forecast=forecast,
                )
            )
        return StormUpdate(cells=tuple(cells), timestamp=observed_at)

    def latest_radar_frame(
        self,
        product: str,
        site_code: str = "",
    ) -> RadarFrame | None:
        if product not in {"reflectivity", "velocity"}:
            raise ValueError("Radar product must be reflectivity or velocity.")
        if product == "velocity" and not site_code:
            raise ValueError("A radar site code is required for velocity data.")
        if product == "reflectivity" and site_code:
            raise ValueError("Reflectivity radar frames do not use a site code.")
        rows = self._table_rows(
            "radar_frames",
            {
                "select": "observed_at,storage_path",
                "product": f"eq.{product}",
                "site_code": f"eq.{site_code}",
                "order": "observed_at.desc",
                "limit": "1",
            },
        )
        if not rows:
            return None
        observed_at = _parse_datetime(rows[0].get("observed_at"), "radar")
        storage_path = rows[0].get("storage_path")
        if not isinstance(storage_path, str) or not storage_path:
            raise SupabaseError("Radar frame metadata contains no Storage path.")
        if any(
            not part or part in {".", ".."}
            for part in storage_path.split("/")
        ):
            raise SupabaseError("Radar frame metadata contains an unsafe Storage path.")
        path = "/".join(quote(part, safe="") for part in storage_path.split("/"))
        contents = self._request(
            "GET",
            f"/storage/v1/object/public/{RADAR_BUCKET}/{path}",
        )
        if not isinstance(contents, bytes):
            raise SupabaseError("Supabase Storage returned an invalid radar file.")
        return decode_radar_file(contents, timestamp=observed_at)

    def recent_lightning(self, minutes: int = 15) -> tuple[LightningStrike, ...]:
        if minutes < 1:
            raise ValueError("Lightning lookback must be at least one minute.")
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        rows = self._table_rows(
            "lightning_strikes",
            {
                "select": (
                    "identifier,latitude,longitude,observed_at,quality,uncertainty_km"
                ),
                "observed_at": f"gte.{cutoff}",
                "order": "observed_at.desc",
                "limit": "5000",
            },
        )
        strikes: list[LightningStrike] = []
        for row in rows:
            try:
                identifier = str(row["identifier"])
                latitude = float(row["latitude"])
                longitude = float(row["longitude"])
                timestamp = _parse_datetime(row.get("observed_at"), "lightning")
                quality = row.get("quality")
                uncertainty = row.get("uncertainty_km")
                if uncertainty is not None:
                    uncertainty = float(uncertainty)
            except (KeyError, TypeError, ValueError) as error:
                raise SupabaseError("Lightning table contains an invalid observation.") from error
            if (
                not identifier
                or not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
                or (
                    uncertainty is not None
                    and (not math.isfinite(uncertainty) or uncertainty < 0)
                )
            ):
                raise SupabaseError("Lightning table contains out-of-range values.")
            strikes.append(
                LightningStrike(
                    identifier=identifier,
                    latitude=latitude,
                    longitude=longitude,
                    timestamp=timestamp,
                    quality=str(quality) if quality is not None else None,
                    uncertainty_km=uncertainty,
                )
            )
        return tuple(strikes)

    def latest_snapshot(
        self,
        product: str,
        scope: str = "",
    ) -> dict[str, Any] | None:
        rows = self._table_rows(
            "live_snapshots",
            {
                "select": "observed_at,payload",
                "product": f"eq.{product}",
                "scope": f"eq.{scope}",
                "order": "observed_at.desc",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def daily_outlook_grid(self, forecast_date: date) -> dict[str, Any] | None:
        print(
            f"[Outlook DB] Looking up daily forecast metadata for {forecast_date.isoformat()}...",
            flush=True,
        )
        rows = self._table_rows(
            "daily_outlook_forecasts",
            {
                "select": "forecast_date,issued_at",
                "forecast_date": f"eq.{forecast_date.isoformat()}",
                "limit": "1",
            },
        )
        if not rows:
            print(
                f"[Outlook DB] No daily forecast metadata for {forecast_date.isoformat()}; "
                "the outlook will use the direct forecast source.",
                flush=True,
            )
            return None
        print(
            f"[Outlook DB] Found daily forecast metadata; reading "
            f"{DAILY_OUTLOOK_GRID_POINT_COUNT} grid points in pages of "
            f"{DAILY_OUTLOOK_GRID_PAGE_SIZE}...",
            flush=True,
        )

        row = rows[0]
        if row.get("forecast_date") != forecast_date.isoformat():
            raise SupabaseError("Daily outlook row has an unexpected forecast date.")
        point_rows: list[dict[str, Any]] = []
        offset = 0
        page_count = math.ceil(
            DAILY_OUTLOOK_GRID_POINT_COUNT / DAILY_OUTLOOK_GRID_PAGE_SIZE
        )
        while offset < DAILY_OUTLOOK_GRID_POINT_COUNT:
            page_number = offset // DAILY_OUTLOOK_GRID_PAGE_SIZE + 1
            print(
                f"[Outlook DB] Requesting page {page_number}/{page_count} "
                f"(offset {offset})...",
                flush=True,
            )
            page_rows = self._table_rows(
                "daily_outlook_forecast_points",
                {
                    "select": "point_index,grid_payload",
                    "forecast_date": f"eq.{forecast_date.isoformat()}",
                    "order": "point_index.asc",
                    "limit": str(DAILY_OUTLOOK_GRID_PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            print(
                f"[Outlook DB] Page {page_number}/{page_count} returned "
                f"{len(page_rows)} rows; {min(offset + len(page_rows), DAILY_OUTLOOK_GRID_POINT_COUNT)} "
                f"of {DAILY_OUTLOOK_GRID_POINT_COUNT} collected.",
                flush=True,
            )
            if not page_rows:
                break
            if len(page_rows) > DAILY_OUTLOOK_GRID_POINT_COUNT - offset:
                raise SupabaseError(
                    "Daily outlook contains more points than the published grid."
                )
            point_rows.extend(page_rows)
            offset += len(page_rows)
        if len(point_rows) == DAILY_OUTLOOK_GRID_POINT_COUNT:
            print("[Outlook DB] Reconstructing and validating the 744-point grid...", flush=True)
            grid = []
            for expected_index, point_row in enumerate(point_rows):
                payload = point_row.get("grid_payload")
                if (
                    point_row.get("point_index") != expected_index
                    or not isinstance(payload, dict)
                    or not isinstance(payload.get("icon_d2"), dict)
                ):
                    raise SupabaseError(
                        "Daily outlook points must contain a complete ICON-D2 grid."
                    )
                grid.append(payload)
            print("[Outlook DB] Shared grid is complete.", flush=True)
        elif point_rows:
            raise SupabaseError(
                f"Daily outlook contains {len(point_rows)} of "
                f"{DAILY_OUTLOOK_GRID_POINT_COUNT} grid points."
            )
        else:
            print(
                "[Outlook DB] No point rows found; checking the legacy single-grid payload.",
                flush=True,
            )
            legacy_rows = self._table_rows(
                "daily_outlook_forecasts",
                {
                    "select": "grid_payload",
                    "forecast_date": f"eq.{forecast_date.isoformat()}",
                    "limit": "1",
                },
            )
            grid = legacy_rows[0].get("grid_payload") if legacy_rows else None
            if not isinstance(grid, list) or not grid or not all(
                isinstance(point, dict)
                and isinstance(point.get("icon_d2"), dict)
                for point in grid
            ):
                raise SupabaseError(
                    "Daily outlook payload must contain ICON-D2 grid forecasts."
                )
        return {
            "issued_at": _parse_datetime(row.get("issued_at"), "daily outlook"),
            "grid": grid,
        }

    def latest_rain_cells(self) -> tuple[RainCell, ...] | None:
        snapshot = self.latest_snapshot("rain_cells")
        if snapshot is None:
            return None
        payload = snapshot.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("cells"), list):
            raise SupabaseError("Rain-cell snapshot payload must contain a cells array.")
        observed_at = _parse_datetime(snapshot.get("observed_at"), "rain-cell")
        cells: list[RainCell] = []
        for item in payload["cells"]:
            if not isinstance(item, dict):
                raise SupabaseError("Rain-cell snapshot contains an invalid cell.")
            try:
                identifier = str(item["identifier"])
                latitude = float(item["latitude"])
                longitude = float(item["longitude"])
                maximum = float(item["max_reflectivity_dbz"])
                area_pixels = int(item["area_pixels"])
                track_payload = item.get("track", [])
                track = tuple(
                    RainTrackPoint(
                        latitude=float(point["latitude"]),
                        longitude=float(point["longitude"]),
                        timestamp=_parse_datetime(point["timestamp"], "rain track"),
                        reflectivity_dbz=float(point["reflectivity_dbz"]),
                    )
                    for point in track_payload
                )
            except (KeyError, TypeError, ValueError) as error:
                raise SupabaseError("Rain-cell snapshot contains invalid cell data.") from error
            if (
                not identifier
                or not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not math.isfinite(maximum)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
                or area_pixels < 1
                or any(
                    not math.isfinite(point.latitude)
                    or not math.isfinite(point.longitude)
                    or not math.isfinite(point.reflectivity_dbz)
                    for point in track
                )
            ):
                raise SupabaseError("Rain-cell snapshot contains invalid cell values.")
            if not track:
                track = (
                    RainTrackPoint(
                        latitude=latitude,
                        longitude=longitude,
                        timestamp=observed_at,
                        reflectivity_dbz=maximum,
                    ),
                )
            cells.append(
                RainCell(
                    identifier=identifier,
                    latitude=latitude,
                    longitude=longitude,
                    max_reflectivity_dbz=maximum,
                    area_pixels=area_pixels,
                    track=track,
                )
            )
        return tuple(cells)

    def latest_rotation_detections(
        self,
        site_code: str,
    ) -> tuple[RotationDetection, ...] | None:
        if not site_code:
            raise ValueError("A radar site code is required for rotation detections.")
        snapshot = self.latest_snapshot("rotation_detections", site_code)
        if snapshot is None:
            return None
        payload = snapshot.get("payload")
        if not isinstance(payload, dict) or not isinstance(
            payload.get("detections"),
            list,
        ):
            raise SupabaseError(
                "Rotation snapshot payload must contain a detections array."
            )
        detections: list[RotationDetection] = []
        for item in payload["detections"]:
            if not isinstance(item, dict):
                raise SupabaseError("Rotation snapshot contains an invalid detection.")
            try:
                detection = RotationDetection(
                    latitude=float(item["latitude"]),
                    longitude=float(item["longitude"]),
                    negative_pixels=int(item["negative_pixels"]),
                    positive_pixels=int(item["positive_pixels"]),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise SupabaseError(
                    "Rotation snapshot contains invalid detection data."
                ) from error
            if (
                not -90 <= detection.latitude <= 90
                or not -180 <= detection.longitude <= 180
                or not math.isfinite(detection.latitude)
                or not math.isfinite(detection.longitude)
                or detection.negative_pixels < 0
                or detection.positive_pixels < 0
            ):
                raise SupabaseError(
                    "Rotation snapshot contains invalid detection values."
                )
            detections.append(detection)
        return tuple(detections)

    def recent_reports(self, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if not 1 <= limit <= 500:
            raise ValueError("Community report limit must be between 1 and 500.")
        user = self.current_user
        if user is None or user.username is None:
            raise SupabaseError("Choose a reporter username to view community reports.")
        try:
            rows = self._table_rows(
                "storm_reports",
                {
                    "select": "id,reporter_username,cell_id,latitude,longitude,report,created_at",
                    "order": "created_at.desc",
                    "limit": str(limit),
                },
                authenticated=True,
            )
        except SupabaseError as error:
            if error.status_code == 401:
                self._clear_session(
                    "The saved report identity expired. Without a linked sign-in method, "
                    "it cannot be recovered."
                )
            raise
        return tuple(rows)

    def submit_report(self, cell: StormCell, report: str) -> None:
        report = report.strip()
        if not report or len(report) > 2000:
            raise ValueError("Report text must contain between 1 and 2000 characters.")
        token = self._access_token()
        user = self.current_user
        if user is None or user.username is None:
            raise SupabaseError("Choose a reporter username before submitting a report.")
        try:
            self._request(
                "POST",
                "/rest/v1/storm_reports",
                payload={
                    "user_id": user.identifier,
                    "reporter_username": user.username,
                    "cell_id": cell.identifier,
                    "latitude": cell.latitude,
                    "longitude": cell.longitude,
                    "report": report,
                },
                access_token=token,
            )
        except SupabaseError as error:
            if error.status_code == 401:
                self._clear_session(
                    "The saved report identity expired. Without a linked sign-in method, "
                    "it cannot be recovered."
                )
            raise
