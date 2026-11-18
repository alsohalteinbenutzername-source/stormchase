from __future__ import annotations

import math
from typing import Any

import numpy as np


PRESSURE_LEVELS_HPA = (
    1000,
    975,
    950,
    925,
    900,
    875,
    850,
    825,
    800,
    750,
    700,
    650,
    600,
    550,
    500,
    450,
    400,
    350,
    300,
    250,
    200,
    150,
    100,
)
PRESSURE_LEVEL_VARIABLES = (
    "temperature",
    "relative_humidity",
    "geopotential_height",
    "wind_speed",
    "wind_direction",
)
PRESSURE_LEVEL_HOURLY_FIELDS = tuple(
    f"{variable}_{pressure}hPa"
    for pressure in PRESSURE_LEVELS_HPA
    for variable in PRESSURE_LEVEL_VARIABLES
)

_RD = 287.05
_CP = 1004.0
_G = 9.80665
_EPSILON = 0.622
_LV = 2.5e6
_KAPPA = _RD / _CP
_PARCEL_STEP_M = 100.0


def _finite_value(hourly: dict[str, Any], index: int, field: str) -> float | None:
    values = hourly.get(field)
    if not isinstance(values, list) or index < 0 or index >= len(values):
        return None
    try:
        value = float(values[index])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _saturation_vapor_pressure(temperature_k: float) -> float:
    temperature_c = temperature_k - 273.15
    return 6.112 * math.exp(
        17.67 * temperature_c / (temperature_c + 243.5)
    )


def _mixing_ratio(vapor_pressure: float, pressure: float) -> float:
    return _EPSILON * vapor_pressure / max(pressure - vapor_pressure, 0.1)


def _dewpoint_from_mixing_ratio(mixing_ratio: float, pressure: float) -> float:
    vapor_pressure = mixing_ratio * pressure / (_EPSILON + mixing_ratio)
    logarithm = math.log(max(vapor_pressure, 0.01) / 6.112)
    return 273.15 + 243.5 * logarithm / (17.67 - logarithm)


def _build_profile(
    hourly: dict[str, Any],
    index: int,
) -> tuple[list[dict[str, float]], list[dict[str, float]]] | None:
    surface_pressure = _finite_value(hourly, index, "surface_pressure")
    surface_temperature = _finite_value(hourly, index, "temperature_2m")
    surface_dewpoint = _finite_value(hourly, index, "dew_point_2m")
    surface_elevation = hourly.get("surface_elevation_m")
    if surface_elevation is None:
        return None
    try:
        surface_elevation = float(surface_elevation)
    except (TypeError, ValueError):
        return None
    if (
        surface_pressure is None
        or surface_temperature is None
        or surface_dewpoint is None
        or not math.isfinite(surface_elevation)
    ):
        return None

    surface_temperature += 273.15
    surface_dewpoint = min(surface_dewpoint + 273.15, surface_temperature)
    surface_mixing_ratio = _mixing_ratio(
        _saturation_vapor_pressure(surface_dewpoint),
        surface_pressure,
    )
    profile = [{
        "pressure": surface_pressure,
        "height": 0.0,
        "temperature": surface_temperature,
        "mixing_ratio": surface_mixing_ratio,
    }]

    wind_speed = _finite_value(hourly, index, "wind_speed_10m")
    wind_direction = _finite_value(hourly, index, "wind_direction_10m")
    winds = []
    if wind_speed is not None and wind_direction is not None:
        angle = math.radians(wind_direction)
        winds.append({
            "height": 10.0,
            "u": -wind_speed / 3.6 * math.sin(angle),
            "v": -wind_speed / 3.6 * math.cos(angle),
        })

    for pressure in PRESSURE_LEVELS_HPA:
        if pressure >= surface_pressure:
            continue
        temperature = _finite_value(
            hourly,
            index,
            f"temperature_{pressure}hPa",
        )
        humidity = _finite_value(
            hourly,
            index,
            f"relative_humidity_{pressure}hPa",
        )
        geopotential_height = _finite_value(
            hourly,
            index,
            f"geopotential_height_{pressure}hPa",
        )
        if (
            temperature is None
            or humidity is None
            or geopotential_height is None
        ):
            continue

        height = geopotential_height - surface_elevation
        if height <= profile[-1]["height"]:
            continue
        temperature += 273.15
        humidity = min(max(humidity, 0.0), 100.0)
        vapor_pressure = (
            _saturation_vapor_pressure(temperature) * humidity / 100.0
        )
        profile.append({
            "pressure": float(pressure),
            "height": height,
            "temperature": temperature,
            "mixing_ratio": _mixing_ratio(vapor_pressure, pressure),
        })

        wind_speed = _finite_value(hourly, index, f"wind_speed_{pressure}hPa")
        wind_direction = _finite_value(
            hourly,
            index,
            f"wind_direction_{pressure}hPa",
        )
        if wind_speed is not None and wind_direction is not None:
            angle = math.radians(wind_direction)
            winds.append({
                "height": height,
                "u": -wind_speed / 3.6 * math.sin(angle),
                "v": -wind_speed / 3.6 * math.cos(angle),
            })

    if len(profile) < 8 or profile[-1]["height"] < 10000.0 or len(winds) < 4:
        return None
    winds.sort(key=lambda point: point["height"])
    return profile, winds


def _parcel_diagnostics(
    profile: list[dict[str, float]],
    start_pressure: float,
    start_temperature: float,
    start_mixing_ratio: float,
) -> dict[str, float | None]:
    heights = np.asarray([point["height"] for point in profile], dtype=float)
    pressures = np.asarray([point["pressure"] for point in profile], dtype=float)
    log_pressures = np.log(pressures)
    temperatures = np.asarray([point["temperature"] for point in profile], dtype=float)
    mixing_ratios = np.asarray(
        [point["mixing_ratio"] for point in profile],
        dtype=float,
    )
    start_height = float(np.interp(
        math.log(start_pressure),
        log_pressures[::-1],
        heights[::-1],
    ))
    start_dewpoint = _dewpoint_from_mixing_ratio(
        start_mixing_ratio,
        start_pressure,
    )
    start_dewpoint = min(start_dewpoint, start_temperature)
    lcl_temperature = 1.0 / (
        1.0 / (start_dewpoint - 56.0)
        + math.log(start_temperature / start_dewpoint) / 800.0
    ) + 56.0
    lcl_pressure = start_pressure * (
        lcl_temperature / start_temperature
    ) ** (1.0 / _KAPPA)
    lcl_height = float(np.interp(
        math.log(lcl_pressure),
        log_pressures[::-1],
        heights[::-1],
    ))
    lcl_height = min(max(lcl_height, start_height), float(heights[-1]))
    lcl_temperature = start_temperature * (
        lcl_pressure / start_pressure
    ) ** _KAPPA

    def pressure_at(height: float) -> float:
        return math.exp(float(np.interp(height, heights, log_pressures)))

    def moist_lapse_rate(temperature: float, pressure: float) -> float:
        saturation = _saturation_vapor_pressure(temperature)
        saturation_ratio = _mixing_ratio(saturation, pressure)
        numerator = _G * (
            1.0 + _LV * saturation_ratio / (_RD * temperature)
        )
        denominator = _CP + (
            _LV**2 * saturation_ratio * _EPSILON
            / (_RD * temperature**2)
        )
        return numerator / denominator

    def integrate_moist(
        height: float,
        temperature: float,
        end_height: float,
    ) -> float:
        while height < end_height:
            step = min(_PARCEL_STEP_M, end_height - height)

            def derivative(at_height: float, at_temperature: float) -> float:
                return -moist_lapse_rate(
                    at_temperature,
                    pressure_at(at_height),
                )

            k1 = derivative(height, temperature)
            k2 = derivative(
                height + step / 2.0,
                temperature + step * k1 / 2.0,
            )
            k3 = derivative(
                height + step / 2.0,
                temperature + step * k2 / 2.0,
            )
            k4 = derivative(
                height + step,
                temperature + step * k3,
            )
            temperature += step * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
            height += step
        return temperature

    parcel_heights = np.unique(np.concatenate((
        np.arange(start_height, heights[-1], _PARCEL_STEP_M),
        np.asarray([start_height, lcl_height, heights[-1]]),
    )))
    parcel_temperatures = []
    previous_height = start_height
    previous_temperature = start_temperature
    for height in parcel_heights:
        pressure = pressure_at(float(height))
        if height <= lcl_height:
            parcel_temperature = start_temperature * (
                pressure / start_pressure
            ) ** _KAPPA
        else:
            if previous_height <= lcl_height:
                previous_height = lcl_height
                previous_temperature = lcl_temperature
            parcel_temperature = integrate_moist(
                previous_height,
                previous_temperature,
                float(height),
            )
        parcel_temperatures.append(parcel_temperature)
        previous_height = float(height)
        previous_temperature = parcel_temperature

    environment_temperatures = np.interp(
        parcel_heights,
        heights,
        temperatures,
    )
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
        parcel_ratio = (
            start_mixing_ratio
            if height <= lcl_height
            else _mixing_ratio(
                _saturation_vapor_pressure(parcel_temperature),
                pressure,
            )
        )
        parcel_virtual_temperature = parcel_temperature * (
            1.0 + 0.61 * parcel_ratio
        )
        environment_virtual_temperature = environment_temperature * (
            1.0 + 0.61 * environment_ratio
        )
        buoyancy.append(
            _G
            * (parcel_virtual_temperature - environment_virtual_temperature)
            / environment_virtual_temperature
        )
    buoyancy = np.asarray(buoyancy, dtype=float)

    def signed_area(
        lower_height: float,
        upper_height: float,
        positive: bool,
    ) -> float:
        integration_heights = np.concatenate((
            np.asarray([lower_height]),
            parcel_heights[
                (parcel_heights > lower_height)
                & (parcel_heights < upper_height)
            ],
            np.asarray([upper_height]),
        ))
        integration_buoyancy = np.interp(
            integration_heights,
            parcel_heights,
            buoyancy,
        )
        if positive:
            integration_buoyancy = np.maximum(integration_buoyancy, 0.0)
        else:
            integration_buoyancy = np.maximum(-integration_buoyancy, 0.0)
        return float(np.sum(
            (integration_buoyancy[:-1] + integration_buoyancy[1:])
            * np.diff(integration_heights)
            / 2.0
        ))

    lcl_index = int(np.searchsorted(parcel_heights, lcl_height))
    lfc_height = None
    if buoyancy[lcl_index] >= 0.0:
        lfc_height = lcl_height
    else:
        for point in range(lcl_index, len(parcel_heights) - 1):
            if buoyancy[point] <= 0.0 < buoyancy[point + 1]:
                fraction = -buoyancy[point] / (
                    buoyancy[point + 1] - buoyancy[point]
                )
                lfc_height = float(
                    parcel_heights[point]
                    + fraction
                    * (parcel_heights[point + 1] - parcel_heights[point])
                )
                break

    if lfc_height is None:
        return {
            "cape": 0.0,
            "cin": -signed_area(start_height, float(parcel_heights[-1]), False),
            "lcl": lcl_height,
            "el": None,
        }

    el_height = float(parcel_heights[-1])
    for point in range(int(np.searchsorted(parcel_heights, lfc_height)), len(parcel_heights) - 1):
        if buoyancy[point] >= 0.0 > buoyancy[point + 1]:
            fraction = buoyancy[point] / (
                buoyancy[point] - buoyancy[point + 1]
            )
            el_height = float(
                parcel_heights[point]
                + fraction
                * (parcel_heights[point + 1] - parcel_heights[point])
            )
            break
    return {
        "cape": signed_area(lfc_height, el_height, True),
        "cin": -signed_area(start_height, lfc_height, False),
        "lcl": lcl_height,
        "el": el_height,
    }


def _profile_value(
    profile: list[dict[str, float]],
    pressure: float,
    field: str,
) -> float:
    pressures = np.asarray([point["pressure"] for point in profile], dtype=float)
    values = np.asarray([point[field] for point in profile], dtype=float)
    return float(np.interp(
        math.log(pressure),
        np.log(pressures)[::-1],
        values[::-1],
    ))


def _wind_at_height(
    winds: list[dict[str, float]],
    height: float,
) -> tuple[float, float]:
    heights = np.asarray([point["height"] for point in winds], dtype=float)
    u = np.asarray([point["u"] for point in winds], dtype=float)
    v = np.asarray([point["v"] for point in winds], dtype=float)
    return (
        float(np.interp(height, heights, u)),
        float(np.interp(height, heights, v)),
    )


def _mean_wind(
    winds: list[dict[str, float]],
    lower_height: float,
    upper_height: float,
) -> tuple[float, float]:
    heights = [lower_height]
    heights.extend(
        point["height"]
        for point in winds
        if lower_height < point["height"] < upper_height
    )
    heights.append(upper_height)
    heights.sort()
    components = [_wind_at_height(winds, height) for height in heights]
    depth = upper_height - lower_height
    u_mean = sum(
        (components[index][0] + components[index + 1][0])
        * (heights[index + 1] - heights[index])
        / 2.0
        for index in range(len(heights) - 1)
    ) / depth
    v_mean = sum(
        (components[index][1] + components[index + 1][1])
        * (heights[index + 1] - heights[index])
        / 2.0
        for index in range(len(heights) - 1)
    ) / depth
    return u_mean, v_mean


def _bunkers_right_motion(winds: list[dict[str, float]]) -> tuple[float, float]:
    mean_u, mean_v = _mean_wind(winds, 0.0, 6000.0)
    surface_u, surface_v = _wind_at_height(winds, 0.0)
    six_km_u, six_km_v = _wind_at_height(winds, 6000.0)
    shear_u = six_km_u - surface_u
    shear_v = six_km_v - surface_v
    shear_magnitude = math.hypot(shear_u, shear_v)
    if shear_magnitude <= 1e-6:
        return mean_u, mean_v
    return (
        mean_u + 7.5 * shear_v / shear_magnitude,
        mean_v - 7.5 * shear_u / shear_magnitude,
    )


def _effective_srh(
    winds: list[dict[str, float]],
    lower_height: float,
    upper_height: float,
    storm_u: float,
    storm_v: float,
) -> float:
    if upper_height <= lower_height:
        return 0.0
    heights = [lower_height]
    heights.extend(
        point["height"]
        for point in winds
        if lower_height < point["height"] < upper_height
    )
    heights.append(upper_height)
    heights.sort()
    components = [_wind_at_height(winds, height) for height in heights]
    helicity = 0.0
    for index in range(len(heights) - 1):
        u0, v0 = components[index]
        u1, v1 = components[index + 1]
        helicity += (
            (u0 + u1) / 2.0 - storm_u
        ) * (v1 - v0) - (
            (v0 + v1) / 2.0 - storm_v
        ) * (u1 - u0)
    return max(helicity, 0.0)


def calculate_spc_effective_parameters(
    hourly: dict[str, Any],
    index: int,
) -> dict[str, float] | dict[str, None]:
    """Estimate SPC effective-layer STP and SCP from an interpolated ICON profile."""
    sounding = _build_profile(hourly, index)
    if sounding is None:
        return {"stp": None, "scp": None}
    profile, winds = sounding
    surface_pressure = profile[0]["pressure"]
    profile_bottom = profile[-1]["pressure"]
    if profile_bottom > surface_pressure - 400.0:
        return {"stp": None, "scp": None}

    mixed_layer_top = max(surface_pressure - 100.0, profile_bottom)
    pressures = np.linspace(surface_pressure, mixed_layer_top, 11)
    layer_depth = surface_pressure - mixed_layer_top
    if layer_depth <= 0.0:
        return {"stp": None, "scp": None}
    potential_temperatures = []
    mixing_ratios = []
    for pressure in pressures:
        temperature = _profile_value(profile, float(pressure), "temperature")
        mixing_ratio = _profile_value(profile, float(pressure), "mixing_ratio")
        potential_temperatures.append(
            temperature * (1000.0 / pressure) ** _KAPPA
        )
        mixing_ratios.append(mixing_ratio)
    mean_theta = sum(
        (potential_temperatures[i] + potential_temperatures[i + 1])
        * (pressures[i] - pressures[i + 1])
        / 2.0
        for i in range(len(pressures) - 1)
    ) / layer_depth
    mean_mixing_ratio = sum(
        (mixing_ratios[i] + mixing_ratios[i + 1])
        * (pressures[i] - pressures[i + 1])
        / 2.0
        for i in range(len(pressures) - 1)
    ) / layer_depth
    mixed_temperature = mean_theta * (surface_pressure / 1000.0) ** _KAPPA
    mixed_diagnostics = _parcel_diagnostics(
        profile,
        surface_pressure,
        mixed_temperature,
        mean_mixing_ratio,
    )

    candidate_pressures = [
        float(pressure)
        for pressure in np.arange(
            surface_pressure,
            max(profile_bottom, surface_pressure - 500.0) - 1e-6,
            -50.0,
        )
    ]
    if not candidate_pressures or candidate_pressures[0] != surface_pressure:
        candidate_pressures.insert(0, surface_pressure)

    candidates = []
    for pressure in candidate_pressures:
        temperature = _profile_value(profile, pressure, "temperature")
        mixing_ratio = _profile_value(profile, pressure, "mixing_ratio")
        diagnostics = _parcel_diagnostics(
            profile,
            pressure,
            temperature,
            mixing_ratio,
        )
        height = float(np.interp(
            math.log(pressure),
            np.log([point["pressure"] for point in profile])[::-1],
            [point["height"] for point in profile][::-1],
        ))
        candidates.append({
            "pressure": pressure,
            "height": height,
            **diagnostics,
        })

    most_unstable_candidates = [
        candidate
        for candidate in candidates
        if candidate["pressure"] >= surface_pressure - 300.0
    ]
    most_unstable = max(
        most_unstable_candidates,
        key=lambda candidate: float(candidate["cape"]),
    )
    qualifying = [
        candidate["cape"] >= 100.0 and candidate["cin"] >= -250.0
        for candidate in candidates
    ]
    effective_base = None
    effective_top = None
    for candidate_index, is_effective in enumerate(qualifying):
        if not is_effective:
            continue
        effective_base = candidates[candidate_index]["height"]
        effective_top = effective_base
        for top_index in range(candidate_index + 1, len(candidates)):
            if not qualifying[top_index]:
                break
            effective_top = candidates[top_index]["height"]
        break

    effective_srh = 0.0
    effective_bwd = 0.0
    if effective_base is not None and effective_top is not None:
        storm_u, storm_v = _bunkers_right_motion(winds)
        effective_srh = _effective_srh(
            winds,
            effective_base,
            effective_top,
            storm_u,
            storm_v,
        )
        equilibrium_level = most_unstable["el"]
        if equilibrium_level is not None and equilibrium_level > effective_base:
            end_height = (effective_base + float(equilibrium_level)) / 2.0
            end_u, end_v = _wind_at_height(winds, end_height)
            base_u, base_v = _wind_at_height(winds, effective_base)
            effective_bwd = math.hypot(end_u - base_u, end_v - base_v)

    mlcape = mixed_diagnostics["cape"]
    mllcl = mixed_diagnostics["lcl"]
    mlcin = mixed_diagnostics["cin"]
    mucape = most_unstable["cape"]
    if (
        mlcape is None
        or mllcl is None
        or mlcin is None
        or mucape is None
    ):
        return {"stp": None, "scp": None}

    return calculate_spc_composites(
        mlcape=mlcape,
        mllcl=mllcl,
        mlcin=mlcin,
        mucape=mucape,
        effective_srh=effective_srh,
        effective_bwd=effective_bwd,
        effective_base_height=effective_base,
    )


def calculate_spc_composites(
    mlcape: float,
    mllcl: float,
    mlcin: float,
    mucape: float,
    effective_srh: float,
    effective_bwd: float,
    effective_base_height: float | None,
) -> dict[str, float]:
    """Apply the SPC effective-layer STP and SCP normalizations."""
    if effective_base_height is None or effective_base_height > 50.0:
        stp = 0.0
    else:
        lcl_factor = (
            1.0
            if mllcl < 1000.0
            else max((2000.0 - mllcl) / 1000.0, 0.0)
        )
        ebwd_factor = (
            0.0
            if effective_bwd < 12.5
            else min(effective_bwd / 20.0, 1.5)
        )
        cin_factor = (
            1.0
            if mlcin > -50.0
            else max((mlcin + 200.0) / 150.0, 0.0)
        )
        stp = (
            max(mlcape, 0.0) / 1500.0
            * lcl_factor
            * max(effective_srh, 0.0) / 150.0
            * ebwd_factor
            * cin_factor
        )

    scp_bwd_factor = (
        0.0
        if effective_bwd < 10.0
        else min(effective_bwd / 20.0, 1.0)
    )
    scp = (
        max(mucape, 0.0) / 1000.0
        * max(effective_srh, 0.0) / 50.0
        * scp_bwd_factor
    )
    return {"stp": stp, "scp": scp}
