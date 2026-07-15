from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from voice_agent.server.protocols import encode_message
from voice_agent.util.audio import b64encode, float_to_pcm16_bytes, read_wav


def resample_to_16k(samples: np.ndarray, sr: int) -> np.ndarray:
    if sr == 16000:
        return samples
    from scipy import signal
    return signal.resample_poly(samples, 16000, sr)


async def run_test(wav_path: str, user_id: str = "scott", protocol: str = "hermes_overlay_v1"):
    url = "ws://localhost:8765/ws"
    samples, sr = read_wav(wav_path)
    print(f"Loaded {wav_path}: {len(samples)} samples @ {sr}Hz ({len(samples)/sr:.2f}s)")

    if sr != 16000:
        samples = resample_to_16k(samples, sr)
        sr = 16000
        print(f"Resampled to 16kHz: {len(samples)} samples ({len(samples)/sr:.2f}s)")

    chunk_ms = 30
    chunk_size = int(sr * (chunk_ms / 1000))

    async with websockets.connect(url, ping_interval=None) as ws:
        # start_session
        msg = encode_message(protocol, "start_session", {
            "session_id": "e2e_test",
            "sample_rate": sr,
            "channels": 1,
            "encoding": "pcm_s16le",
            "user_id": user_id,
        })
        await ws.send(json.dumps(msg))
        resp = json.loads(await ws.recv())
        print(f"ACK: {resp}")

        # send all audio chunks as fast as possible
        seq = 0
        for offset in range(0, len(samples), chunk_size):
            chunk = samples[offset:offset + chunk_size]
            if len(chunk) == 0:
                break
            pcm = float_to_pcm16_bytes(chunk)
            msg = encode_message(protocol, "audio_chunk", {
                "session_id": "e2e_test",
                "seq": seq,
                "chunk_ms": chunk_ms,
                "pcm_bytes": b64encode(pcm),
                "t_client_ms": int(time.time() * 1000),
            })
            await ws.send(json.dumps(msg))
            # drain ack + any events for THIS chunk
            await _drain_one(ws)
            seq += 1

        print(f"Sent {seq} chunks. Sending end_session...")
        # end_session
        msg = encode_message(protocol, "end_session", {"session_id": "e2e_test"})
        await ws.send(json.dumps(msg))
        # drain all remaining messages
        await _drain_all(ws, timeout=60.0)


async def _drain_one(ws, timeout=1.0):
    try:
        data = await asyncio.wait_for(ws.recv(), timeout=timeout)
        ev = json.loads(data)
        _show_event(ev)
    except TimeoutError:
        pass


async def _drain_all(ws, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            data = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 0.5))
            ev = json.loads(data)
            _show_event(ev)
        except TimeoutError:
            break


def _show_event(ev: dict):
    if "frame" in ev:
        inner = ev["frame"]
        etype = inner.get("type", "?")
        epayload = inner.get("payload", {})
    else:
        etype = ev.get("type", "?")
        epayload = ev.get("payload", {})
    if etype == "event":
        etype = epayload.get("type", "event")
        epayload = epayload.get("payload", {})
    if etype == "auth_decision":
        score = epayload.get("score", "?")
        accepted = epayload.get("accepted", "?")
        print(f"  >>> AUTH: accepted={accepted}, score={score:.4f}")
    elif etype == "tts_output":
        path = epayload.get("path", "?")
        text = epayload.get("text", "")
        print(f"  >>> TTS: path={path}")
        if text:
            print(f"         text: {text[:100]}")
    elif etype in ("stt_final", "stt_partial"):
        text = epayload.get("text", "")
        print(f"  >>> STT: {text[:80]}")
    elif etype == "session_end":
        print("  >>> SESSION END")
    elif etype == "stt_refine":
        text = epayload.get("text", "")
        print(f"  >>> STT_REFINE: {text[:80]}")
    elif etype == "ack":
        pass  # skip ack noise
    else:
        print(f"  >>> {etype}: {json.dumps(epayload)[:100]}")


if __name__ == "__main__":
    import websockets
    wav = sys.argv[1] if len(sys.argv) > 1 else ""
    if not wav:
        td = Path("/ssd-ingest/voice-insight/training/scott")
        import json as _json
        mf = td / "manifest.jsonl"
        lines = [ln for ln in mf.read_text().splitlines() if ln]
        entry = _json.loads(lines[len(lines) // 2])
        wav = entry["container_path"]
        print(f"No WAV arg; using midpoint training clip: {wav}")
    asyncio.run(run_test(wav))
