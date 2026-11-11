from types import SimpleNamespace
import unittest

from loctrack import location_from_geoposition


class LocationFromGeopositionTests(unittest.TestCase):
    def test_converts_valid_windows_coordinate(self) -> None:
        position = SimpleNamespace(
            coordinate=SimpleNamespace(
                latitude=51.5,
                longitude=7.4,
                accuracy=12.0,
            )
        )

        location = location_from_geoposition(position)

        self.assertEqual(location.latitude, 51.5)
        self.assertEqual(location.longitude, 7.4)
        self.assertEqual(location.accuracy_meters, 12.0)

    def test_rejects_invalid_windows_coordinate(self) -> None:
        position = SimpleNamespace(
            coordinate=SimpleNamespace(
                latitude=91.0,
                longitude=7.4,
                accuracy=12.0,
            )
        )

        with self.assertRaisesRegex(ValueError, "outside the valid range"):
            location_from_geoposition(position)


if __name__ == "__main__":
    unittest.main()
