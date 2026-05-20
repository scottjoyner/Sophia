# voice-agent

A lightweight Sophia voice overlay service intended for Hermes-agent workflows.
It keeps voice orchestration (STT/VAD/auth/TTS) separate from the core agent runtime
and supports protocol adaptation for containerized communication.

## Design goals
- Overlay architecture: voice I/O is decoupled from core agent logic.
- Container-friendly protocol: supports native websocket and `hermes_overlay_v1` envelope.
- Low-memory defaults (5-10 GB target): tiny/int8 STT profile, fallback TTS, mock/openai-compatible LLM.

## Quickstart
```bash
pip install -e .
voice-agent serve --host 0.0.0.0 --port 8765 --config configs/dev.yaml
```

## Protocols
`server.protocol` in config controls websocket message parsing:
- `native_ws`: `{type, payload}`
- `hermes_overlay_v1`: `{protocol, frame: {type, payload}, meta}`

## Recommended model profile (5-10 GB RAM)
- STT: `faster-whisper tiny` with `int8`, `cpu_threads=2`
- TTS: fallback (or Piper if prebuilt voice is available)
- LLM: external API/OpenAI-compatible endpoint, keep local memory pressure low

## CLI
```bash
voice-agent enroll --user scott --mic --phrases tests/fixtures/enroll_phrases.txt --n 5
voice-agent verify --user scott --mic --seconds 3
voice-agent mic --url ws://localhost:8765/ws --user scott
voice-agent replay --url ws://localhost:8765/ws --wav tests/fixtures/sample.wav --ref tests/fixtures/sample.txt --user scott
voice-agent bench --dataset dataset/manifest.jsonl --out runs/demo --config configs/bench.yaml
voice-agent report --run runs/demo/results.sqlite
```
