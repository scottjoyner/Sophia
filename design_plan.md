# Sophia Voice Overlay Refactor Plan

## Current architecture review
- Existing `voice-agent` implementation already separates concerns into STT, auth, LLM loop, and TTS pipeline.
- Websocket server previously assumed a single native payload shape and lacked explicit Hermes overlay semantics.
- STT defaults used a larger implicit model profile (`base`) not tuned for 5-10 GB RAM containers.

## Refactor applied
1. Added protocol adapter layer with `native_ws` and `hermes_overlay_v1` decoders.
2. Added runtime/model profile settings in config to support memory-constrained deployments.
3. Switched streaming STT model selection to config-driven values (default tiny/int8).
4. Updated docs and dev config to align with Hermes overlay operation.

## Deployment shape
- Preferred as a lightweight sidecar container:
  - Websocket ingress from Hermes runtime.
  - Emits transcript/auth/tts events through shared run artifacts (DB + JSONL) and websocket acks.
- Optional split into microservices later:
  - `voice-ingress` (websocket + VAD)
  - `voice-inference` (STT/TTS/LLM orchestration)
  - `voice-auth` (speaker verification)

## Suggested Hugging Face options under 5-10 GB RAM
- STT: `faster-whisper tiny` or `base` int8 if latency allows.
- TTS: Piper lightweight voice, or fallback synth if no model volume is mounted.
- Avoid loading both heavyweight local LLM + larger STT in the same container.

## Next integration step
- Wire Hermes-agent to send `hermes_overlay_v1` envelope frames and consume ack/event stream.
