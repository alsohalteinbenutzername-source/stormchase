from __future__ import annotations

import asyncio
from typing import Callable

import websockets

from lightning import LightningStrike, build_blitzortung_request, parse_blitzortung_message


BLITZORTUNG_STREAM_URLS = (
    "wss://live.lightningmaps.org:443/",
    "wss://live2.lightningmaps.org:443/",
)


async def stream_blitzortung_lightning(
    on_strike: Callable[[LightningStrike], None],
    on_status: Callable[[str], None],
) -> None:
    """Keep the public LightningMaps feed connected and forward Germany strokes."""
    last_ids: dict[str, int] = {}
    retry_seconds = 1
    attempt = 0

    while True:
        url = BLITZORTUNG_STREAM_URLS[attempt % len(BLITZORTUNG_STREAM_URLS)]
        attempt += 1
        try:
            on_status("Connecting to Blitzortung lightning…")
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
            ) as connection:
                await connection.send(build_blitzortung_request(last_ids))
                retry_seconds = 1
                on_status("Blitzortung lightning connected")
                async for message in connection:
                    for strike in parse_blitzortung_message(message):
                        source_id, stroke_id = strike.identifier.split("/", maxsplit=1)
                        last_ids[source_id] = max(last_ids.get(source_id, 0), int(stroke_id))
                        on_strike(strike)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            on_status(f"Blitzortung lightning reconnecting ({type(error).__name__})")
            await asyncio.sleep(retry_seconds)
            retry_seconds = min(retry_seconds * 2, 60)
