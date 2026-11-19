import unittest
from unittest.mock import patch

from publisher.publisher import (
    OUTLOOK_FETCH_WINDOW_SECONDS,
    OUTLOOK_GRID_VERSION,
    OUTLOOK_HOURLY_FIELDS,
    PublisherError,
    _request,
    _fetch_daily_outlook_grid,
    _outlook_grid_points,
    main,
    publish_daily_outlook,
)


class EmptyResponse:
    def __enter__(self):
        return self

    def __exit__(self, _exception_type, _exception, _traceback):
        return None

    def read(self):
        return b""


class DailyOutlookPublisherTests(unittest.TestCase):
    def test_outlook_request_uses_required_raw_hourly_fields(self) -> None:
        self.assertLess(len(OUTLOOK_HOURLY_FIELDS), 50)
        self.assertIn("temperature_925hPa", OUTLOOK_HOURLY_FIELDS)
        self.assertIn("geopotential_height_500hPa", OUTLOOK_HOURLY_FIELDS)
        self.assertNotIn("temperature_975hPa", OUTLOOK_HOURLY_FIELDS)

    def test_fetches_all_outlook_batches_within_two_minutes(self) -> None:
        points = _outlook_grid_points()
        elapsed = [0.0]

        def fake_fetch(request_params):
            latitudes = request_params["latitude"].split(",")
            longitudes = request_params["longitude"].split(",")
            hourly_values = {
                field: [
                    -25
                    if field == "cape"
                    else -50
                    if field == "convective_inhibition"
                    else 10
                ]
                for field in OUTLOOK_HOURLY_FIELDS
            }
            return [
                {
                    "elevation": 100,
                    "hourly": {
                        "time": ["2026-11-18T12:00"],
                        **hourly_values,
                    },
                }
                for _latitude, _longitude in zip(latitudes, longitudes)
            ]

        def sleep(seconds):
            elapsed[0] += seconds

        with (
            patch(
                "publisher.publisher._fetch_openmeteo_batch",
                side_effect=fake_fetch,
            ) as fetch,
            patch("publisher.publisher.time.monotonic", side_effect=lambda: elapsed[0]),
            patch("publisher.publisher.time.sleep", side_effect=sleep) as sleep_mock,
        ):
            grid = _fetch_daily_outlook_grid()

        self.assertEqual(fetch.call_count, 8)
        self.assertEqual(len(grid), len(points))
        self.assertEqual(len(sleep_mock.call_args_list), fetch.call_count - 1)
        self.assertAlmostEqual(elapsed[0], 105.0)
        self.assertLessEqual(elapsed[0], OUTLOOK_FETCH_WINDOW_SECONDS)
        self.assertNotIn("temperature_975hPa", grid[0]["icon_d2"])
        self.assertIn("temperature_850hPa", grid[0]["icon_d2"])
        self.assertEqual(grid[0]["icon_d2"]["cape"], [-25])
        self.assertEqual(grid[0]["icon_d2"]["cin"], [-50])
        self.assertNotIn("srh_0_1km", grid[0]["icon_d2"])
        self.assertNotIn("cape_0_3km", grid[0]["icon_d2"])
        self.assertNotIn("stp", grid[0]["icon_d2"])
        self.assertNotIn("scp", grid[0]["icon_d2"])

    def test_request_retries_a_timeout(self) -> None:
        with (
            patch("publisher.publisher.SUPABASE_URL", "https://example.supabase.co"),
            patch("publisher.publisher.SERVICE_KEY", "test-key"),
            patch(
                "publisher.publisher.urlopen",
                side_effect=[TimeoutError("read timed out"), EmptyResponse()],
            ) as urlopen,
            patch("publisher.publisher.time.sleep"),
        ):
            result = _request(
                "POST",
                "/rest/v1/daily_outlook_forecasts",
                payload=[],
            )

        self.assertIsNone(result)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(
            [call.kwargs["timeout"] for call in urlopen.call_args_list],
            [45, 45],
        )

    def test_publishes_outlook_points_in_bounded_batches(self) -> None:
        grid = [{"icon_d2": {"point": index}} for index in range(51)]
        with (
            patch("publisher.publisher._request", return_value=[]) as request,
            patch(
                "publisher.publisher._fetch_daily_outlook_grid",
                return_value=grid,
            ),
            patch("publisher.publisher._upsert") as upsert,
        ):
            publish_daily_outlook()

        self.assertEqual(request.call_count, 2)
        point_query = request.call_args_list[0].kwargs["query"]
        version_query = request.call_args_list[1].kwargs["query"]
        self.assertEqual(
            point_query["limit"],
            "744",
        )
        self.assertEqual(
            version_query["grid_payload->>grid_version"],
            f"eq.{OUTLOOK_GRID_VERSION}",
        )
        self.assertEqual(
            version_query["limit"],
            "744",
        )
        self.assertEqual(
            request.call_args_list[1].args[1],
            "/rest/v1/daily_outlook_forecast_points",
        )
        self.assertEqual(
            upsert.call_args_list[0].args[0],
            "daily_outlook_forecasts",
        )
        self.assertEqual(upsert.call_args_list[0].args[2][0]["grid_payload"], [])

        point_batches = upsert.call_args_list[1:]
        self.assertEqual(
            [len(call.args[2]) for call in point_batches],
            [25, 25, 1],
        )
        written_rows = [
            row
            for call in point_batches
            for row in call.args[2]
        ]
        self.assertEqual(
            [row["point_index"] for row in written_rows],
            list(range(len(grid))),
        )
        self.assertEqual(
            [row["grid_payload"] for row in written_rows],
            [
                {**point, "grid_version": OUTLOOK_GRID_VERSION}
                for point in grid
            ],
        )

    def test_skips_fetch_when_all_outlook_points_are_published(self) -> None:
        with (
            patch(
                "publisher.publisher._request",
                side_effect=[
                    [{"point_index": index} for index in range(744)],
                    [{"point_index": index} for index in range(744)],
                ],
            ) as request,
            patch("publisher.publisher._fetch_daily_outlook_grid") as fetch,
            patch("publisher.publisher._upsert") as upsert,
        ):
            publish_daily_outlook()

        self.assertEqual(request.call_count, 2)
        fetch.assert_not_called()
        upsert.assert_not_called()

    def test_refetches_complete_grid_with_partial_version_markers(self) -> None:
        grid = [{"icon_d2": {"point": 0}}]
        with (
            patch(
                "publisher.publisher._request",
                side_effect=[
                    [{"point_index": index} for index in range(744)],
                    [{"point_index": index} for index in range(25)],
                ],
            ),
            patch(
                "publisher.publisher._fetch_daily_outlook_grid",
                return_value=grid,
            ) as fetch,
            patch("publisher.publisher._upsert") as upsert,
        ):
            publish_daily_outlook()

        fetch.assert_called_once()
        self.assertGreaterEqual(upsert.call_count, 2)

    def test_request_reports_exhausted_timeout_retries(self) -> None:
        with (
            patch("publisher.publisher.SUPABASE_URL", "https://example.supabase.co"),
            patch("publisher.publisher.SERVICE_KEY", "test-key"),
            patch(
                "publisher.publisher.urlopen",
                side_effect=TimeoutError("read timed out"),
            ) as urlopen,
            patch("publisher.publisher.time.sleep"),
        ):
            with self.assertRaisesRegex(PublisherError, "timed out or failed"):
                _request(
                    "POST",
                    "/rest/v1/daily_outlook_forecasts",
                    payload=[],
                )

        self.assertEqual(urlopen.call_count, 4)


class PublisherTaskSelectionTests(unittest.TestCase):
    def test_live_publisher_does_not_fetch_daily_outlook(self) -> None:
        with (
            patch("publisher.publisher._configuration", return_value=("url", "key")),
            patch("publisher.publisher.purge_expired_radar_objects") as purge,
            patch("publisher.publisher.publish_reflectivity_and_rain") as reflectivity,
            patch("publisher.publisher.publish_konrad3d") as konrad,
            patch("publisher.publisher.publish_velocity_and_rotations") as velocity,
            patch("publisher.publisher.publish_lightning") as lightning,
            patch("publisher.publisher.publish_daily_outlook") as outlook,
        ):
            main([])

        for task in (purge, reflectivity, konrad, velocity, lightning):
            task.assert_called_once()
        outlook.assert_not_called()

    def test_daily_outlook_mode_runs_without_live_publisher_tasks(self) -> None:
        with (
            patch("publisher.publisher._configuration", return_value=("url", "key")),
            patch("publisher.publisher.purge_expired_radar_objects") as purge,
            patch("publisher.publisher.publish_reflectivity_and_rain") as reflectivity,
            patch("publisher.publisher.publish_konrad3d") as konrad,
            patch("publisher.publisher.publish_velocity_and_rotations") as velocity,
            patch("publisher.publisher.publish_lightning") as lightning,
            patch("publisher.publisher.publish_daily_outlook") as outlook,
        ):
            main(["--daily-outlook-only"])

        outlook.assert_called_once()
        for task in (purge, reflectivity, konrad, velocity, lightning):
            task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
