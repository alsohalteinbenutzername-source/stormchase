from datetime import datetime
import math
import unittest
from zoneinfo import ZoneInfo

from calculations import (
    calculate_best_day_window,
    calculate_environment,
    calculate_manual_storm,
    calculate_storm_motion_features,
)
from stormanalyzer import analyze_snowstorm, analyze_storm


class CalculateManualStormTests(unittest.TestCase):
    def setUp(self) -> None:
        self.values = {
            "cape": 2000,
            "high_shear": 35,
            "low_shear": 20,
            "dewpoint": 18,
            "srh": 150,
            "cin": -50,
            "lcl": 1000,
            "humidity": 70,
            "temperature": 30,
            "lapse": 6.5,
            "coverage": 100,
        }

    def test_calculates_manual_values_with_stormanalyzer(self) -> None:
        expected = analyze_storm(
            self.values["cape"],
            self.values["high_shear"],
            self.values["low_shear"],
            self.values["dewpoint"],
            self.values["srh"],
            self.values["cin"],
            self.values["lcl"],
            self.values["humidity"],
            self.values["temperature"],
            self.values["lapse"],
            self.values["coverage"],
        )

        self.assertEqual(calculate_manual_storm(self.values), expected)

    def test_rejects_missing_or_non_finite_manual_values(self) -> None:
        missing = dict(self.values)
        del missing["cape"]
        with self.assertRaisesRegex(ValueError, "Missing manual input: cape"):
            calculate_manual_storm(missing)

        non_finite = dict(self.values, cape=float("nan"))
        with self.assertRaisesRegex(ValueError, "cape must be finite"):
            calculate_manual_storm(non_finite)

    def test_calibrates_tornado_score_by_the_final_overall_risk(self) -> None:
        low_risk = analyze_storm(100, 35, 29, 20, 300, -150, 850, 85, 30, 7.5)
        high_risk = analyze_storm(1000, 35, 29, 20, 300, -50, 850, 85, 30, 7.5)

        self.assertAlmostEqual(low_risk["risk"], 0.58)
        self.assertAlmostEqual(low_risk["tornado"], 0.82)
        self.assertAlmostEqual(high_risk["risk"], 75.24)
        self.assertAlmostEqual(high_risk["tornado"], 61.08)

    def test_distinguishes_hslc_tornadoes_from_warm_season_hail_supercells(self) -> None:
        hslc = analyze_storm(
            250, 40, 25, 16, 400, -30, 600, 80, 16, 7.0,
            srh_0_1km=400,
            srh_0_3km=450,
            srh_efficiency=0.8,
            shear_0_500m=12,
            critical_angle=90,
            low_level_lapse=6.0,
            cape_0_3km=250,
            lfc=500,
        )
        warm_hail_supercell = analyze_storm(
            3500, 50, 15, 22, 100, -20, 1600, 60, 32, 8.0,
            srh_0_1km=50,
            srh_0_3km=350,
            srh_efficiency=0.1,
            shear_0_500m=2.5,
            critical_angle=35,
            low_level_lapse=4.2,
            cape_0_3km=100,
            lfc=2200,
        )
        elevated_rotation = analyze_storm(
            3500, 50, 25, 22, 350, -20, 700, 80, 30, 7.5,
            srh_0_1km=50,
            srh_0_3km=400,
            srh_efficiency=0.1,
            shear_0_500m=12,
            critical_angle=90,
            low_level_lapse=6.5,
            cape_0_3km=300,
            lfc=500,
        )

        self.assertGreaterEqual(hslc["tornado"], 50)
        self.assertLess(warm_hail_supercell["tornado"], 50)
        self.assertGreaterEqual(warm_hail_supercell["supercell"], 15)
        self.assertGreaterEqual(warm_hail_supercell["hail"], 50)
        self.assertLess(elevated_rotation["tornado"], 50)
        self.assertGreaterEqual(elevated_rotation["supercell"], 15)

    def test_cin_gate_only_dampens_tornado_score(self) -> None:
        profile = {
            "cape": 250,
            "high_shear": 40,
            "low_shear": 25,
            "dewpoint": 16,
            "srh": 400,
            "lcl": 600,
            "rh": 80,
            "temp": 16,
            "lapse": 7.0,
            "srh_0_1km": 400,
            "srh_0_3km": 450,
            "srh_efficiency": 0.8,
            "shear_0_500m": 12,
            "critical_angle": 90,
            "low_level_lapse": 6.0,
            "cape_0_3km": 250,
            "lfc": 500,
        }
        weak_cin = analyze_storm(cin=-30, **profile)
        strong_cin = analyze_storm(cin=-180, **profile)

        self.assertLess(strong_cin["tornado"], weak_cin["tornado"])
        self.assertEqual(strong_cin["supercell"], weak_cin["supercell"])
        self.assertEqual(strong_cin["hail"], weak_cin["hail"])

    def test_derives_near_surface_vector_shear_and_tolerates_nan_optional_winds(self) -> None:
        sounding = {
            "wind10": 10,
            "dir10": 180,
            "wind180": float("nan"),
            "dir180": float("nan"),
            "wind925": 30,
            "dir925": 180,
            "wind850": 40,
            "dir850": 180,
            "wind700": 50,
            "dir700": 180,
            "wind500": 60,
            "dir500": 180,
        }

        features = calculate_storm_motion_features(sounding)

        self.assertTrue(math.isfinite(features["shear_10m_180m"]))
        self.assertTrue(math.isfinite(features["shear_0_500m"]))
        expected_shear = (20 * (180 - 10) / (750 - 10)) / 3.6
        self.assertAlmostEqual(features["shear_10m_180m"], expected_shear)

    def test_keeps_extreme_environment_scores_out_of_the_near_100_range(self) -> None:
        analysis = analyze_storm(3700, 35, 29, 20, 300, 0, 850, 85, 30, 7.5)

        self.assertAlmostEqual(analysis["risk"], 89.22)
        self.assertLess(analysis["risk"], 95)
        self.assertLessEqual(analysis["tornado"], 80)
        self.assertLessEqual(analysis["hail"], 80)
        self.assertLessEqual(analysis["wind"], 80)

    def test_doubles_snowstorm_risk_to_the_new_maximum(self) -> None:
        analysis = analyze_snowstorm(10, 5, -5, 55)

        self.assertEqual(analysis["risk"], 100)
        self.assertEqual(analysis["wind"], 20)


class CalculateBestDayWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 11, 13, 17, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        self.hourly = {
            "time": [
                "2026-11-13T18:00",
                "2026-11-13T19:00",
                "2026-11-13T20:00",
                "2026-11-14T12:00",
            ],
            "cape": [5000, 100, 1000, 2500],
            "cin": [-50, -50, -50, -50],
            "temperature_2m": [25, 25, 25, 25],
            "dew_point_2m": [None, 18, 18, 18],
            "relative_humidity_2m": [70, 70, 70, 70],
            "temperature_700hPa": [10, 10, 10, 10],
            "temperature_500hPa": [-10, -10, -10, -10],
            "precipitation": [0, 0, 0, 0],
            "snowfall": [0, 0, 0, 0],
            "wind_speed_10m": [10, 10, 10, 10],
            "wind_direction_10m": [180, 180, 180, 180],
            "wind_speed_925hPa": [15, 15, 15, 15],
            "wind_direction_925hPa": [180, 180, 180, 180],
            "wind_speed_850hPa": [20, 20, 20, 20],
            "wind_direction_850hPa": [180, 180, 180, 180],
            "wind_speed_700hPa": [30, 30, 30, 30],
            "wind_direction_700hPa": [180, 180, 180, 180],
            "wind_speed_500hPa": [40, 40, 40, 40],
            "wind_direction_500hPa": [180, 180, 180, 180],
        }

    def test_selects_best_valid_hour_separately_for_day_one_and_two(self) -> None:
        day_one = calculate_best_day_window(
            self.hourly,
            self.now.date(),
            self.now,
        )
        day_two = calculate_best_day_window(
            self.hourly,
            self.now.date().replace(day=14),
            self.now,
        )

        self.assertIsNotNone(day_one)
        self.assertIsNotNone(day_two)
        self.assertEqual(day_one["time"].hour, 20)
        self.assertEqual(day_one["environment"]["cape"], 1000)
        self.assertEqual(day_two["time"].hour, 12)
        self.assertEqual(day_two["environment"]["cape"], 2500)

    def test_extracts_optional_low_level_fields_and_skips_nan_required_values(self) -> None:
        hourly = dict(self.hourly)
        hourly["temperature_850hPa"] = [10, 10, 10, 10]
        hourly["wind_speed_180m"] = [float("nan")] * 4
        hourly["wind_direction_180m"] = [float("nan")] * 4
        hourly["cape_0_3km"] = [float("nan")] * 4
        hourly["lfc_height_m"] = [float("nan")] * 4
        hourly["temperature_2m"] = [25, 25, float("nan"), 25]

        environment = calculate_environment(hourly, 1)
        best = calculate_best_day_window(hourly, self.now.date(), self.now)

        self.assertIsNone(environment["cape_0_3km"])
        self.assertIsNone(environment["lfc"])
        self.assertTrue(math.isfinite(environment["shear_0_500m"]))
        self.assertIsNotNone(environment["low_level_lapse"])
        self.assertEqual(best["time"].hour, 19)


if __name__ == "__main__":
    unittest.main()
