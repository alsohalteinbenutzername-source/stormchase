from io import BytesIO
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import numpy as np

from ui import fetch_dwd_warning_overlay


class DwdWarningOverlayTests(unittest.TestCase):
    def test_draws_warning_polygons_from_the_dwd_feature_service(self) -> None:
        payload = {
            "features": [
                {
                    "properties": {"EC_AREA_COLOR": "251 140 0"},
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [
                            [
                                [
                                    [2.0, 2.0],
                                    [8.0, 2.0],
                                    [8.0, 8.0],
                                    [2.0, 8.0],
                                    [2.0, 2.0],
                                ]
                            ]
                        ],
                    },
                }
            ]
        }
        with patch(
            "ui.urlopen",
            return_value=BytesIO(json.dumps(payload).encode("utf-8")),
        ) as open_url:
            image, extent = fetch_dwd_warning_overlay(
                (0.0, 10.0, 0.0, 10.0),
                100,
                100,
            )

        query = parse_qs(urlparse(open_url.call_args.args[0]).query)
        self.assertEqual(query["request"], ["GetFeature"])
        self.assertEqual(query["typeNames"], ["dwd:Warnungen_Gemeinden_vereinigt"])
        self.assertEqual(extent, (0.0, 10.0, 0.0, 10.0))
        np.testing.assert_array_equal(image[50, 50], (251, 140, 0, 110))
        self.assertEqual(image.shape, (100, 100, 4))


if __name__ == "__main__":
    unittest.main()
