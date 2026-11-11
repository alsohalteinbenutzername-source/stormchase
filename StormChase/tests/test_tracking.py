import unittest

from tracking import parse_konrad3d_xml, project_cell_motion


class ParseKonrad3dXmlTests(unittest.TestCase):
    def test_parses_cells_tracks_and_metadata(self) -> None:
        xml = b"""\
        <konrad3d>
          <head><metadata><reference_time>2026-10-29T12:00:00Z</reference_time></metadata></head>
          <cells>
            <cell id="K3D-17" latitude="51.2" longitude="7.4" max_reflectivity="58">
              <track>
                <point latitude="51.0" longitude="7.1" />
                <point latitude="51.1" longitude="7.2" />
              </track>
              <severity>severe</severity>
            </cell>
          </cells>
        </konrad3d>
        """

        update = parse_konrad3d_xml(xml)

        self.assertEqual(len(update.cells), 1)
        cell = update.cells[0]
        self.assertEqual(cell.identifier, "K3D-17")
        self.assertEqual((cell.latitude, cell.longitude), (51.2, 7.4))
        self.assertEqual(
            cell.track,
            ((51.0, 7.1), (51.1, 7.2), (51.2, 7.4)),
        )
        self.assertEqual(cell.reflectivity_dbz, 58.0)
        self.assertEqual(cell.severity, "severe")
        self.assertEqual(update.timestamp.isoformat(), "2026-10-29T12:00:00+00:00")

    def test_handles_an_empty_cells_report(self) -> None:
        update = parse_konrad3d_xml(
            b"<konrad3d><cells reference_time='2026-10-29T12:00:00Z'/></konrad3d>"
        )

        self.assertEqual(update.cells, ())
        self.assertIsNone(update.timestamp)

    def test_rejects_malformed_xml(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid KONRAD3D XML"):
            parse_konrad3d_xml(b"<konrad3d>")


class ProjectCellMotionTests(unittest.TestCase):
    def test_projects_downwind_north_when_wind_comes_from_south(self) -> None:
        path = project_cell_motion(50.0, 10.0, 180.0, 36.0)

        self.assertEqual(len(path), 7)
        self.assertEqual(path[0], (50.0, 10.0))
        self.assertGreater(path[-1][0], path[0][0])
        self.assertAlmostEqual(path[-1][0] - path[0][0], 0.323, delta=0.01)
        self.assertAlmostEqual(path[-1][1], path[0][1], delta=0.001)

    def test_projects_downwind_west_when_wind_comes_from_east(self) -> None:
        path = project_cell_motion(50.0, 10.0, 90.0, 36.0)

        self.assertLess(path[-1][1], path[0][1])
        self.assertAlmostEqual(path[-1][0], path[0][0], delta=0.002)

    def test_rejects_invalid_wind_speed_and_projection_steps(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            project_cell_motion(50.0, 10.0, 180.0, -1.0)
        with self.assertRaisesRegex(ValueError, "must be positive"):
            project_cell_motion(50.0, 10.0, 180.0, 36.0, step_minutes=0)


if __name__ == "__main__":
    unittest.main()
