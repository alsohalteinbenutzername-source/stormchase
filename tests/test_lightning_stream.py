import asyncio
import json
import unittest
from unittest.mock import patch

import lightning_stream
from lightning import build_blitzortung_request, parse_blitzortung_message


class BlitzortungMessageTests(unittest.TestCase):
    def test_builds_germany_subscription_request_with_resume_ids(self) -> None:
        request = json.loads(build_blitzortung_request({"17": 42}))

        self.assertEqual(request["v"], 24)
        self.assertEqual(request["i"], {"17": 42})
        self.assertEqual(request["p"], [56.0, 16.0, 47.0, 5.0])
        self.assertFalse(request["s"])
        self.assertEqual(request["r"], "feed")

    def test_parses_valid_germany_strokes_and_skips_invalid_records(self) -> None:
        message = json.dumps(
            {
                "strokes": [
                    {
                        "src": 17,
                        "id": 42,
                        "time": 1794380000000,
                        "lat": 50.1,
                        "lon": 10.2,
                        "dev": 1250,
                    },
                    {"src": 17, "id": 43, "time": 1794380000000, "lat": 40, "lon": 10},
                    {"src": 17, "id": 44, "time": "invalid", "lat": 50, "lon": 10},
                ]
            }
        )

        strikes = parse_blitzortung_message(message)

        self.assertEqual(len(strikes), 1)
        self.assertEqual(strikes[0].identifier, "17/42")
        self.assertEqual((strikes[0].latitude, strikes[0].longitude), (50.1, 10.2))
        self.assertEqual(strikes[0].uncertainty_km, 1.25)
        self.assertIsNotNone(strikes[0].timestamp.tzinfo)

    def test_ignores_invalid_messages(self) -> None:
        self.assertEqual(parse_blitzortung_message("not json"), ())
        self.assertEqual(parse_blitzortung_message('{"strokes":[]}'), ())


class BlitzortungStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_subscription_and_forwards_live_strikes(self) -> None:
        class FakeConnection:
            def __init__(self) -> None:
                self.request = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def send(self, request: str) -> None:
                self.request = json.loads(request)

            def __aiter__(self):
                async def messages():
                    yield json.dumps(
                        {
                            "strokes": [
                                {
                                    "src": 17,
                                    "id": 42,
                                    "time": 1794380000000,
                                    "lat": 50.1,
                                    "lon": 10.2,
                                }
                            ]
                        }
                    )
                    raise asyncio.CancelledError

                return messages()

        connection = FakeConnection()
        strikes = []
        statuses = []
        with patch.object(
            lightning_stream.websockets,
            "connect",
            return_value=connection,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await lightning_stream.stream_blitzortung_lightning(
                    strikes.append,
                    statuses.append,
                )

        self.assertEqual(connection.request["p"], [56.0, 16.0, 47.0, 5.0])
        self.assertEqual([strike.identifier for strike in strikes], ["17/42"])
        self.assertEqual(statuses[-1], "Blitzortung lightning connected")


if __name__ == "__main__":
    unittest.main()
