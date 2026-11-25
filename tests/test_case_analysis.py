from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from case_analysis import CaseAnalysisStore
from tracking import StormCell


class CaseAnalysisStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.store = CaseAnalysisStore(Path(self.temp_dir.name) / "cases.sqlite3")
        self.start = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def cell(
        identifier: str,
        latitude: float,
        longitude: float,
        observed_reflectivity: float,
        forecast=(),
    ) -> StormCell:
        return StormCell(
            identifier=identifier,
            latitude=latitude,
            longitude=longitude,
            track=(),
            reflectivity_dbz=observed_reflectivity,
            severity="severe" if observed_reflectivity >= 50 else None,
            forecast=forecast,
        )

    def test_tracks_cell_evolution_and_verifies_forecast(self) -> None:
        valid_time = self.start + timedelta(minutes=15)
        first = self.cell(
            "K3D-1",
            50.0,
            8.0,
            45.0,
            ((valid_time, (50.1, 8.0)),),
        )
        self.store.record_cells((first,), self.start)
        second = self.cell("K3D-1", 50.1, 8.0, 55.0)
        self.store.record_cells((second,), valid_time)

        history = self.store.history("K3D-1")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["reflectivity_dbz"], 45.0)
        self.assertEqual(history[1]["reflectivity_dbz"], 55.0)

        skill = self.store.forecast_skill()
        self.assertEqual(len(skill), 1)
        self.assertEqual(skill[0]["model"], "KONRAD3D")
        self.assertEqual(skill[0]["lead_minutes"], 15)
        self.assertEqual(skill[0]["samples"], 1)
        self.assertAlmostEqual(skill[0]["mean_position_error_km"], 0.0, places=5)

        timeline = self.store.timeline()
        self.assertTrue(any(item["event_type"] == "forecast_verification" for item in timeline))

    def test_starts_new_cell_case_after_a_long_gap_and_compares_history(self) -> None:
        self.store.record_cells(
            (self.cell("OLD-1", 50.0, 8.0, 48.0),),
            self.start,
        )
        self.store.record_cells(
            (self.cell("OLD-1", 50.05, 8.05, 56.0),),
            self.start + timedelta(minutes=10),
        )
        later = self.start + timedelta(minutes=90)
        self.store.record_cells(
            (self.cell("NEW-1", 50.0, 8.0, 50.0),),
            later,
        )
        self.store.record_cells(
            (self.cell("NEW-1", 50.05, 8.05, 58.0),),
            later + timedelta(minutes=10),
        )

        stats = self.store.case_statistics("NEW-1")
        self.assertIsNotNone(stats)
        self.assertEqual(stats["observation_count"], 2)
        comparisons = self.store.compare_cases("NEW-1")
        self.assertEqual(len(comparisons), 1)
        self.assertEqual(comparisons[0]["cell_id"], "OLD-1")

    def test_event_timeline_is_local_and_deduplicates(self) -> None:
        self.store.record_event("test", "event-1", self.start, "Recorded locally")
        self.store.record_event("test", "event-1", self.start, "Recorded locally again")
        events = self.store.timeline()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "Recorded locally")


if __name__ == "__main__":
    unittest.main()
