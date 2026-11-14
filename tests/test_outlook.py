from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from calculations import nearest_outlook_entry
from ui import calculate_germany_outlook, germany_grid_points


class NearestOutlookEntryTests(unittest.TestCase):
    def test_uses_the_nearest_forecast_for_the_selected_day(self) -> None:
        nearby = {
            "day": 1,
            "latitude": 51.1,
            "longitude": 7.2,
            "environment": {},
        }
        outlook = [
            {"day": 0, "latitude": 51.0, "longitude": 7.0, "environment": {}},
            nearby,
            {"day": 1, "latitude": 53.0, "longitude": 11.0, "environment": {}},
        ]

        self.assertIs(nearest_outlook_entry(outlook, 1, 51.0, 7.0), nearby)

    def test_returns_none_when_day_has_no_calculation(self) -> None:
        outlook = [{"day": 0, "latitude": 51.0, "longitude": 7.0}]

        self.assertIsNone(nearest_outlook_entry(outlook, 1, 51.0, 7.0))


class GermanyOutlookGridTests(unittest.TestCase):
    def test_builds_every_point_in_the_requested_grid(self) -> None:
        points = germany_grid_points(())

        self.assertEqual(len(points), 744)
        self.assertEqual(len(set(points)), len(points))
        self.assertEqual(points[0], (47.0, 4.0))
        self.assertEqual(points[-1], (56.0, 16.0))

    def test_calculates_both_days_for_every_returned_point(self) -> None:
        points = [(50.1, 7.1), (51.2, 8.2), (52.3, 9.3)]
        start = datetime.now(ZoneInfo("Europe/Berlin")).replace(
            minute=0,
            second=0,
            microsecond=0,
        ) + timedelta(hours=1)

        def hourly_data(hours: int, cape: float) -> dict[str, list]:
            times = [
                (start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M")
                for index in range(hours)
            ]
            values = {
                "cape": cape,
                "cin": -50,
                "temperature_2m": 25,
                "dew_point_2m": 18,
                "relative_humidity_2m": 70,
                "temperature_700hPa": 10,
                "temperature_500hPa": -10,
                "precipitation": 0,
                "snowfall": 0,
                "wind_speed_10m": 10,
                "wind_direction_10m": 180,
                "wind_speed_925hPa": 15,
                "wind_direction_925hPa": 180,
                "wind_speed_850hPa": 20,
                "wind_direction_850hPa": 180,
                "wind_speed_700hPa": 30,
                "wind_direction_700hPa": 180,
                "wind_speed_500hPa": 40,
                "wind_direction_500hPa": 180,
            }
            return {
                "time": times,
                **{field: [value] * hours for field, value in values.items()},
            }

        forecasts = [
            {
                "icon_d2": hourly_data(48, 500 + index),
            }
            for index in range(len(points))
        ]
        with (
            patch("ui.germany_grid_points", return_value=points),
            patch("weather_api.get_germany_grid_weather", return_value=forecasts) as fetch,
        ):
            outlook, _ = calculate_germany_outlook(())

        self.assertEqual(fetch.call_args.args[0], points)
        self.assertEqual(len(outlook), len(points) * 2)
        self.assertEqual(
            {(entry["latitude"], entry["longitude"]) for entry in outlook},
            set(points),
        )
        self.assertEqual({entry["day"] for entry in outlook}, set(range(2)))
        for entry in outlook:
            point_index = points.index((entry["latitude"], entry["longitude"]))
            expected_cape = 500 + point_index
            self.assertEqual(entry["environment"]["cape"], expected_cape)

    def test_rejects_missing_location_forecasts_instead_of_truncating(self) -> None:
        points = [(50.1, 7.1), (51.2, 8.2)]
        with (
            patch("ui.germany_grid_points", return_value=points),
            patch("weather_api.get_germany_grid_weather", return_value=[]),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "returned 0 location forecasts for 2 outlook grid points",
            ):
                calculate_germany_outlook(())


if __name__ == "__main__":
    unittest.main()
