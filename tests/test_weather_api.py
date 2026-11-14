import unittest
from unittest.mock import patch

from ui import germany_grid_points
from weather_api import STORMANALYZER_HOURLY_FIELDS, get_germany_grid_weather


class FakeWeatherResponse:
    from_cache = False

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class GermanyGridWeatherTests(unittest.TestCase):
    def test_batches_only_icon_d2_for_all_744_grid_points(self) -> None:
        points = germany_grid_points(())
        fields = STORMANALYZER_HOURLY_FIELDS.split(",")

        def fake_get_weather_response(url, params):
            self.assertEqual(url, "https://api.open-meteo.com/v1/dwd-icon")
            self.assertEqual(params["models"], "icon_d2")
            self.assertEqual(params["forecast_days"], 2)
            latitudes = [float(value) for value in params["latitude"].split(",")]
            longitudes = [float(value) for value in params["longitude"].split(",")]
            payload = []
            for latitude, longitude in zip(latitudes, longitudes):
                values = {
                    field: [
                        latitude * 1000 + longitude
                        if field == "cape"
                        else -50
                        if field == "convective_inhibition"
                        else 10
                    ]
                    for field in fields
                }
                payload.append(
                    {
                        "hourly": {
                            "time": ["2026-01-01T12:00"],
                            **values,
                        }
                    }
                )
            return FakeWeatherResponse(payload)

        with (
            patch("weather_api._weather_response_cached", return_value=True),
            patch(
                "weather_api._get_weather_response",
                side_effect=fake_get_weather_response,
            ) as get_response,
        ):
            forecasts = get_germany_grid_weather(points)

        self.assertEqual(get_response.call_count, 8)
        self.assertEqual(len(forecasts), len(points))
        for (latitude, longitude), forecast in zip(points, forecasts):
            expected_cape = latitude * 1000 + longitude
            self.assertEqual(forecast["icon_d2"]["cape"][0], expected_cape)
            self.assertEqual(len(forecast["icon_d2"]["time"]), 1)


if __name__ == "__main__":
    unittest.main()
