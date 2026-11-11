from datetime import datetime, timedelta, timezone
import unittest

import numpy as np

from radar import (
    GERMANY_EXTENT,
    RainCell,
    RainCellTracker,
    RadarFrame,
    detect_rain_cells,
)


class DetectRainCellsTests(unittest.TestCase):
    def test_detects_connected_components_only_inside_dbz_band(self) -> None:
        reflectivity = np.zeros((20, 20), dtype=np.float32)
        reflectivity[10:13, 10:13] = 25.0
        reflectivity[2:4, 3:5] = 25.0
        reflectivity[5:8, 5:8] = 31.0
        reflectivity[15:17, 15:17] = 19.0
        reflectivity[2:4, 17:19] = 25.0
        reflectivity[4:6, 17:19] = 35.0
        reflectivity[0, 0] = np.nan

        cells = detect_rain_cells(
            RadarFrame(reflectivity, None, GERMANY_EXTENT),
            minimum_pixels=6,
        )

        self.assertEqual(len(cells), 1)
        cell = cells[0]
        self.assertEqual(cell.area_pixels, 9)
        self.assertEqual(cell.max_reflectivity_dbz, 25.0)
        self.assertAlmostEqual(cell.latitude, 50.825)
        self.assertAlmostEqual(cell.longitude, 11.325)

    def test_isolated_pixels_are_not_reported_as_rain_cells(self) -> None:
        reflectivity = np.zeros((80, 80), dtype=np.float32)
        reflectivity[::8, ::8] = 25.0

        cells = detect_rain_cells(RadarFrame(reflectivity, None))

        self.assertEqual(cells, ())

    def test_nearby_echoes_merge_into_one_cell_and_count_echo_pixels(self) -> None:
        reflectivity = np.zeros((80, 80), dtype=np.float32)
        reflectivity[20:25, 20:25] = 24.0
        reflectivity[20:25, 28:33] = 27.0

        cells = detect_rain_cells(RadarFrame(reflectivity, None))

        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0].area_pixels, 50)
        self.assertEqual(cells[0].max_reflectivity_dbz, 27.0)

    def test_rejects_invalid_detection_limits(self) -> None:
        frame = RadarFrame(np.zeros((2, 2), dtype=np.float32), None)

        with self.assertRaisesRegex(ValueError, "must not exceed"):
            detect_rain_cells(frame, minimum_dbz=31, maximum_dbz=20)
        with self.assertRaisesRegex(ValueError, "at least one pixel"):
            detect_rain_cells(frame, minimum_pixels=0)


class RainCellTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = RainCellTracker()
        self.start = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

    @staticmethod
    def cell(latitude: float, longitude: float) -> RainCell:
        return RainCell(
            identifier="detection",
            latitude=latitude,
            longitude=longitude,
            max_reflectivity_dbz=25.0,
            area_pixels=8,
        )

    def test_associates_moving_cells_and_keeps_a_timestamped_track(self) -> None:
        first = self.tracker.update((self.cell(51.0, 7.0),), self.start)[0]
        second = self.tracker.update(
            (self.cell(51.05, 7.1),),
            self.start + timedelta(minutes=5),
        )[0]

        self.assertEqual(first.identifier, second.identifier)
        self.assertEqual(len(second.track), 2)
        self.assertEqual(second.track[-1].timestamp, self.start + timedelta(minutes=5))

    def test_does_not_duplicate_a_track_point_for_the_same_scan(self) -> None:
        self.tracker.update((self.cell(51.0, 7.0),), self.start)

        repeated = self.tracker.update((self.cell(51.0, 7.0),), self.start)[0]

        self.assertEqual(len(repeated.track), 1)

    def test_starts_a_new_track_for_a_distant_cell(self) -> None:
        first = self.tracker.update((self.cell(51.0, 7.0),), self.start)[0]
        other = self.tracker.update(
            (self.cell(54.0, 13.0),),
            self.start + timedelta(minutes=5),
        )[0]

        self.assertNotEqual(first.identifier, other.identifier)


if __name__ == "__main__":
    unittest.main()
