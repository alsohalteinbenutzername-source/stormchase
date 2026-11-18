from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from radar import RainCell, RotationDetection
from supabase_client import (
    AuthUser,
    SupabaseClient,
    SupabaseConfig,
    SupabaseError,
    _AuthSession,
)
from tracking import StormCell


class SupabaseConfigTests(unittest.TestCase):
    def test_requires_client_configuration_from_environment(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(SupabaseError, "STORMCHASE_SUPABASE_URL"):
                SupabaseConfig.from_environment()

    def test_reads_bundled_configuration(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("supabase_client.SUPABASE_URL", "https://bundled.supabase.co"),
            patch("supabase_client.SUPABASE_PUBLISHABLE_KEY", "sb_publishable_bundled"),
        ):
            config = SupabaseConfig.from_environment()

        self.assertEqual(config.url, "https://bundled.supabase.co")
        self.assertEqual(config.api_key, "sb_publishable_bundled")

    def test_environment_configuration_overrides_bundled_values(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "STORMCHASE_SUPABASE_URL": "https://environment.supabase.co",
                    "STORMCHASE_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_environment",
                },
                clear=True,
            ),
            patch("supabase_client.SUPABASE_URL", "https://bundled.supabase.co"),
            patch("supabase_client.SUPABASE_PUBLISHABLE_KEY", "sb_publishable_bundled"),
        ):
            config = SupabaseConfig.from_environment()

        self.assertEqual(config.url, "https://environment.supabase.co")
        self.assertEqual(config.api_key, "sb_publishable_environment")

    def test_rejects_non_tls_remote_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            SupabaseConfig("http://db.example.com", "anon-key")

    def test_allows_local_supabase_http(self) -> None:
        config = SupabaseConfig("http://localhost:54321", "anon-key")

        self.assertEqual(config.url, "http://localhost:54321")


class SupabaseDataContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = SupabaseClient(
            SupabaseConfig("https://example.supabase.co", "public-anon-key")
        )

    def test_rest_requests_use_public_anon_key_and_decode_json(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"ok":true}'
        response.headers.get.return_value = "application/json"
        with patch("supabase_client.urlopen", return_value=response) as urlopen_mock:
            result = self.client._request("GET", "/rest/v1/live_snapshots")

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.headers["Apikey"], "public-anon-key")
        self.assertEqual(request.headers["Authorization"], "Bearer public-anon-key")
        self.assertEqual(result, {"ok": True})

    def test_publishable_key_is_not_sent_as_a_bearer_token(self) -> None:
        client = SupabaseClient(
            SupabaseConfig("https://example.supabase.co", "sb_publishable_public-key")
        )
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"ok":true}'
        response.headers.get.return_value = "application/json"
        with patch("supabase_client.urlopen", return_value=response) as urlopen_mock:
            client._request("GET", "/rest/v1/live_snapshots")

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.headers["Apikey"], "sb_publishable_public-key")
        self.assertNotIn("Authorization", request.headers)

    def test_authenticated_requests_use_the_user_access_token(self) -> None:
        client = SupabaseClient(
            SupabaseConfig("https://example.supabase.co", "sb_publishable_public-key")
        )
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"ok":true}'
        response.headers.get.return_value = "application/json"
        with patch("supabase_client.urlopen", return_value=response) as urlopen_mock:
            client._request(
                "GET",
                "/rest/v1/community_reports",
                access_token="signed-in-user-jwt",
            )

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.headers["Apikey"], "sb_publishable_public-key")
        self.assertEqual(
            request.headers["Authorization"], "Bearer signed-in-user-jwt"
        )

    def test_parses_konrad3d_snapshot_into_existing_map_model(self) -> None:
        rows = [
            {
                "observed_at": "2026-11-11T10:00:00Z",
                "payload": {
                    "cells": [
                        {
                            "identifier": "K3D-4",
                            "latitude": 51.2,
                            "longitude": 7.4,
                            "reflectivity_dbz": 52.0,
                            "severity": "severe",
                            "track": [[51.1, 7.3], [51.2, 7.4]],
                            "forecast": [
                                {
                                    "timestamp": "2026-11-11T11:00:00Z",
                                    "latitude": 51.3,
                                    "longitude": 7.5,
                                }
                            ],
                        }
                    ]
                },
            }
        ]
        with patch.object(self.client, "_table_rows", return_value=rows):
            update = self.client.latest_storm_update()

        self.assertEqual(update.cells[0].identifier, "K3D-4")
        self.assertEqual(update.cells[0].track, ((51.1, 7.3), (51.2, 7.4)))
        self.assertEqual(
            update.cells[0].forecast,
            ((datetime(2026, 11, 11, 11, tzinfo=timezone.utc), (51.3, 7.5)),),
        )
        self.assertEqual(update.timestamp, datetime(2026, 11, 11, 10, tzinfo=timezone.utc))

    def test_parses_rain_cells_and_rotation_detections(self) -> None:
        def latest_snapshot(product: str, scope: str = "") -> dict[str, object]:
            if product == "rain_cells":
                return {
                    "observed_at": "2026-11-11T10:00:00Z",
                    "payload": {
                        "cells": [
                            {
                                "identifier": "rain-1",
                                "latitude": 51.0,
                                "longitude": 7.0,
                                "max_reflectivity_dbz": 27.0,
                                "area_pixels": 40,
                                "track": [],
                            }
                        ]
                    },
                }
            self.assertEqual(scope, "HNR")
            return {
                "payload": {
                    "detections": [
                        {
                            "latitude": 51.5,
                            "longitude": 7.5,
                            "negative_pixels": 8,
                            "positive_pixels": 9,
                        }
                    ]
                }
            }

        self.client.latest_snapshot = latest_snapshot

        rain_cells = self.client.latest_rain_cells()
        rotations = self.client.latest_rotation_detections("HNR")

        self.assertIsInstance(rain_cells[0], RainCell)
        self.assertEqual(rain_cells[0].track[0].timestamp, datetime(2026, 11, 11, 10, tzinfo=timezone.utc))
        self.assertEqual(rotations, (RotationDetection(51.5, 7.5, 8, 9),))

    def test_reads_daily_outlook_grid_for_the_requested_date(self) -> None:
        issued_at = "2026-11-11T10:00:00Z"
        point = {"icon_d2": {"time": ["2026-11-11T10:00"], "cin": [0]}}
        grid = [point] * 744
        with patch.object(
            self.client,
            "_table_rows",
            side_effect=[
                [{"forecast_date": "2026-11-11", "issued_at": issued_at}],
                [
                    {"point_index": index, "grid_payload": point}
                    for index in range(744)
                ],
            ],
        ) as table_rows:
            result = self.client.daily_outlook_grid(date(2026, 11, 11))

        self.assertEqual(result["grid"], grid)
        self.assertEqual(result["issued_at"], datetime(2026, 11, 11, 10, tzinfo=timezone.utc))
        self.assertEqual(
            table_rows.call_args_list[0].args,
            (
                "daily_outlook_forecasts",
                {
                    "select": "forecast_date,issued_at",
                    "forecast_date": "eq.2026-11-11",
                    "limit": "1",
                },
            ),
        )
        self.assertEqual(
            table_rows.call_args_list[1].args,
            (
                "daily_outlook_forecast_points",
                {
                    "select": "point_index,grid_payload",
                    "forecast_date": "eq.2026-11-11",
                    "order": "point_index.asc",
                    "limit": "744",
                },
            ),
        )

    def test_reads_legacy_daily_outlook_when_no_point_rows_exist(self) -> None:
        issued_at = "2026-11-11T10:00:00Z"
        grid = [{"icon_d2": {"time": ["2026-11-11T10:00"], "cin": [0]}}]
        with patch.object(
            self.client,
            "_table_rows",
            side_effect=[
                [{"forecast_date": "2026-11-11", "issued_at": issued_at}],
                [],
                [{"grid_payload": grid}],
            ],
        ):
            result = self.client.daily_outlook_grid(date(2026, 11, 11))

        self.assertEqual(result["grid"], grid)

    def test_returns_none_when_daily_outlook_is_not_published(self) -> None:
        with patch.object(self.client, "_table_rows", return_value=[]):
            self.assertIsNone(self.client.daily_outlook_grid(date(2026, 11, 11)))

    def test_rejects_incomplete_daily_outlook_points(self) -> None:
        with patch.object(
            self.client,
            "_table_rows",
            side_effect=[
                [
                    {
                        "forecast_date": "2026-11-11",
                        "issued_at": "2026-11-11T10:00:00Z",
                    }
                ],
                [{"point_index": 0, "grid_payload": {"icon_d2": {}}}],
            ],
        ):
            with self.assertRaisesRegex(SupabaseError, "1 of 744"):
                self.client.daily_outlook_grid(date(2026, 11, 11))

    def test_rejects_malformed_legacy_daily_outlook_grid(self) -> None:
        with patch.object(
            self.client,
            "_table_rows",
            side_effect=[
                [
                    {
                        "forecast_date": "2026-11-11",
                        "issued_at": "2026-11-11T10:00:00Z",
                    }
                ],
                [],
                [{"grid_payload": [{"ecmwf": {}}]}],
            ],
        ):
            with self.assertRaisesRegex(SupabaseError, "ICON-D2"):
                self.client.daily_outlook_grid(date(2026, 11, 11))

    def test_rejects_empty_legacy_daily_outlook_grid(self) -> None:
        with patch.object(
            self.client,
            "_table_rows",
            side_effect=[
                [
                    {
                        "forecast_date": "2026-11-11",
                        "issued_at": "2026-11-11T10:00:00Z",
                    }
                ],
                [],
                [{"grid_payload": []}],
            ],
        ):
            with self.assertRaisesRegex(SupabaseError, "ICON-D2"):
                self.client.daily_outlook_grid(date(2026, 11, 11))

    def test_returns_latest_timestamped_radar_file_from_storage(self) -> None:
        rows = [
            {
                "observed_at": "2026-11-11T10:00:00Z",
                "storage_path": "reflectivity/2026-11-11T100000.h5",
            }
        ]
        with (
            patch.object(self.client, "_table_rows", return_value=rows),
            patch.object(self.client, "_request", return_value=b"hdf5-data") as request,
            patch("supabase_client.decode_radar_file", return_value="decoded") as decode,
        ):
            frame = self.client.latest_radar_frame("reflectivity")

        self.assertEqual(frame, "decoded")
        self.assertIn("/storage/v1/object/public/stormchase-radar/", request.call_args.args[1])
        decode.assert_called_once_with(
            b"hdf5-data",
            timestamp=datetime(2026, 11, 11, 10, tzinfo=timezone.utc),
        )

    def test_rejects_unsafe_storage_path(self) -> None:
        rows = [
            {"observed_at": "2026-11-11T10:00:00Z", "storage_path": "../secret.h5"}
        ]
        with patch.object(self.client, "_table_rows", return_value=rows):
            with self.assertRaisesRegex(SupabaseError, "unsafe Storage path"):
                self.client.latest_radar_frame("reflectivity")

    def test_submits_report_with_authenticated_user_and_cell_coordinates(self) -> None:
        self.client._session = _AuthSession(
            access_token="access",
            refresh_token="refresh",
            user=AuthUser("user-1", "stormchaser_1"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        cell = StormCell(
            identifier="K3D-4",
            latitude=51.2,
            longitude=7.4,
            track=((51.2, 7.4),),
            reflectivity_dbz=52.0,
            severity=None,
        )
        with patch.object(self.client, "_request") as request:
            self.client.submit_report(cell, "  rotation observed  ")

        self.assertEqual(
            request.call_args.kwargs["payload"],
            {
                "user_id": "user-1",
                "reporter_username": "stormchaser_1",
                "cell_id": "K3D-4",
                "latitude": 51.2,
                "longitude": 7.4,
                "report": "rotation observed",
            },
        )

    def test_anonymous_session_starts_without_email_or_password(self) -> None:
        auth_payload = {
            "access_token": "short-lived-access",
            "refresh_token": "rotating-refresh",
            "expires_in": 3600,
            "user": {"id": "user-1", "is_anonymous": True},
        }
        with (
            patch.object(self.client, "_request", side_effect=[auth_payload, []]) as request,
            patch("supabase_client.keyring.set_password") as save_session,
        ):
            user = self.client.ensure_anonymous_session()

        self.assertEqual(user, AuthUser("user-1", None))
        self.assertEqual(self.client.current_user, user)
        self.assertEqual(request.call_args_list[0].args[1], "/auth/v1/signup")
        self.assertEqual(request.call_args_list[0].kwargs["payload"], {"data": {}})
        saved = json.loads(save_session.call_args.args[2])
        self.assertEqual(saved["refresh_token"], "rotating-refresh")
        self.assertNotIn("access_token", saved)

    def test_set_reporter_username_creates_a_unique_profile(self) -> None:
        auth_payload = {
            "access_token": "short-lived-access",
            "refresh_token": "rotating-refresh",
            "expires_in": 3600,
            "user": {"id": "user-1", "is_anonymous": True},
        }
        with (
            patch.object(
                self.client,
                "_request",
                side_effect=[auth_payload, [], None],
            ) as request,
            patch("supabase_client.keyring.set_password") as save_session,
        ):
            user = self.client.set_reporter_username("StormChaser_1")

        self.assertEqual(user, AuthUser("user-1", "stormchaser_1"))
        self.assertEqual(
            request.call_args_list[2].kwargs["payload"],
            {"user_id": "user-1", "username": "stormchaser_1"},
        )
        self.assertEqual(
            request.call_args_list[2].kwargs["access_token"],
            "short-lived-access",
        )
        saved = json.loads(save_session.call_args.args[2])
        self.assertEqual(saved["username"], "stormchaser_1")

    def test_anonymous_sign_in_explains_when_provider_is_disabled(self) -> None:
        with patch.object(
            self.client,
            "_request",
            side_effect=SupabaseError(
                "Supabase request failed (403): Anonymous sign-ins are disabled",
                status_code=403,
            ),
        ):
            with self.assertRaisesRegex(
                SupabaseError,
                "Anonymous Sign-Ins to be enabled",
            ):
                self.client.ensure_anonymous_session()

    def test_restore_session_loads_the_saved_reporter_username(self) -> None:
        auth_payload = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "user": {"id": "user-1", "is_anonymous": True},
        }
        with (
            patch(
                "supabase_client.keyring.get_password",
                return_value='{"refresh_token":"old-refresh"}',
            ),
            patch.object(
                self.client,
                "_request",
                side_effect=[auth_payload, [{"username": "stormchaser_1"}]],
            ),
            patch("supabase_client.keyring.set_password"),
        ):
            user = self.client.restore_session()

        self.assertEqual(user, AuthUser("user-1", "stormchaser_1"))
        self.assertEqual(self.client.current_user, user)

    def test_rejects_invalid_username_before_auth_request(self) -> None:
        with patch.object(self.client, "_request") as request:
            with self.assertRaisesRegex(ValueError, "3–24 characters"):
                self.client.set_reporter_username("not a username")
        request.assert_not_called()

    def test_reporter_username_cannot_be_changed_after_registration(self) -> None:
        self.client._session = _AuthSession(
            access_token="access",
            refresh_token="refresh",
            user=AuthUser("user-1", "stormchaser_1"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        with (
            patch.object(
                self.client,
                "_table_rows",
                return_value=[{"username": "stormchaser_1"}],
            ),
            patch("supabase_client.keyring.set_password"),
        ):
            with self.assertRaisesRegex(ValueError, "can only be set once"):
                self.client.set_reporter_username("another_name")

    def test_rejects_a_username_claimed_by_another_identity(self) -> None:
        self.client._session = _AuthSession(
            access_token="access",
            refresh_token="refresh",
            user=AuthUser("user-1", None),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        with (
            patch.object(self.client, "_table_rows", side_effect=[[], []]),
            patch.object(
                self.client,
                "_request",
                side_effect=SupabaseError(
                    "duplicate key value violates unique constraint",
                    status_code=409,
                ),
            ),
            patch("supabase_client.keyring.set_password"),
        ):
            with self.assertRaisesRegex(SupabaseError, "already in use"):
                self.client.set_reporter_username("stormchaser_1")

    def test_restore_keeps_saved_token_when_supabase_is_temporarily_unavailable(self) -> None:
        with (
            patch(
                "supabase_client.keyring.get_password",
                return_value='{"refresh_token":"refresh"}',
            ),
            patch.object(
                self.client,
                "_request",
                side_effect=SupabaseError("network unavailable"),
            ),
            patch("supabase_client.keyring.delete_password") as delete_session,
        ):
            with self.assertRaisesRegex(SupabaseError, "network unavailable"):
                self.client.restore_session()

        delete_session.assert_not_called()

    def test_community_reports_require_authentication(self) -> None:
        with self.assertRaisesRegex(SupabaseError, "reporter username"):
            self.client.recent_reports()

        self.client._session = _AuthSession(
            access_token="access",
            refresh_token="refresh",
            user=AuthUser("user-1", "stormchaser_1"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        def read_rows(
            table: str,
            _query: dict[str, str],
            *,
            authenticated: bool = False,
        ) -> list[dict[str, object]]:
            self.assertEqual(table, "storm_reports")
            self.assertEqual(
                _query["select"],
                "id,reporter_username,cell_id,latitude,longitude,report,created_at",
            )
            self.assertEqual(_query["order"], "created_at.desc")
            self.assertTrue(authenticated)
            return []

        self.client._table_rows = read_rows

        self.assertEqual(self.client.recent_reports(), ())

    def test_expired_report_session_is_cleared(self) -> None:
        self.client._session = _AuthSession(
            access_token="expired-access",
            refresh_token="expired-refresh",
            user=AuthUser("user-1", "stormchaser_1"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        cell = StormCell(
            identifier="K3D-4",
            latitude=51.2,
            longitude=7.4,
            track=((51.2, 7.4),),
            reflectivity_dbz=None,
            severity=None,
        )
        with (
            patch.object(
                self.client,
                "_request",
                side_effect=SupabaseError("expired", status_code=401),
            ),
            patch("supabase_client.keyring.delete_password"),
        ):
            with self.assertRaisesRegex(SupabaseError, "expired"):
                self.client.submit_report(cell, "rotation observed")

        self.assertIsNone(self.client.current_user)

    def test_expired_saved_session_is_removed_without_authenticating(self) -> None:
        with (
            patch(
                "supabase_client.keyring.get_password",
                return_value='{"refresh_token":"expired"}',
            ),
            patch.object(
                self.client,
                "_request",
                side_effect=SupabaseError("expired", status_code=401),
            ),
            patch("supabase_client.keyring.delete_password") as delete_session,
        ):
            user = self.client.restore_session()

        self.assertIsNone(user)
        self.assertIsNone(self.client.current_user)
        delete_session.assert_called_once()

    def test_rejects_empty_or_oversized_reports_before_network_call(self) -> None:
        cell = StormCell(
            identifier="K3D-4",
            latitude=51.2,
            longitude=7.4,
            track=((51.2, 7.4),),
            reflectivity_dbz=None,
            severity=None,
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 2000"):
            self.client.submit_report(cell, "  ")
        with self.assertRaisesRegex(ValueError, "between 1 and 2000"):
            self.client.submit_report(cell, "x" * 2001)

    def test_schema_defines_authenticated_report_policies(self) -> None:
        schema = (
            Path(__file__).parents[1] / "supabase" / "001_stormchase_schema.sql"
        ).read_text(encoding="utf-8")
        anonymous_reporters = (
            Path(__file__).parents[1] / "supabase" / "004_anonymous_reporters.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("with check (auth.uid() = user_id)", schema)
        self.assertIn("for select to authenticated", schema)
        self.assertIn("stormchase-radar", schema)
        self.assertIn("auth.jwt() ->> 'is_anonymous'", anonymous_reporters)
        self.assertIn("profile.username = reporter_username", anonymous_reporters)
        self.assertIn("Report limit reached", anonymous_reporters)


if __name__ == "__main__":
    unittest.main()
