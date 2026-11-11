from datetime import datetime, timedelta, timezone
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
            user=AuthUser("user-1", "storm@example.com"),
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

        self.assertEqual(request.call_args.kwargs["payload"], {
            "user_id": "user-1",
            "cell_id": "K3D-4",
            "latitude": 51.2,
            "longitude": 7.4,
            "report": "rotation observed",
        })

    def test_sign_in_persists_only_refresh_token_and_sets_current_user(self) -> None:
        auth_payload = {
            "access_token": "short-lived-access",
            "refresh_token": "rotating-refresh",
            "expires_in": 3600,
            "user": {"id": "user-1", "email": "storm@example.com"},
        }
        with (
            patch.object(self.client, "_request", return_value=auth_payload),
            patch("supabase_client.keyring.set_password") as save_session,
        ):
            user = self.client.sign_in("storm@example.com", "secret-password")

        self.assertEqual(user, AuthUser("user-1", "storm@example.com"))
        self.assertEqual(self.client.current_user, user)
        saved = json.loads(save_session.call_args.args[2])
        self.assertEqual(saved["refresh_token"], "rotating-refresh")
        self.assertNotIn("access_token", saved)

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
        with self.assertRaisesRegex(SupabaseError, "Sign in is required"):
            self.client.recent_reports()

        self.client._session = _AuthSession(
            access_token="access",
            refresh_token="refresh",
            user=AuthUser("user-1", "storm@example.com"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        def read_rows(
            table: str,
            _query: dict[str, str],
            *,
            authenticated: bool = False,
        ) -> list[dict[str, object]]:
            self.assertEqual(table, "storm_reports")
            self.assertEqual(_query["order"], "created_at.desc")
            self.assertTrue(authenticated)
            return []

        self.client._table_rows = read_rows

        self.assertEqual(self.client.recent_reports(), ())

    def test_expired_report_session_is_cleared(self) -> None:
        self.client._session = _AuthSession(
            access_token="expired-access",
            refresh_token="expired-refresh",
            user=AuthUser("user-1", "storm@example.com"),
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

        self.assertIn("with check (auth.uid() = user_id)", schema)
        self.assertIn("for select to authenticated", schema)
        self.assertIn("stormchase-radar", schema)


if __name__ == "__main__":
    unittest.main()
