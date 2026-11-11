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

        frame = decode_radar_file(contents.getvalue())

        self.assertEqual(frame.extent, (6.5, 12.5, 50.0, 54.2))


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
        velocity = np.zeros((10, 10), dtype=np.float32)
        velocity[2:4, 2:5] = -15.0
        velocity[2:4, 5:8] = 15.0
        frame = RadarFrame(velocity, None, extent=(0.0, 10.0, 0.0, 10.0))

        detections = detect_rotations(frame)

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertAlmostEqual(detection.latitude, 7.0)
        self.assertAlmostEqual(detection.longitude, 5.0)
        self.assertEqual(detection.negative_pixels, 6)
        self.assertEqual(detection.positive_pixels, 6)
        self.assertTrue(rotation_detection(frame))

    def test_ignores_small_or_separated_velocity_regions(self) -> None:
        velocity = np.zeros((12, 12), dtype=np.float32)
        velocity[1:3, 1:3] = -20.0
        velocity[1:3, 8:10] = 20.0

        self.assertEqual(detect_rotations(RadarFrame(velocity, None)), ())
        self.assertFalse(rotation_detection(RadarFrame(velocity, None)))


if __name__ == "__main__":
    unittest.main()
