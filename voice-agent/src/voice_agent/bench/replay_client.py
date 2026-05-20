from __future__ import annotations

import asyncio
import json
from typing import Optional

import websockets

from ..util.audio import b64encode, float_to_pcm16_bytes, read_wav
from ..util.time import now_ms


async def replay_wav(url: str, wav_path: str, user_id: str, session_id: str = "session") -> None:
    samples, sr = read_wav(wav_path)
    chunk_ms = 30
    chunk_size = int(sr * (chunk_ms / 1000))
    async with websockets.connect(url) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "start_session",
                    "payload": {
                        "session_id": session_id,
                        "sample_rate": sr,
                        "channels": 1,
                        "encoding": "pcm_s16le",
                        "user_id": user_id,
                    },
                }
            )
        )
        await ws.recv()
        seq = 0
        for idx in range(0, len(samples), chunk_size):
            chunk = samples[idx : idx + chunk_size]
            pcm = float_to_pcm16_bytes(chunk)
            await ws.send(
                json.dumps(
                    {
                        "type": "audio_chunk",
                        "payload": {
                            "session_id": session_id,
                            "seq": seq,
                            "chunk_ms": chunk_ms,
                            "pcm_bytes": b64encode(pcm),
                            "t_client_ms": now_ms(),
                        },
                    }
                )
            )
            await ws.recv()
            await asyncio.sleep(chunk_ms / 1000)
            seq += 1
        await ws.send(json.dumps({"type": "end_session", "payload": {"session_id": session_id}}))
        await ws.recv()
