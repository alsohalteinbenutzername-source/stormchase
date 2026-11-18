import math
from collections.abc import Mapping
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np

from stormanalyzer import analyze_snowstorm, analyze_storm


GERMANY_TZ = ZoneInfo(
    "Europe/Berlin"
)
NO_LFC_SENTINEL_M = 6000.0



def wind_to_vector(
    speed,
    direction
):


    rad = math.radians(
        direction
    )

    u = (
        -speed *
        math.sin(rad)
    )

    v = (
        -speed *
        math.cos(rad)
    )

    return u, v


def vector_difference(
    wind1,
    dir1,
    wind2,
    dir2
):


    u1, v1 = wind_to_vector(
        wind1,
        dir1
    )

    u2, v2 = wind_to_vector(
        wind2,
        dir2
    )

    du = u2 - u1
    dv = v2 - v1

    return math.sqrt(
        du ** 2 +
        dv ** 2
    )

def calculate_srh(
    sounding
):

    levels = [
        ("wind10", "dir10"),
        ("wind925", "dir925"),
        ("wind850", "dir850"),
        ("wind700", "dir700"),
        ("wind500", "dir500")
    ]

    vectors = []

    for wind, direction in levels:

        u, v = wind_to_vector(
            sounding[wind],
            sounding[direction]
        )

        vectors.append(
            (u, v)
        )

    srh = 0.0

    for i in range(
        len(vectors) - 1
    ):

        u1, v1 = vectors[i]
        u2, v2 = vectors[i + 1]

        srh += (
            u1 * v2
            -
            v1 * u2
        )

    srh = abs(
        srh
    ) * 0.15

    return min(
        round(srh, 1),
        400.0
    )


def _finite_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def calculate_storm_motion_features(sounding):
    """Derive near-surface shear, storm-relative helicity, and hodograph metrics."""
    layer_heights = {
        "wind10": 10.0,
        "wind180": 180.0,
        "wind925": _finite_float(sounding.get("height925")),
        "wind850": _finite_float(sounding.get("height850")),
        "wind700": _finite_float(sounding.get("height700")),
        "wind500": _finite_float(sounding.get("height500")),
    }

    profile = []
    for wind_name, height in layer_heights.items():
        if height is None:
            continue
        speed = _finite_float(sounding.get(wind_name))
        direction = _finite_float(sounding.get(f"dir{wind_name[4:]}"))
        if speed is None or direction is None or speed < 0 or height < 10.0:
            continue
        profile.append((height, speed / 3.6, direction))

    profile.sort(key=lambda point: point[0])
    if (
        len(profile) < 3
        or profile[0][0] != 10.0
        or not any(point[0] == 180.0 for point in profile)
        or profile[-1][0] < 3000.0
    ):
        raise ValueError("A complete surface-to-3 km wind profile is required.")

    heights = np.asarray([point[0] for point in profile], dtype=float)
    speeds = np.asarray([point[1] for point in profile], dtype=float)
    directions = np.deg2rad(np.asarray([point[2] for point in profile], dtype=float))
    components = np.column_stack(
        (-speeds * np.sin(directions), -speeds * np.cos(directions))
    )
    u_wind, v_wind = components[:, 0], components[:, 1]

    def wind_at(height):
        return (
            float(np.interp(height, heights, u_wind)),
            float(np.interp(height, heights, v_wind)),
        )

    def shear_between(bottom, top):
        u0, v0 = wind_at(bottom)
        u1, v1 = wind_at(top)
        return math.hypot(u1 - u0, v1 - v0)

    def mean_wind(bottom, top):
        interior = heights[(heights > bottom) & (heights < top)]
        levels = np.concatenate(([bottom], interior, [top]))
        u_values = np.interp(levels, heights, u_wind)
        v_values = np.interp(levels, heights, v_wind)
        layer_depths = np.diff(levels)
        return (
            float(np.sum((u_values[:-1] + u_values[1:]) * layer_depths / 2) / (top - bottom)),
            float(np.sum((v_values[:-1] + v_values[1:]) * layer_depths / 2) / (top - bottom)),
        )

    def bunkers_right_mover():
        top = min(6000.0, float(heights[-1]))
        if top <= 10.0:
            return None
        mean_u, mean_v = mean_wind(0.0, top)
        low_u, low_v = mean_wind(0.0, top / 2.0)
        high_u, high_v = mean_wind(top / 2.0, top)
        shear_u, shear_v = high_u - low_u, high_v - low_v
        shear_magnitude = math.hypot(shear_u, shear_v)
        if shear_magnitude < 1e-6:
            return None
        deviation = 7.5
        return (
            mean_u + deviation * shear_v / shear_magnitude,
            mean_v - deviation * shear_u / shear_magnitude,
        )

    storm_motion = bunkers_right_mover()

    def storm_relative_helicity(top):
        if storm_motion is None or heights[-1] < top:
            return None
        interior = heights[(heights > 0) & (heights < top)]
        levels = np.concatenate(([0.0], interior, [top]))
        u_values = np.interp(levels, heights, u_wind) - storm_motion[0]
        v_values = np.interp(levels, heights, v_wind) - storm_motion[1]
        du = np.diff(u_values)
        dv = np.diff(v_values)
        return max(float(np.sum(u_values[:-1] * dv - v_values[:-1] * du)), 0.0)

    surface_u, surface_v = wind_at(10.0)
    wind180_u, wind180_v = wind_at(180.0)
    shear_u, shear_v = wind180_u - surface_u, wind180_v - surface_v
    inflow_angle = None
    if storm_motion is not None:
        inflow_u = storm_motion[0] - surface_u
        inflow_v = storm_motion[1] - surface_v
        denominator = math.hypot(inflow_u, inflow_v) * math.hypot(shear_u, shear_v)
        if denominator > 1e-6:
            cosine = np.clip(
                (inflow_u * shear_u + inflow_v * shear_v) / denominator,
                -1.0,
                1.0,
            )
            inflow_angle = math.degrees(math.acos(float(cosine)))

    srh_1km = storm_relative_helicity(1000.0)
    srh_3km = storm_relative_helicity(3000.0)
    efficiency = 0.0
    if srh_1km is not None and storm_motion is not None:
        mean_u, mean_v = mean_wind(0.0, 1000.0)
        mean_storm_relative_wind = math.hypot(
            mean_u - storm_motion[0],
            mean_v - storm_motion[1],
        )
        theoretical_srh = mean_storm_relative_wind * shear_between(0.0, 1000.0)
        if theoretical_srh > 1e-6:
            efficiency = min(srh_1km / theoretical_srh, 1.0)

    return {
        "shear_10m_180m": shear_between(10.0, 180.0),
        "shear_0_500m": shear_between(0.0, 500.0),
        "shear_0_1km": shear_between(0.0, 1000.0),
        "critical_angle": inflow_angle if inflow_angle is not None else 0.0,
        "srh_0_1km": srh_1km if srh_1km is not None else 0.0,
        "srh_0_3km": srh_3km if srh_3km is not None else 0.0,
        "srh_efficiency": efficiency,
    }


def calculate_low_level_lapse_rate(sounding):
    """Interpolate the surface-to-2 km lapse rate from the model temperature profile."""
    surface = _finite_float(sounding.get("temperature_2m"))
    temp925 = _finite_float(sounding.get("temperature_925"))
    temp850 = _finite_float(sounding.get("temperature_850"))
    temp700 = _finite_float(sounding.get("temperature_700"))
    temp500 = _finite_float(sounding.get("temperature_500"))
    if (
        surface is None
        or temp925 is None
        or temp850 is None
        or temp700 is None
        or temp500 is None
    ):
        return None

    profile = [(2.0, surface)]
    for level, temperature in (
        (925, temp925),
        (850, temp850),
        (700, temp700),
        (500, temp500),
    ):
        height = _finite_float(sounding.get(f"height{level}"))
        if height is not None and height > profile[-1][0]:
            profile.append((height, temperature))
    profile_height = np.asarray([height for height, _ in profile], dtype=float)
    profile_temperature = np.asarray([temperature for _, temperature in profile], dtype=float)
    if profile_height[-1] < 2000.0:
        return None
    temperature_2km = float(np.interp(2000.0, profile_height, profile_temperature))
    return (surface - temperature_2km) / 2.0


def _hourly_required_value(hourly, index, field):
    values = hourly.get(field)
    if not isinstance(values, list) or index >= len(values):
        raise ValueError(f"Missing required ICON-D2 profile field: {field}.")
    value = _finite_float(values[index])
    if value is None:
        raise ValueError(f"Invalid required ICON-D2 profile value: {field}.")
    return value


def _saturation_vapor_pressure(temp_kelvin):
    temp_celsius = temp_kelvin - 273.15
    return 6.112 * math.exp(
        17.67 * temp_celsius / (temp_celsius + 243.5)
    )


def _mixing_ratio_from_vapor_pressure(vapor_pressure, pressure):
    epsilon = 0.622
    vapor_pressure = min(max(vapor_pressure, 0.0), pressure * 0.99)
    return epsilon * vapor_pressure / (pressure - vapor_pressure)


def _moist_adiabatic_lapse_rate(temp_kelvin, pressure_hpa):
    gas_constant_dry_air = 287.05
    heat_capacity = 1004.0
    latent_heat = 2.5e6
    epsilon = 0.622
    vapor_pressure = min(_saturation_vapor_pressure(temp_kelvin), pressure_hpa * 0.99)
    saturation_mixing_ratio = _mixing_ratio_from_vapor_pressure(
        vapor_pressure,
        pressure_hpa,
    )
    numerator = 9.80665 / heat_capacity * (
        1.0 + latent_heat * saturation_mixing_ratio
        / (gas_constant_dry_air * temp_kelvin)
    )
    denominator = 1.0 + (
        latent_heat**2 * saturation_mixing_ratio * epsilon
        / (heat_capacity * gas_constant_dry_air * temp_kelvin**2)
    )
    return numerator / denominator


def calculate_low_level_buoyancy_metrics(hourly, index):
    """Estimate surface-parcel 0–3 km CAPE and LFC from ICON-D2 profiles."""
    surface_temperature = _hourly_required_value(hourly, index, "temperature_2m") + 273.15
    surface_dewpoint = _hourly_required_value(hourly, index, "dew_point_2m") + 273.15
    surface_pressure = _hourly_required_value(hourly, index, "surface_pressure")
    surface_elevation = _finite_float(hourly.get("surface_elevation_m"))
    if surface_elevation is None:
        raise ValueError("Missing required ICON-D2 profile field: surface_elevation_m.")

    surface_dewpoint = min(surface_dewpoint, surface_temperature)
    surface_vapor_pressure = _saturation_vapor_pressure(surface_dewpoint)
    surface_mixing_ratio = _mixing_ratio_from_vapor_pressure(
        surface_vapor_pressure,
        surface_pressure,
    )
    profile = [
        (2.0, surface_pressure, surface_temperature, surface_mixing_ratio)
    ]
    for level in (925, 850, 700, 500):
        pressure = float(level)
        if pressure >= surface_pressure:
            continue
        temperature = _hourly_required_value(
            hourly,
            index,
            f"temperature_{level}hPa",
        ) + 273.15
        relative_humidity = _hourly_required_value(
            hourly,
            index,
            f"relative_humidity_{level}hPa",
        )
        geopotential_height = _hourly_required_value(
            hourly,
            index,
            f"geopotential_height_{level}hPa",
        )
        height = geopotential_height - surface_elevation
        if height <= profile[-1][0]:
            continue
        vapor_pressure = (
            _saturation_vapor_pressure(temperature)
            * min(max(relative_humidity, 0.0), 100.0)
            / 100.0
        )
        mixing_ratio = _mixing_ratio_from_vapor_pressure(vapor_pressure, pressure)
        profile.append((height, pressure, temperature, mixing_ratio))

    if profile[-1][0] < 3000.0:
        raise ValueError("ICON-D2 thermodynamic profile does not reach 3 km AGL.")

    heights = np.asarray([point[0] for point in profile], dtype=float)
    pressures = np.asarray([point[1] for point in profile], dtype=float)
    temperatures = np.asarray([point[2] for point in profile], dtype=float)
    mixing_ratios = np.asarray([point[3] for point in profile], dtype=float)
    log_pressures = np.log(pressures)

    temperature_k = surface_temperature
    dewpoint_k = surface_dewpoint
    lcl_temperature = 1.0 / (
        1.0 / (dewpoint_k - 56.0)
        + math.log(temperature_k / dewpoint_k) / 800.0
    ) + 56.0
    lcl_pressure = surface_pressure * (
        lcl_temperature / temperature_k
    ) ** (1004.0 / 287.05)
    lcl_height = float(np.interp(
        math.log(lcl_pressure),
        log_pressures[::-1],
        heights[::-1],
    ))
    lcl_height = min(max(lcl_height, 0.0), float(heights[-1]))

    def pressure_at(height):
        return math.exp(float(np.interp(height, heights, log_pressures)))

    def moist_derivative(height, temperature):
        return -_moist_adiabatic_lapse_rate(
            temperature,
            pressure_at(height),
        )

    def integrate_moist_parcel(start_height, start_temperature, end_height):
        temperature = start_temperature
        height = start_height
        while height < end_height:
            step = min(25.0, end_height - height)
            k1 = moist_derivative(height, temperature)
            k2 = moist_derivative(
                height + step / 2.0,
                temperature + step * k1 / 2.0,
            )
            k3 = moist_derivative(
                height + step / 2.0,
                temperature + step * k2 / 2.0,
            )
            k4 = moist_derivative(
                height + step,
                temperature + step * k3,
            )
            temperature += step * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
            height += step
        return temperature

    top = float(heights[-1])
    parcel_heights = np.unique(np.concatenate((
        np.arange(0.0, top, 25.0),
        np.asarray([0.0, 3000.0, lcl_height, top]),
    )))
    parcel_temperatures = []
    previous_height = 0.0
    previous_temperature = surface_temperature
    for height in parcel_heights:
        pressure = pressure_at(float(height))
        if height <= lcl_height:
            parcel_temperature = surface_temperature * (
                pressure / surface_pressure
            ) ** (287.05 / 1004.0)
        else:
            if previous_height <= lcl_height:
                previous_height = lcl_height
                previous_temperature = surface_temperature * (
                    lcl_pressure / surface_pressure
                ) ** (287.05 / 1004.0)
            parcel_temperature = integrate_moist_parcel(
                previous_height,
                previous_temperature,
                float(height),
            )
        parcel_temperatures.append(parcel_temperature)
        previous_height = float(height)
        previous_temperature = parcel_temperature

    environment_temperatures = np.interp(parcel_heights, heights, temperatures)
    environment_mixing_ratios = np.interp(
        parcel_heights,
        heights,
        mixing_ratios,
    )
    buoyancy = []
    for height, parcel_temperature, environment_temperature, environment_ratio in zip(
        parcel_heights,
        parcel_temperatures,
        environment_temperatures,
        environment_mixing_ratios,
    ):
        pressure = pressure_at(float(height))
        if height <= lcl_height:
            parcel_ratio = surface_mixing_ratio
        else:
            parcel_ratio = _mixing_ratio_from_vapor_pressure(
                _saturation_vapor_pressure(parcel_temperature),
                pressure,
            )
        parcel_virtual_temperature = parcel_temperature * (
            1.0 + 0.61 * parcel_ratio
        )
        environment_virtual_temperature = environment_temperature * (
            1.0 + 0.61 * environment_ratio
        )
        buoyancy.append(
            9.80665
            * (parcel_virtual_temperature - environment_virtual_temperature)
            / environment_virtual_temperature
        )

    buoyancy = np.asarray(buoyancy, dtype=float)
    low_level_mask = parcel_heights <= 3000.0
    low_level_heights = parcel_heights[low_level_mask]
    low_level_buoyancy = np.maximum(buoyancy[low_level_mask], 0.0)
    cape_0_3km = float(np.sum(
        (low_level_buoyancy[:-1] + low_level_buoyancy[1:])
        * np.diff(low_level_heights)
        / 2.0
    ))

    lfc = NO_LFC_SENTINEL_M
    lcl_index = int(np.searchsorted(parcel_heights, lcl_height))
    if buoyancy[lcl_index] >= 0.0:
        lfc = lcl_height
    else:
        for point in range(lcl_index, len(parcel_heights) - 1):
            if buoyancy[point] <= 0.0 < buoyancy[point + 1]:
                fraction = -buoyancy[point] / (
                    buoyancy[point + 1] - buoyancy[point]
                )
                lfc = float(
                    parcel_heights[point]
                    + fraction * (parcel_heights[point + 1] - parcel_heights[point])
                )
                break

    return {"cape_0_3km": cape_0_3km, "lfc": lfc}


def calculate_lcl(
    temp,
    dewpoint
):

    lcl = (
        125.0 *
        (
            temp -
            dewpoint
        )
    )

    return max(
        round(lcl, 0),
        0
    )


def normalize_cin(
    value
):

    if value is None:

        return 0.0

    try:

        value = float(
            value
        )

    except (
        TypeError,
        ValueError
    ):

        return 0.0

    return -abs(
        value
    )


def parse_time(
    time_string
):

    if not time_string:

        return None

    try:

        value = time_string.replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(
            value
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=GERMANY_TZ
            )

        else:

            dt = dt.astimezone(
                GERMANY_TZ
            )

        return dt

    except Exception:

        return None


def classify_time(
    time_string
):

    dt = parse_time(
        time_string
    )

    if dt is None:

        return "forecast"

    now = datetime.now(
        GERMANY_TZ
    )

    difference_minutes = (
        dt -
        now
    ).total_seconds() / 60.0

    if (
        -30 <=
        difference_minutes <
        30
    ):

        return "current"

    if difference_minutes < -30:

        return "past"

    return "forecast"


def get_current_time():

    return datetime.now(
        GERMANY_TZ
    )



def calculate_environment(
    hourly,
    i
):


    cape = hourly[
        "cape"
    ][i]

    temperature = hourly[
        "temperature_2m"
    ][i]

    dewpoint = hourly[
        "dew_point_2m"
    ][i]

    humidity = hourly[
        "relative_humidity_2m"
    ][i]
    temp925 = hourly["temperature_925hPa"][i]
    temp850 = hourly["temperature_850hPa"][i]

    cin = normalize_cin(
        hourly["cin"][i]
    )

    temp700 = hourly[
        "temperature_700hPa"
    ][i]

    temp500 = hourly[
        "temperature_500hPa"
    ][i]

    lapse = (
        temp700 -
        temp500
    ) / 3.0


    lcl = calculate_lcl(
        temperature,
        dewpoint
    )

    high_shear = vector_difference(

        hourly[
            "wind_speed_850hPa"
        ][i],

        hourly[
            "wind_direction_850hPa"
        ][i],

        hourly[
            "wind_speed_500hPa"
        ][i],

        hourly[
            "wind_direction_500hPa"
        ][i]

    ) / 1.852


    low_shear = vector_difference(

        hourly[
            "wind_speed_10m"
        ][i],

        hourly[
            "wind_direction_10m"
        ][i],

        hourly[
            "wind_speed_850hPa"
        ][i],

        hourly[
            "wind_direction_850hPa"
        ][i]

    ) / 1.852

    sounding = {

        "wind10":
            hourly[
                "wind_speed_10m"
            ][i],

        "dir10":
            hourly[
                "wind_direction_10m"
            ][i],

        "wind925":
            hourly[
                "wind_speed_925hPa"
            ][i],

        "dir925":
            hourly[
                "wind_direction_925hPa"
            ][i],

        "wind850":
            hourly[
                "wind_speed_850hPa"
            ][i],

        "dir850":
            hourly[
                "wind_direction_850hPa"
            ][i],

        "wind700":
            hourly[
                "wind_speed_700hPa"
            ][i],

        "dir700":
            hourly[
                "wind_direction_700hPa"
            ][i],

        "wind500":
            hourly[
                "wind_speed_500hPa"
            ][i],

        "dir500":
            hourly[
                "wind_direction_500hPa"
            ][i]
    }

    sounding["wind180"] = hourly["wind_speed_180m"][i]
    sounding["dir180"] = hourly["wind_direction_180m"][i]
    sounding["temperature_2m"] = temperature
    sounding["temperature_925"] = temp925
    sounding["temperature_850"] = temp850
    sounding["temperature_700"] = temp700
    sounding["temperature_500"] = temp500
    surface_elevation = _finite_float(hourly.get("surface_elevation_m"))
    if surface_elevation is None:
        raise ValueError("Missing required ICON-D2 profile field: surface_elevation_m.")
    sounding["height925"] = hourly["geopotential_height_925hPa"][i] - surface_elevation
    sounding["height850"] = hourly["geopotential_height_850hPa"][i] - surface_elevation
    sounding["height700"] = hourly["geopotential_height_700hPa"][i] - surface_elevation
    sounding["height500"] = hourly["geopotential_height_500hPa"][i] - surface_elevation


    srh = calculate_srh(
        sounding
    )
    motion_features = calculate_storm_motion_features(sounding)
    low_level_lapse = calculate_low_level_lapse_rate(sounding)
    if low_level_lapse is None:
        raise ValueError("ICON-D2 profile does not support a 0–2 km lapse rate.")
    buoyancy_features = calculate_low_level_buoyancy_metrics(hourly, i)

    return {

        "cape":
            round(
                float(cape),
                1
            ),

        "cin":
            round(
                cin,
                1
            ),

        "lcl":
            lcl,

        "temperature":
            round(
                float(temperature),
                1
            ),

        "dewpoint":
            round(
                float(dewpoint),
                1
            ),

        "humidity":
            round(
                float(humidity),
                1
            ),

        "precipitation":
            round(
                float(hourly["precipitation"][i] or 0),
                1
            ),

        "snowfall":
            round(
                float(hourly["snowfall"][i] or 0),
                2
            ),

        "wind_speed":
            round(
                float(hourly["wind_speed_10m"][i] or 0),
                1
            ),

        "srh":
            srh,

        "high_shear":
            round(
                high_shear,
                1
            ),

        "low_shear":
            round(
                low_shear,
                1
            ),

        "lapse":
            round(
                lapse,
                2
            ),

        "low_level_lapse":
            round(low_level_lapse, 2),

        "shear_10m_180m":
            motion_features["shear_10m_180m"],

        "shear_0_500m":
            motion_features["shear_0_500m"],

        "shear_0_1km":
            motion_features["shear_0_1km"],

        "critical_angle":
            motion_features["critical_angle"],

        "srh_0_1km":
            motion_features["srh_0_1km"],

        "srh_0_3km":
            motion_features["srh_0_3km"],

        "srh_efficiency":
            motion_features["srh_efficiency"],

        "cape_0_3km":
            buoyancy_features["cape_0_3km"],

        "lfc":
            buoyancy_features["lfc"],

        "sounding":
            sounding
    }


def calculate_manual_storm(values: Mapping[str, float]) -> dict:
    fields = (
        "cape",
        "high_shear",
        "low_shear",
        "dewpoint",
        "srh",
        "cin",
        "lcl",
        "humidity",
        "temperature",
        "lapse",
        "coverage",
        "srh_0_1km",
        "srh_0_3km",
        "srh_efficiency",
        "shear_0_500m",
        "critical_angle",
        "low_level_lapse",
        "cape_0_3km",
        "lfc",
    )
    parameters = {}
    for field in fields:
        try:
            value = float(values[field])
        except KeyError as error:
            raise ValueError(f"Missing manual input: {field}.") from error
        except (TypeError, ValueError) as error:
            raise ValueError(f"Manual input {field} must be a number.") from error
        if not math.isfinite(value):
            raise ValueError(f"Manual input {field} must be finite.")
        parameters[field] = value

    return analyze_storm(
        parameters["cape"],
        parameters["high_shear"],
        parameters["low_shear"],
        parameters["dewpoint"],
        parameters["srh"],
        parameters["cin"],
        parameters["lcl"],
        parameters["humidity"],
        parameters["temperature"],
        parameters["lapse"],
        parameters["coverage"],
        srh_0_1km=parameters["srh_0_1km"],
        srh_0_3km=parameters["srh_0_3km"],
        srh_efficiency=parameters["srh_efficiency"],
        shear_0_500m=parameters["shear_0_500m"],
        critical_angle=parameters["critical_angle"],
        low_level_lapse=parameters["low_level_lapse"],
        cape_0_3km=parameters["cape_0_3km"],
        lfc=parameters["lfc"],
    )


def nearest_outlook_entry(
    outlook: list[dict],
    day: int,
    latitude: float,
    longitude: float,
) -> dict | None:
    candidates = [
        entry
        for entry in outlook
        if entry.get("day") == day and isinstance(entry.get("environment"), dict)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda entry: (
            (entry["latitude"] - latitude) ** 2
            + ((entry["longitude"] - longitude) * math.cos(math.radians(latitude))) ** 2
        ),
    )


def _has_required_environment_data(hourly, index):
    required_variables = (
        "cape",
        "temperature_2m",
        "dew_point_2m",
        "relative_humidity_2m",
        "surface_pressure",
        "temperature_925hPa",
        "temperature_850hPa",
        "relative_humidity_925hPa",
        "geopotential_height_925hPa",
        "temperature_700hPa",
        "temperature_500hPa",
        "relative_humidity_850hPa",
        "relative_humidity_700hPa",
        "relative_humidity_500hPa",
        "geopotential_height_850hPa",
        "geopotential_height_700hPa",
        "geopotential_height_500hPa",
        "wind_speed_10m",
        "wind_direction_10m",
        "wind_speed_180m",
        "wind_direction_180m",
        "wind_speed_925hPa",
        "wind_direction_925hPa",
        "wind_speed_850hPa",
        "wind_direction_850hPa",
        "wind_speed_700hPa",
        "wind_direction_700hPa",
        "wind_speed_500hPa",
        "wind_direction_500hPa",
    )
    surface_elevation = _finite_float(hourly.get("surface_elevation_m"))
    has_required_data = (
        surface_elevation is not None
        and all(
            variable in hourly
            and index < len(hourly[variable])
            and _finite_float(hourly[variable][index]) is not None
            for variable in required_variables
        )
    )
    if not has_required_data:
        return False
    return (
        hourly["geopotential_height_500hPa"][index] - surface_elevation >= 3000.0
    )


def _storm_feature_arguments(environment):
    feature_names = (
        "srh_0_1km",
        "srh_0_3km",
        "srh_efficiency",
        "shear_0_500m",
        "critical_angle",
        "low_level_lapse",
        "cape_0_3km",
        "lfc",
    )
    return {name: environment[name] for name in feature_names}


def calculate_best_day_window(hourly, target_day: date, now: datetime | None = None):
    current_time = now or datetime.now(GERMANY_TZ)
    best = None
    best_score = float("-inf")

    for index, time_string in enumerate(hourly["time"]):
        dt = parse_time(time_string)
        if (
            dt is None
            or dt.date() != target_day
            or dt < current_time
            or not _has_required_environment_data(hourly, index)
        ):
            continue

        environment = calculate_environment(hourly, index)
        analysis = analyze_storm(
            environment["cape"],
            environment["high_shear"],
            environment["low_shear"],
            environment["dewpoint"],
            environment["srh"],
            environment["cin"],
            environment["lcl"],
            environment["humidity"],
            environment["temperature"],
            environment["lapse"],
            environment.get("coverage", 50),
            **_storm_feature_arguments(environment),
        )
        if analysis["risk"] > best_score:
            best_score = analysis["risk"]
            best = {
                **analysis,
                "environment": environment,
                "time": dt,
                "selection_score": best_score,
            }

    return best



def calculate_timeline(
    hourly
):
    """
    Builds the complete past/current/forecast timeline.
    """

    timeline = []

    times = hourly[
        "time"
    ]

    for i in range(
        len(times)
    ):

        if not _has_required_environment_data(hourly, i):
            continue

        result = calculate_environment(
            hourly,
            i
        )

        result["time"] = (
            times[i]
        )

        result["period"] = (
            classify_time(
                times[i]
            )
        )

        timeline.append(
            result
        )

    return timeline


def split_timeline(
    timeline
):
    """
    Splits timeline into past/current/forecast.
    """

    past = []
    current = []
    forecast = []

    for item in timeline:

        period = item.get(
            "period",
            "forecast"
        )

        if period == "past":

            past.append(
                item
            )

        elif period == "current":

            current.append(
                item
            )

        else:

            forecast.append(
                item
            )

    return {
        "past": past,
        "current": current,
        "forecast": forecast
    }


def calculate_forecast_window(
    timeline,
    hours=48
):
    """
    Returns the next `hours` forecast points.

    This remains a 48-hour forecast window and is NOT
    restricted to the current day.
    """

    forecast = [
        item
        for item in timeline
        if item.get(
            "period"
        ) == "forecast"
    ]

    return forecast[
        :hours
    ]



def score_environment(
    result,
    dt=None
):
    """
    Scores one environmental point.

    Higher score = more supportive environment.

    No artificial time-of-day adjustment is applied.
    """

    cape = float(
        result["cape"]
    )

    shear = float(
        result["high_shear"]
    )

    srh = float(
        result["srh"]
    )

    dewpoint = float(
        result["dewpoint"]
    )

    temp = float(
        result["temperature"]
    )

    cin = normalize_cin(
        result["cin"]
    )

    score = 0.0

    score += min(
        cape / 100.0,
        35.0
    )


    score += min(
        shear / 2.0,
        20.0
    )

    if dewpoint >= 20:

        score += 15

    elif dewpoint >= 18:

        score += 12

    elif dewpoint >= 15:

        score += 7

    elif dewpoint < 10:

        score -= 8

    score += min(
        srh / 25.0,
        8.0
    )


    if temp >= 25:

        score += 5

    if temp >= 30:

        score += 8


    if cin < -25 and cape < 400 :

        cin_penalty = (
            (abs(cin) - 25.0)
            / 25.0
        )

        cin_penalty = min(
            cin_penalty,
            20.0
        )

        score -= cin_penalty

    if cape < 100:

        score -= 50

    return round(
        score,
        1
    )



def calculate_best_window(
    hourly,
    winter_mode=False
):

    times = hourly[
        "time"
    ]

    now = datetime.now(
        GERMANY_TZ
    )

    today = now.date()

    best = None
    best_score = -999.0

    candidates = []

    for i in range(
        len(times)
    ):

        dt = parse_time(
            times[i]
        )

        if dt is None:

            continue



        if dt.date() != today:

            continue


        if dt < now:

            continue

        if not _has_required_environment_data(hourly, i):
            continue

        result = calculate_environment(
            hourly,
            i
        )

        if winter_mode:
            analysis = analyze_snowstorm(
                result["snowfall"],
                result["precipitation"],
                result["temperature"],
                result["wind_speed"]
            )
        else:
            analysis = analyze_storm(
                result["cape"],
                result["high_shear"],
                result["low_shear"],
                result["dewpoint"],
                result["srh"],
                result["cin"],
                result["lcl"],
                result["humidity"],
                result["temperature"],
                result["lapse"],
                result.get(
                    "coverage",
                    50
                ),
                **_storm_feature_arguments(result),
            )

        score = analysis["risk"]

        candidates.append(
            (
                dt,
                score,
                result,
                times[i]
            )
        )

        if score > best_score:

            best_score = score

            best = result.copy()

            best["time"] = (
                times[i]
            )

            best["selection_score"] = (
                score
            )

    print(
        "================================"
    )

    print(
        "BEST HOUR — CURRENT DAY"
    )

    print(
        f"DATE: {today}"
    )

    print(
        f"CANDIDATES: {len(candidates)}"
    )

    if best is not None:

        print(
            f"BEST: {best['time']}"
        )

        print(
            f"BEST WINDOW RISK: {best['selection_score']}/100"
        )

        print(
            f"CAPE: {best['cape']} J/kg"
        )

        print(
            f"CIN: {best['cin']} J/kg"
        )

        print(
            f"SHEAR: {best['high_shear']} kt"
        )

        print(
            f"SRH: {best['srh']}"
        )

    else:

        print(
            "NO REMAINING HOURS TODAY"
        )

    return best


def calculate_best_forecast_window(
    timeline,
    hours=48
):

    forecast = calculate_forecast_window(
        timeline,
        hours
    )

    if not forecast:

        return None

    best = None
    best_score = -999.0

    for item in forecast:

        dt = parse_time(
            item.get(
                "time"
            )
        )

        score = score_environment(
            item,
            dt
        )

        if score > best_score:

            best_score = score

            best = item.copy()

            best[
                "selection_score"
            ] = score

    return best