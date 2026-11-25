from datetime import datetime, timedelta, timezone
import unittest

from warning_system import (
    WARNING_LEVELS,
    beam_height_contour_range_km,
    radar_beam_height_km,
    validate_polygon,
    validate_warning_payload,
    warning_level,
)


class WarningLevelTests(unittest.TestCase):
    def test_nws_wind_and_hail_thresholds(self) -> None:
        self.assertEqual(warning_level("damaging_wind", 1).threshold, "≥58 mph / 93 km/h")
        self.assertEqual(warning_level("damaging_wind", 2).tag, "CONSIDERABLE")
        self.assertEqual(warning_level("damaging_wind", 3).tag, "DESTRUCTIVE")
        self.assertEqual(warning_level("hail", 1).threshold, "≥1.00 in / 25 mm")
        self.assertEqual(warning_level("hail", 2).threshold, "≥1.75 in / 44 mm")
        self.assertEqual(warning_level("hail", 3).threshold, "≥2.75 in / 70 mm")

    def test_tornado_escalation_requires_confirmation_for_catastrophic_tier(self) -> None:
        self.assertEqual(warning_level("tornado", 2).tag, "CONSIDERABLE / PDS WORDING")
        self.assertEqual(warning_level("tornado", 3).tag, "CATASTROPHIC / TORNADO EMERGENCY CRITERIA")
        with self.assertRaisesRegex(ValueError, "reliable-confirmation"):
            validate_warning_payload(
                "tornado", 3, [[8, 50], [9, 50], [9, 51]],
                "Catastrophic tornado threat", "Confirmed catastrophic threat",
                datetime.now(timezone.utc) + timedelta(hours=1),
            )

    def test_dwd_heavy_rain_levels_map_to_warnstufen_two_to_four(self) -> None:
        self.assertIn("15–25 mm/1 h", warning_level("heavy_rain", 1).threshold)
        self.assertEqual(warning_level("heavy_rain", 2).tag, "DWD LEVEL 3")
        self.assertIn(">40 mm/1 h", warning_level("heavy_rain", 3).threshold)

    def test_polygon_is_closed_and_rejects_bad_coordinates(self) -> None:
        polygon = validate_polygon([[8, 50], [9, 50], [9, 51], [8, 51]])
        self.assertEqual(polygon[0], polygon[-1])
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_polygon([[8, 50], [9, 50], [float("nan"), 51]])
        with self.assertRaisesRegex(ValueError, "cross"):
            validate_polygon([[0, 0], [2, 2], [0, 2], [2, 0]])

    def test_warning_payload_is_unofficial_and_bounded(self) -> None:
        payload = validate_warning_payload(
            "damaging_wind",
            2,
            [[8, 50], [9, 50], [9, 51], [8, 51]],
            "Considerable wind threat",
            "Evidence and expected impact summary",
            datetime.now(timezone.utc) + timedelta(hours=2),
        )
        self.assertFalse(payload["is_official"])
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["level_tag"], "CONSIDERABLE")


class RadarBeamGeometryTests(unittest.TestCase):
    def test_beam_height_increases_with_range(self) -> None:
        self.assertGreater(radar_beam_height_km(100, 0.5), radar_beam_height_km(20, 0.5))

    def test_beam_height_range_is_inverse_of_height_function(self) -> None:
        for height in (0.5, 1.0, 2.0, 3.0):
            distance = beam_height_contour_range_km(height, 0.5)
            self.assertAlmostEqual(
                radar_beam_height_km(distance, 0.5),
                height,
                places=6,
            )

    def test_rejects_negative_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            radar_beam_height_km(-1, 0.5)


if __name__ == "__main__":
    unittest.main()
