from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from calculations import calculate_best_day_window, calculate_manual_storm
from stormanalyzer import analyze_storm


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


if __name__ == "__main__":
    unittest.main()
