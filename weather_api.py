import math
import requests
import requests_cache
import time
import random
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from config import LATITUDE, LONGITUDE


STORMANALYZER_HOURLY_FIELDS = (
    "cape,convective_inhibition,surface_pressure,"
    "temperature_2m,dew_point_2m,relative_humidity_2m,"
    "temperature_925hPa,temperature_850hPa,temperature_700hPa,"
    "temperature_500hPa,relative_humidity_925hPa,relative_humidity_850hPa,"
    "relative_humidity_700hPa,relative_humidity_500hPa,"
    "geopotential_height_925hPa,geopotential_height_850hPa,"
    "geopotential_height_700hPa,geopotential_height_500hPa,"
    "precipitation,wind_speed_10m,wind_direction_10m,"
    "wind_speed_180m,wind_direction_180m,"
    "wind_speed_925hPa,wind_direction_925hPa,wind_speed_850hPa,"
    "wind_direction_850hPa,wind_speed_700hPa,wind_direction_700hPa,"
    "wind_speed_500hPa,wind_direction_500hPa"
)
OPTIONAL_STORMANALYZER_HOURLY_FIELDS = ()



params = {
    "latitude": LATITUDE,
    "longitude": LONGITUDE,
    "hourly": STORMANALYZER_HOURLY_FIELDS,

    "past_hours": 24,
    "forecast_hours": 48,

    "models": "icon_d2",
    "wind_speed_unit": "kmh",
    "timezone": "auto"
}




def _weather_cache_name():
    app_data = os.environ.get("LOCALAPPDATA")
    cache_root = (
        Path(app_data) / "StormChase"
        if app_data
        else Path.home() / ".cache" / "StormChase"
    )
    cache_root.mkdir(parents=True, exist_ok=True)
    return str(cache_root / "openmeteo")


WEATHER_CACHE_NAME = _weather_cache_name()
WEATHER_BUDGET_DB = str(Path(WEATHER_CACHE_NAME).with_name("openmeteo_budget.sqlite"))
cache_session = requests_cache.CachedSession(
    WEATHER_CACHE_NAME,
    # reuse recent runs to avoid repeat api quota use.
    expire_after=21600,
    stale_if_error=True,
)

cin_params = {
    "latitude": LATITUDE,
    "longitude": LONGITUDE,

    "hourly": [
        "convective_inhibition"
    ],

    "models": "ecmwf_ifs",

    "forecast_days": 10,

    "timezone": "auto"
}

WEATHER_BATCH_DELAY_SECONDS = 60
OUTLOOK_BATCH_SIZE = 100
OUTLOOK_CALLS_PER_MINUTE = 450
WEATHER_CALLS_PER_DAY = 8500
WEATHER_RETRY_ATTEMPTS = 4
_weather_request_lock = Lock()


class WeatherRateLimitError(RuntimeError):
    """Raised after Open-Meteo rejects the request limit retries."""


def _retry_delay(response, attempt):
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if retry_after:
        try:
            return min(max(float(retry_after), 1.0), 60.0)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return min(max((retry_at - datetime.now(timezone.utc)).total_seconds(), 1.0), 60.0)
            except (TypeError, ValueError, OverflowError):
                pass
    return min(5.0 * (2 ** attempt) + random.uniform(0.0, 1.0), 60.0)


def _get_weather_response(url, request_params, timeout=60, cache_expire_after=None):
    last_error = None
    for attempt in range(WEATHER_RETRY_ATTEMPTS):
        response = None
        try:
            request_options = {"timeout": timeout}
            if cache_expire_after is not None:
                request_options["expire_after"] = cache_expire_after
            with _weather_request_lock:
                if not _weather_response_cached(url, request_params):
                    call_weight = _weather_call_weight(request_params)
                    _wait_for_weather_budget(call_weight)
                response = cache_session.get(
                    url,
                    params=request_params,
                    **request_options,
                )
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            status = response.status_code if response is not None else None
            if status is not None and status != 429 and status < 500:
                raise
            if attempt + 1 >= WEATHER_RETRY_ATTEMPTS:
                if status == 429:
                    retry_after = response.headers.get("Retry-After")
                    detail = f"Open-Meteo rate limit (HTTP 429) after {WEATHER_RETRY_ATTEMPTS} attempts."
                    if retry_after:
                        detail += f" Retry-After: {retry_after}."
                    raise WeatherRateLimitError(detail) from error
                raise
            time.sleep(_retry_delay(response, attempt))
    raise last_error or RuntimeError("Weather request failed without a response.")


def get_weather_json(url, request_params, timeout=30, cache_seconds=3600):
    """Fetch one Open-Meteo JSON response through the shared cache and retry path."""
    response = _get_weather_response(
        url,
        request_params,
        timeout=timeout,
        cache_expire_after=cache_seconds,
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid weather response returned from {url}.")
    if payload.get("error"):
        raise RuntimeError(payload.get("reason", "Weather API returned an error."))
    return payload


def get_weather_json_batch(url, request_params, timeout=30, cache_seconds=300):
    """Fetch one or more Open-Meteo locations through the shared request path."""
    response = _get_weather_response(
        url,
        request_params,
        timeout=timeout,
        cache_expire_after=cache_seconds,
    )
    payload = response.json()
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not all(isinstance(point, dict) for point in payload):
        raise RuntimeError(f"Invalid batch weather response returned from {url}.")
    for point in payload:
        if point.get("error"):
            raise RuntimeError(point.get("reason", "Weather API returned an error."))
    return payload


def _weather_response_cached(url, request_params):
    try:
        return cache_session.cache.contains(url=url, params=request_params)
    except (AttributeError, TypeError):
        return False


def _weather_call_weight(request_params):
    variable_count = 0
    for field in ("hourly", "current", "daily", "minutely_15"):
        values = request_params.get(field, ())
        if isinstance(values, str):
            values = values.split(",")
        variable_count += sum(1 for value in values if str(value).strip())

    latitude = request_params.get("latitude", "")
    if isinstance(latitude, str):
        location_count = len([value for value in latitude.split(",") if value.strip()])
    elif isinstance(latitude, (list, tuple)):
        location_count = len(latitude)
    else:
        location_count = 1
    model_names = request_params.get("models")
    model_count = len(model_names.split(",")) if isinstance(model_names, str) else 1
    return max(1.0, variable_count / 10.0) * max(location_count, 1) * max(model_count, 1)


def _wait_for_weather_budget(call_weight):
    if call_weight > OUTLOOK_CALLS_PER_MINUTE:
        raise WeatherRateLimitError(
            "A single Open-Meteo request exceeds StormChase's safe per-minute budget."
        )
    while True:
        now = time.time()
        with sqlite3.connect(WEATHER_BUDGET_DB, timeout=30, isolation_level=None) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS weather_calls (timestamp REAL NOT NULL, weight REAL NOT NULL)"
            )
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM weather_calls WHERE timestamp <= ?", (now - 86400,)
            )
            history = connection.execute(
                "SELECT timestamp, weight FROM weather_calls WHERE timestamp > ? ORDER BY timestamp",
                (now - 86400,),
            ).fetchall()
            used_day = sum(weight for _timestamp, weight in history)
            if used_day + call_weight > WEATHER_CALLS_PER_DAY:
                connection.rollback()
                raise WeatherRateLimitError(
                    "StormChase reached its conservative rolling daily Open-Meteo budget. "
                    "The API's daily limit may require waiting up to 24 hours."
                )

            recent = [entry for entry in history if entry[0] > now - 60.0]
            used_minute = sum(weight for _timestamp, weight in recent)
            if used_minute + call_weight <= OUTLOOK_CALLS_PER_MINUTE:
                connection.execute(
                    "INSERT INTO weather_calls (timestamp, weight) VALUES (?, ?)",
                    (now, call_weight),
                )
                connection.commit()
                return

            freed_calls = 0.0
            next_allowed_at = now + 1.0
            for timestamp, weight in recent:
                freed_calls += weight
                if used_minute - freed_calls + call_weight <= OUTLOOK_CALLS_PER_MINUTE:
                    next_allowed_at = timestamp + 60.0
                    break
            connection.rollback()
        time.sleep(max(next_allowed_at - time.time(), 0.05))


def _request_hourly(url, request_params):
    response = _get_weather_response(url, request_params, timeout=30)

    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid weather response returned from {url}.")

    hourly = data.get("hourly")
    if not isinstance(hourly, dict):
        raise RuntimeError(f"No hourly weather data returned from {url}.")
    try:
        elevation = float(data["elevation"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"No valid surface elevation returned from {url}.") from error
    if not math.isfinite(elevation):
        raise RuntimeError(f"No valid surface elevation returned from {url}.")
    hourly["surface_elevation_m"] = elevation

    return hourly


def get_germany_grid_weather(locations):
    """Fetch ICON-D2 hourly data for outlook points in paced batches."""
    if not locations:
        return []

    cache_now = datetime.now(ZoneInfo("Europe/Berlin"))
    cache_expiry = (cache_now + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    cache_expire_after = max(
        1,
        (
            cache_expiry.astimezone(timezone.utc)
            - cache_now.astimezone(timezone.utc)
        ).total_seconds(),
    )
    fields = STORMANALYZER_HOURLY_FIELDS.split(",")
    batches = [
        locations[index:index + OUTLOOK_BATCH_SIZE]
        for index in range(0, len(locations), OUTLOOK_BATCH_SIZE)
    ]
    request_started = time.monotonic()
    calls_per_location = len(fields) / 10.0
    max_batches_per_minute = max(
        1,
        int(OUTLOOK_CALLS_PER_MINUTE // (OUTLOOK_BATCH_SIZE * calls_per_location)),
    )
    # keep weighted requests within the rolling minute cap.
    batch_interval = 60.0 / max_batches_per_minute + 2.0
    network_request_index = 0
    payload_by_model = {}
    for model, forecast_days in (("icon_d2", 2),):
        model_payload = []
        for batch_index, batch in enumerate(batches, start=1):
            first_point = (batch_index - 1) * OUTLOOK_BATCH_SIZE + 1
            last_point = first_point + len(batch) - 1
            request_params = {
                "latitude": ",".join(f"{latitude:.3f}" for latitude, _ in batch),
                "longitude": ",".join(f"{longitude:.3f}" for _, longitude in batch),
                "hourly": ",".join(fields),
                "models": model,
                "forecast_days": forecast_days,
                "timezone": "Europe/Berlin",
                "wind_speed_unit": "kmh",
            }
            url = "https://api.open-meteo.com/v1/dwd-icon"
            is_cached = _weather_response_cached(url, request_params)
            print(
                f"[Outlook API] {model}: requesting grid points {first_point}–{last_point} "
                f"of {len(locations)} ({batch_index}/{len(batches)})"
                f"{' from local cache' if is_cached else ' from Open-Meteo'}...",
                flush=True,
            )
            if not is_cached:
                scheduled_at = request_started + network_request_index * batch_interval
                wait_seconds = scheduled_at - time.monotonic()
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
            batch_started = time.monotonic()
            try:
                response = _get_weather_response(
                    url,
                    request_params,
                    cache_expire_after=cache_expire_after,
                )
            except Exception as error:
                print(
                    f"[Outlook API] {model}: batch {batch_index}/{len(batches)} failed: "
                    f"{type(error).__name__}: {error}",
                    flush=True,
                )
                raise
            if not getattr(response, "from_cache", False):
                network_request_index += 1
            print(
                f"[Outlook API] {model}: batch {batch_index}/{len(batches)} complete "
                f"in {time.monotonic() - batch_started:.1f}s "
                f"({'cache' if getattr(response, 'from_cache', False) else 'network'}).",
                flush=True,
            )
            batch_payload = response.json()
            if isinstance(batch_payload, dict):
                batch_payload = [batch_payload]
            if not isinstance(batch_payload, list) or len(batch_payload) != len(batch):
                raise RuntimeError(f"Open-Meteo returned an incomplete {model} outlook grid batch.")
            model_payload.extend(batch_payload)

        for point in model_payload:
            hourly = point.get("hourly", {})
            _validate_hourly(hourly, fields)
            try:
                elevation = float(point["elevation"])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError(
                    "Open-Meteo did not return a valid surface elevation."
                ) from error
            if not math.isfinite(elevation):
                raise RuntimeError(
                    "Open-Meteo did not return a valid surface elevation."
                )
            hourly["surface_elevation_m"] = elevation
            times = hourly["time"]
            cape_values = hourly["cape"]
            cin_values = hourly["convective_inhibition"]
            temperature_values = hourly["temperature_2m"]
            dewpoint_values = hourly["dew_point_2m"]
            temperature_850_values = hourly["temperature_850hPa"]
            hourly["cin"] = []
            for index, _ in enumerate(times):
                cape = cape_values[index]
                cape_values[index] = max(float(cape or 0), 0)
                cin = cin_values[index]
                if cin is None:
                    temp = temperature_values[index]
                    dewpoint = dewpoint_values[index]
                    temp850 = temperature_850_values[index]
                    if temp is not None and dewpoint is not None and temp850 is not None:
                        cin = -(float(temp) - float(dewpoint)) * 5 + (float(temp) - float(temp850)) * 3
                hourly["cin"].append(-abs(float(cin)) if cin is not None else 0.0)
        payload_by_model[model] = model_payload

    return [
        {model: payload_by_model[model][index]["hourly"] for model in payload_by_model}
        for index in range(len(locations))
    ]


def _validate_hourly(hourly, variables):
    times = hourly.get("time")
    if not isinstance(times, list) or not times:
        raise RuntimeError("Weather response contains no hourly timestamps.")

    missing_variables = [
        variable
        for variable in variables
        if not isinstance(hourly.get(variable), list)
        or len(hourly[variable]) != len(times)
    ]
    if missing_variables:
        raise RuntimeError(
            "Weather response is missing hourly data for: "
            + ", ".join(missing_variables)
        )


def _fetch_hourly_in_batches(url, request_params, variables):
    midpoint = (len(variables) + 1) // 2
    variable_batches = (
        variables[:midpoint],
        variables[midpoint:]
    )
    values_by_variable = {}
    timestamps_by_batch = []
    elevations = []

    for batch_index, variable_batch in enumerate(variable_batches):
        if not variable_batch:
            continue

        if batch_index:
            print(
                f"Waiting {WEATHER_BATCH_DELAY_SECONDS} seconds "
                "before fetching the second half of hourly data..."
            )
            time.sleep(WEATHER_BATCH_DELAY_SECONDS)

        batch_params = request_params.copy()
        batch_params["hourly"] = ",".join(variable_batch)
        hourly_batch = _request_hourly(url, batch_params)
        _validate_hourly(hourly_batch, variable_batch)
        elevations.append(hourly_batch["surface_elevation_m"])

        batch_times = hourly_batch["time"]
        timestamps_by_batch.append(batch_times)

        for variable in variable_batch:
            values_by_variable[variable] = dict(
                zip(batch_times, hourly_batch[variable])
            )

    if not timestamps_by_batch:
        raise RuntimeError("No hourly data batches were requested.")

    common_times = set(timestamps_by_batch[0])
    for batch_times in timestamps_by_batch[1:]:
        common_times.intersection_update(batch_times)

    times = [
        timestamp
        for timestamp in timestamps_by_batch[0]
        if timestamp in common_times
    ]
    if not times:
        raise RuntimeError(
            "Hourly data batches did not return matching timestamps."
        )
    if any(
        not math.isclose(elevation, elevations[0], abs_tol=0.1)
        for elevation in elevations[1:]
    ):
        raise RuntimeError("Hourly data batches returned inconsistent elevations.")

    hourly = {
        "time": times,
        "surface_elevation_m": elevations[0],
    }
    for variable in variables:
        hourly[variable] = [
            values_by_variable[variable][timestamp]
            for timestamp in times
        ]

    return hourly



def get_weather():

    print("GETTING ICON-D2 WEATHER...")

    variables = params["hourly"].split(",")
    try:
        hourly = _fetch_hourly_in_batches(
            "https://api.open-meteo.com/v1/forecast",
            params,
            variables
        )
        primary_weather_source = "ICON-D2"
    except WeatherRateLimitError:
        raise
    except (requests.RequestException, RuntimeError) as error:
        print(f"WARNING: ICON-D2 weather request failed: {error}")
        print("Waiting 60 seconds before switching to ECMWF.")
        time.sleep(60)

        ecmwf_params = params.copy()
        ecmwf_params.pop("models", None)
        print("GETTING ECMWF WEATHER AFTER ICON-D2 FAILURE...")
        hourly = _fetch_hourly_in_batches(
            "https://api.open-meteo.com/v1/ecmwf",
            ecmwf_params,
            variables
        )
        primary_weather_source = "ECMWF fallback"

    print(f"PRIMARY WEATHER SOURCE: {primary_weather_source}")

    hourly["cape"] = [

        max(
            cape,
            0
        )
        if cape is not None
        else 0

        for cape in hourly["cape"]

    ]

    cin_source = "ICON-D2 estimate"

    def estimate_cin(index):
        temp = hourly["temperature_2m"][index]
        dewpoint = hourly["dew_point_2m"][index]
        temp850 = hourly["temperature_850hPa"][index]

        if temp is None or dewpoint is None or temp850 is None:
            return 0.0

        cin = -((temp - dewpoint) * 5)
        cin += (temp - temp850) * 3
        return round(max(min(cin, 0), -150), 1)



    try:
        print("GETTING ECMWF IFS CIN...")
        cin_payload = get_weather_json(
            "https://api.open-meteo.com/v1/forecast",
            cin_params,
            timeout=30,
            cache_seconds=21600,
        )
        cin_hourly = cin_payload.get("hourly", {})
        cin_times = cin_hourly.get("time", [])
        cin_values = cin_hourly.get("convective_inhibition", [])
        _validate_hourly(cin_hourly, ["convective_inhibition"])

        from zoneinfo import ZoneInfo
        germany_tz = ZoneInfo("Europe/Berlin")

        def timestamp(time_string):
            dt = datetime.fromisoformat(time_string)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=germany_tz)
            return int(dt.timestamp())

        cin_lookup = {
            timestamp(time_string): value
            for time_string, value in zip(cin_times, cin_values)
        }
        hourly["cin"] = []
        for time_string in hourly["time"]:
            value = cin_lookup.get(timestamp(time_string))
            hourly["cin"].append(-abs(float(value)) if value is not None else None)

        matched = sum(value is not None for value in hourly["cin"])
        print(f"ECMWF CIN MATCHED: {matched}/{len(hourly['cin'])}")
        cin_source = "ECMWF IFS" if matched == len(hourly["cin"]) else "ECMWF IFS + ICON-D2 estimate"
        for index, value in enumerate(hourly["cin"]):
            if value is None:
                hourly["cin"][index] = estimate_cin(index)

    except Exception as error:
        print("WARNING: ECMWF IFS CIN request failed.")
        print(f"CIN ERROR: {error}")
        print("Using ICON-D2-derived fallback CIN.")
        hourly["cin"] = [estimate_cin(index) for index in range(len(hourly["time"]))]


    valid_cin = [

        value

        for value in hourly["cin"]

        if value is not None

    ]


    if valid_cin:


        print(f"CIN SOURCE: {cin_source}")

        print(
            f"CIN RANGE: "
            f"{min(valid_cin):.1f} → "
            f"{max(valid_cin):.1f} J/kg"
        )


    return hourly
