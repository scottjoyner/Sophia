# Sophia

Current Hermes deployment docs are in `voice-agent/README.md`.
The `voice-agent` service exposes container-ready `/healthz`, `/readyz`,
`/status`, `/events`, `/intent`, `/voice-chat`, and websocket `/ws` endpoints.

```bash
cd voice-agent
docker compose up --build
```

The legacy root README is preserved for the original dictation experiments.
