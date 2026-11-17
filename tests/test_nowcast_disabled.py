from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from ui import LIVE_MAP, REFLECTIVITY_RADAR, StormRadarApp


class PointNowcastDisabledTests(unittest.TestCase):
    def test_map_click_does_not_schedule_a_nowcast_request(self) -> None:
        app = object.__new__(StormRadarApp)
        app.axes = object()
        app.map_mode = LIVE_MAP
        app.radar_product = REFLECTIVITY_RADAR
        app._weather_location = None
        app.weather_detail = Mock()
        app.loop = Mock()
        app._draw_map = Mock()
        event = SimpleNamespace(
            button=1,
            inaxes=app.axes,
            xdata=7.4,
            ydata=51.2,
        )

        app._on_map_click(event)

        app.loop.create_task.assert_not_called()
        app.weather_detail.set.assert_called_once_with(
            "Point nowcast is disabled to conserve API limits."
        )
        self.assertEqual(app._weather_location, (7.4, 51.2))


if __name__ == "__main__":
    unittest.main()
