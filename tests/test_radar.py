from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import h5py
import numpy as np

from radar import (
    DwdRadarSite,
    RadarFrame,
    VELOCITY_PRODUCT,
    decode_radar_file,
    detect_rotations,
    fetch_latest_velocity_radar,
    nearest_velocity_radar_site,
    rotation_detection,
)


class DecodeRadarFileTests(unittest.TestCase):
    def test_decodes_odim_gain_offset_and_missing_values(self) -> None:
        contents = BytesIO()
        with h5py.File(contents, "w") as radar_file:
            data_group = radar_file.create_group("dataset1/data1")
            data_group.create_dataset("data", data=np.array([[0, 40], [255, 80]], dtype="u1"))
            metadata = data_group.create_group("what")
            metadata.attrs["gain"] = 0.5
            metadata.attrs["offset"] = -32.0
            metadata.attrs["nodata"] = 255

        frame = decode_radar_file(contents.getvalue())

        self.assertEqual(frame.reflectivity.shape, (2, 2))
        self.assertIs(frame.values, frame.reflectivity)
        self.assertEqual(frame.reflectivity[0, 0], -32.0)
        self.assertEqual(frame.reflectivity[0, 1], -12.0)
        self.assertTrue(np.isnan(frame.reflectivity[1, 0]))
        self.assertEqual(frame.reflectivity[1, 1], 8.0)

    def test_rejects_hdf5_without_a_grid(self) -> None:
        contents = BytesIO()
        with h5py.File(contents, "w"):
            pass

        with self.assertRaisesRegex(ValueError, "contains no radar grid"):
            decode_radar_file(contents.getvalue())

    def test_fetches_latest_published_velocity_product(self) -> None:
        expected_frame = RadarFrame(np.zeros((1, 1), dtype=np.float32), None)
        result = SimpleNamespace(
            data=SimpleNamespace(getvalue=lambda: b"radar data"),
            timestamp=None,
            data_updated_at=None,
        )
        with (
            patch("radar.DwdRadarValues") as values,
            patch("radar.decode_radar_file", return_value=expected_frame) as decode,
        ):
            values.return_value.query.return_value = iter((result,))

            frame = fetch_latest_velocity_radar(DwdRadarSite.HNR)

        self.assertIs(frame, expected_frame)
        values.assert_called_once()
        self.assertEqual(values.call_args.kwargs["parameter"], VELOCITY_PRODUCT)
        self.assertEqual(values.call_args.kwargs["site"], DwdRadarSite.HNR)
        decode.assert_called_once_with(b"radar data", timestamp=None)

    def test_decodes_geographic_extent_from_velocity_grid(self) -> None:
        contents = BytesIO()
        with h5py.File(contents, "w") as radar_file:
            data_group = radar_file.create_group("dataset3/data1")
            data_group.create_dataset("data", data=np.ones((2, 2), dtype="u1"))
            where = radar_file["dataset3"].create_group("where")
            for name, value in {
                "LL_lon": 7.0,
                "LR_lon": 12.0,
                "UL_lon": 6.5,
                "UR_lon": 12.5,
                "LL_lat": 50.0,
                "LR_lat": 50.2,
                "UL_lat": 54.0,
                "UR_lat": 54.2,
            }.items():
                where.attrs[name] = value
            where.attrs["projdef"] = "+proj=longlat +datum=WGS84 +no_defs"
            where.attrs["xscale"] = 0.5
            where.attrs["yscale"] = 0.5
            where.attrs["xsize"] = 2
            where.attrs["ysize"] = 2

        frame = decode_radar_file(contents.getvalue())

        self.assertEqual(frame.extent, (6.5, 12.5, 50.0, 54.2))
        self.assertEqual(
            frame.projection,
            "+proj=longlat +datum=WGS84 +no_defs",
        )
        self.assertEqual((frame.xscale, frame.yscale), (0.5, 0.5))

    def test_decodes_velocity_beam_and_station_metadata(self) -> None:
        contents = BytesIO()
        with h5py.File(contents, "w") as radar_file:
            scan = radar_file.create_group("dataset1")
            scan_where = scan.create_group("where")
            scan_where.attrs["elangle"] = 0.5
            scan_where.attrs["rscale"] = 1000.0
            scan_where.attrs["rstart"] = 0.0
            data_group = scan.create_group("data1")
            data_group.create_dataset("data", data=np.ones((2, 2), dtype="u1"))
            station_where = radar_file.create_group("where")
            station_where.attrs["lat"] = 50.0
            station_where.attrs["lon"] = 9.0
            station_where.attrs["height"] = 250.0

        frame = decode_radar_file(contents.getvalue())

        self.assertEqual(frame.site_latitude, 50.0)
        self.assertEqual(frame.site_longitude, 9.0)
        self.assertEqual(frame.site_altitude_m, 250.0)
        self.assertEqual(frame.elevation_angle_degrees, 0.5)
        self.assertEqual(frame.range_scale_m, 1000.0)
        self.assertEqual(frame.range_start_km, 0.0)

    def test_reprojects_projected_grid_for_geographic_map_display(self) -> None:
        frame = RadarFrame(
            np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
            None,
            extent=(-0.5, 1.5, -1.5, 0.5),
            projection="+proj=longlat +datum=WGS84 +no_defs",
            xscale=1.0,
            yscale=1.0,
        )

        np.testing.assert_array_equal(
            frame.geographic_values,
            np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
        )

    def test_decodes_top_level_projection_metadata(self) -> None:
        contents = BytesIO()
        with h5py.File(contents, "w") as radar_file:
            data_group = radar_file.create_group("dataset1/data1")
            data_group.create_dataset("data", data=np.ones((2, 2), dtype="u1"))
            where = radar_file.create_group("where")
            for name, value in {
                "LL_lon": 0.0,
                "LR_lon": 2.0,
                "UL_lon": 0.0,
                "UR_lon": 2.0,
                "LL_lat": 0.0,
                "LR_lat": 0.0,
                "UL_lat": 2.0,
                "UR_lat": 2.0,
                "xscale": 1.0,
                "yscale": 1.0,
                "xsize": 2,
                "ysize": 2,
            }.items():
                where.attrs[name] = value
            where.attrs["projdef"] = "+proj=longlat +datum=WGS84 +no_defs"

        frame = decode_radar_file(contents.getvalue())

        self.assertEqual(frame.extent, (0.0, 2.0, 0.0, 2.0))
        self.assertEqual(frame.projection, "+proj=longlat +datum=WGS84 +no_defs")


class NearestVelocityRadarSiteTests(unittest.TestCase):
    def test_selects_nearest_active_german_doppler_station(self) -> None:
        site = nearest_velocity_radar_site(52.46, 9.6945)

        self.assertEqual(site.site, DwdRadarSite.HNR)
        self.assertEqual(site.name, "Hannover")

    def test_rejects_invalid_coordinates(self) -> None:
        with self.assertRaisesRegex(ValueError, "Latitude"):
            nearest_velocity_radar_site(91, 9)


class RotationDetectionTests(unittest.TestCase):
    def test_detects_adjacent_velocity_couplet_and_returns_its_map_location(self) -> None:
        velocity = np.zeros((5, 10), dtype=np.float32)
        velocity[2:4, 0:5] = -20.0
        velocity[2:4, 5:10] = 20.0
        # A 10-degree grid would span hundreds of kilometres and fail the
        # detector's physical shear criteria. Model a compact radar couplet.
        frame = RadarFrame(velocity, None, extent=(0.0, 0.015, 0.0, 0.005))

        detections = detect_rotations(frame)

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertAlmostEqual(detection.latitude, 0.0025)
        self.assertAlmostEqual(detection.longitude, 0.009)
        self.assertEqual(detection.negative_pixels, 10)
        self.assertEqual(detection.positive_pixels, 10)
        self.assertTrue(rotation_detection(frame))

    def test_ignores_small_or_separated_velocity_regions(self) -> None:
        velocity = np.zeros((12, 12), dtype=np.float32)
        velocity[1:3, 1:3] = -15.0
        velocity[1:3, 8:10] = 15.0

        self.assertEqual(detect_rotations(RadarFrame(velocity, None)), ())
        self.assertFalse(rotation_detection(RadarFrame(velocity, None)))

    def test_rejects_broad_but_weaker_adjacent_couplet(self) -> None:
        velocity = np.zeros((12, 12), dtype=np.float32)
        velocity[2:6, 2:6] = -19.9
        velocity[2:6, 6:10] = 20.0

        self.assertEqual(detect_rotations(RadarFrame(velocity, None)), ())

    def test_rejects_small_but_strong_adjacent_couplet(self) -> None:
        velocity = np.zeros((12, 12), dtype=np.float32)
        velocity[2:4, 2:4] = -20.0
        velocity[2:4, 4:6] = 20.0

        self.assertEqual(detect_rotations(RadarFrame(velocity, None)), ())


if __name__ == "__main__":
    unittest.main()
