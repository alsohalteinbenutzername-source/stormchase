import math
import unittest

from publisher.severe_parameters import (
    PRESSURE_LEVELS_HPA,
    calculate_spc_composites,
    calculate_spc_effective_parameters,
)


class SpcCompositeTests(unittest.TestCase):
    def test_uses_spc_effective_layer_normalization(self) -> None:
        parameters = calculate_spc_composites(
            mlcape=1500,
            mllcl=800,
            mlcin=-50,
            mucape=1000,
            effective_srh=150,
            effective_bwd=20,
            effective_base_height=0,
        )

        self.assertEqual(parameters, {"stp": 1.0, "scp": 3.0})

    def test_applies_effective_layer_caps_and_cutoffs(self) -> None:
        parameters = calculate_spc_composites(
            mlcape=3000,
            mllcl=2500,
            mlcin=-350,
            mucape=5000,
            effective_srh=1000,
            effective_bwd=9,
            effective_base_height=100,
        )

        self.assertEqual(parameters, {"stp": 0.0, "scp": 0.0})


class EffectiveProfileTests(unittest.TestCase):
    def test_returns_unavailable_for_incomplete_profiles(self) -> None:
        self.assertEqual(
            calculate_spc_effective_parameters({}, 0),
            {"stp": None, "scp": None},
        )

    def test_calculates_finite_nonnegative_parameters_from_profile(self) -> None:
        hourly = {
            "surface_pressure": [1000],
            "temperature_2m": [25],
            "dew_point_2m": [18],
            "surface_elevation_m": 0,
            "wind_speed_10m": [10],
            "wind_direction_10m": [180],
        }
        for pressure in PRESSURE_LEVELS_HPA:
            height = 44330.0 * (1.0 - (pressure / 1013.25) ** 0.1903)
            temperature = 25.0 - 0.0065 * height
            value = None if pressure <= 150 else temperature
            hourly[f"temperature_{pressure}hPa"] = [value]
            hourly[f"relative_humidity_{pressure}hPa"] = [
                None if pressure <= 150 else 50
            ]
            hourly[f"geopotential_height_{pressure}hPa"] = [
                None if pressure <= 150 else height
            ]
            hourly[f"wind_speed_{pressure}hPa"] = [
                None if pressure <= 150 else 20 + height / 1000 * 5
            ]
            hourly[f"wind_direction_{pressure}hPa"] = [
                None if pressure <= 150 else 180 - height / 1000 * 15
            ]

        parameters = calculate_spc_effective_parameters(hourly, 0)

        self.assertEqual(set(parameters), {"stp", "scp"})
        self.assertTrue(all(
            value is not None and math.isfinite(value) and value >= 0
            for value in parameters.values()
        ))


if __name__ == "__main__":
    unittest.main()
