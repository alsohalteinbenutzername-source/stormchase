import unittest
from unittest.mock import patch

from ui import germany_grid_points
from weather_api import (
    STORMANALYZER_HOURLY_FIELDS,
    _fetch_hourly_in_batches,
    get_germany_grid_weather,
)


class FakeWeatherResponse:
    from_cache = False

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class GermanyGridWeatherTests(unittest.TestCase):
    def test_batched_hourly_data_preserves_surface_elevation(self) -> None:
        variables = ["cape", "temperature_2m", "wind_speed_10m"]

        def fake_request(_url, params):
            fields = params["hourly"].split(",")
            return {
                "time": ["2026-01-01T12:00"],
                "surface_elevation_m": 123.0,
                **{field: [10] for field in fields},
            }

        with (
            patch("weather_api._request_hourly", side_effect=fake_request),
            patch("weather_api.time.sleep"),
        ):
            hourly = _fetch_hourly_in_batches(
                "https://api.open-meteo.com/v1/forecast",
                {"hourly": ",".join(variables)},
                variables,
            )

        self.assertEqual(hourly["surface_elevation_m"], 123.0)
        self.assertEqual(hourly["time"], ["2026-01-01T12:00"])
        self.assertEqual(hourly["cape"], [10])
        self.assertEqual(hourly["wind_speed_10m"], [10])

    def test_batches_only_icon_d2_for_all_744_grid_points(self) -> None:
        points = germany_grid_points(())
        fields = STORMANALYZER_HOURLY_FIELDS.split(",")

        def fake_get_weather_response(url, params, **kwargs):
            self.assertEqual(url, "https://api.open-meteo.com/v1/dwd-icon")
            self.assertEqual(params["models"], "icon_d2")
            self.assertEqual(params["forecast_days"], 2)
            self.assertGreater(kwargs["cache_expire_after"], 0)
            self.assertLessEqual(kwargs["cache_expire_after"], 86400)
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
                        "elevation": 100,
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
            self.assertEqual(forecast["icon_d2"]["surface_elevation_m"], 100)
            self.assertEqual(len(forecast["icon_d2"]["time"]), 1)


if __name__ == "__main__":
    unittest.main()
