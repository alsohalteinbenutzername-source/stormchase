import unittest
from unittest.mock import patch

from publisher.publisher import (
    OUTLOOK_FETCH_WINDOW_SECONDS,
    OUTLOOK_HOURLY_FIELDS,
    OUTLOOK_PROFILE_FIELDS,
    PublisherError,
    _request,
    _fetch_daily_outlook_grid,
    _outlook_grid_points,
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
    def test_fetches_all_outlook_batches_within_two_minutes(self) -> None:
        points = _outlook_grid_points()
        elapsed = [0.0]

        def fake_fetch(request_params):
            latitudes = request_params["latitude"].split(",")
            longitudes = request_params["longitude"].split(",")
            hourly_values = {
                field: [
                    100
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
            patch(
                "publisher.publisher._derive_outlook_profile_metrics",
                return_value={field: None for field in OUTLOOK_PROFILE_FIELDS},
            ),
            patch("publisher.publisher.time.monotonic", side_effect=lambda: elapsed[0]),
            patch("publisher.publisher.time.sleep", side_effect=sleep) as sleep_mock,
        ):
            grid = _fetch_daily_outlook_grid()

        self.assertEqual(fetch.call_count, 8)
        self.assertEqual(len(grid), len(points))
        self.assertEqual(len(sleep_mock.call_args_list), fetch.call_count - 1)
        self.assertAlmostEqual(elapsed[0], 105.0)
        self.assertLessEqual(elapsed[0], OUTLOOK_FETCH_WINDOW_SECONDS)

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

        request.assert_called_once()
        self.assertEqual(
            request.call_args.args[1],
            "/rest/v1/daily_outlook_forecast_points",
        )
        self.assertEqual(
            request.call_args.kwargs["query"]["limit"],
            "744",
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
            grid,
        )

    def test_skips_fetch_when_all_outlook_points_are_published(self) -> None:
        with (
            patch(
                "publisher.publisher._request",
                return_value=[{"point_index": index} for index in range(744)],
            ),
            patch("publisher.publisher._fetch_daily_outlook_grid") as fetch,
            patch("publisher.publisher._upsert") as upsert,
        ):
            publish_daily_outlook()

        fetch.assert_not_called()
        upsert.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
