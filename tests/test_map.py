import unittest

from radar import GERMANY_EXTENT
from ui import esri_bbox, fit_extent_to_aspect, load_germany_outline, zoom_axis_limits


class GermanyOutlineTests(unittest.TestCase):
    def test_loads_natural_earth_outline_with_expected_coverage(self) -> None:
        outlines = load_germany_outline()

        self.assertGreater(len(outlines), 1)
        longitudes = [longitude for outline in outlines for longitude, _ in outline]
        latitudes = [latitude for outline in outlines for _, latitude in outline]
        self.assertGreaterEqual(min(longitudes), 5.0)
        self.assertLessEqual(max(longitudes), 16.0)
        self.assertGreaterEqual(min(latitudes), 47.0)
        self.assertLessEqual(max(latitudes), 56.0)
        self.assertTrue(all(outline[0] == outline[-1] for outline in outlines))


class MapZoomTests(unittest.TestCase):
    def test_zoom_in_keeps_the_cursor_anchor_fixed(self) -> None:
        limits = zoom_axis_limits((5.0, 16.0), (5.0, 16.0), 8.0, 0.5)

        self.assertAlmostEqual(limits[1] - limits[0], 5.5)
        self.assertAlmostEqual(limits[0] + 3 / 11 * 5.5, 8.0)

    def test_zoom_out_cannot_exceed_the_initial_extent(self) -> None:
        limits = zoom_axis_limits((6.0, 10.0), (5.0, 16.0), 8.0, 10.0)

        self.assertEqual(limits, (5.0, 16.0))

    def test_zoom_in_stops_at_maximum_zoom(self) -> None:
        limits = zoom_axis_limits((5.0, 16.0), (5.0, 16.0), 10.5, 0.01)

        self.assertAlmostEqual(limits[1] - limits[0], 11.0 / 20)
        self.assertAlmostEqual((limits[0] + limits[1]) / 2, 10.5)


class ImageryExtentTests(unittest.TestCase):
    def test_formats_germany_bounds_in_esri_bbox_order(self) -> None:
        image_extent = fit_extent_to_aspect((5.0, 16.0, 47.0, 56.0), 640 / 480)

        self.assertEqual(
            esri_bbox(image_extent),
            (4.5, 47.0, 16.5, 56.0),
        )

    def test_expands_extent_to_match_image_without_moving_its_center(self) -> None:
        extent = fit_extent_to_aspect((5.0, 16.0, 47.0, 56.0), 2.0)

        self.assertAlmostEqual(extent[0] + extent[1], 21.0)
        self.assertAlmostEqual(extent[2] + extent[3], 103.0)
        self.assertAlmostEqual(
            (extent[1] - extent[0]) / (extent[3] - extent[2]),
            2.0,
        )

    def test_rejects_non_positive_image_aspect_ratio(self) -> None:
        with self.assertRaisesRegex(ValueError, "aspect ratio must be positive"):
            fit_extent_to_aspect((5.0, 16.0, 47.0, 56.0), 0)


if __name__ == "__main__":
    unittest.main()
