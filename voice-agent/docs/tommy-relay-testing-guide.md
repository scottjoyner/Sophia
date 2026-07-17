# Tommy Relay Testing Guide

This guide is the operator checklist for proving the Tommy telescope relay is solid before trusting it for live voice sessions across the mesh. It covers fast local verification, API smoke tests, browser/WebRTC signaling checks, websocket fallback, restart recovery, and failure drills.

## Scope

Use this guide whenever relay code, deployment config, mesh device enrollment, or Tommy browser behavior changes.

The relay must prove these properties:

- The logical `tommy` session survives device changes and service restarts.
- Only an enrolled device with both `device_token` and active `lease_token` can send media, transcripts, WebRTC signaling, or negotiation status requests.
- Admin-only observability and takeover routes fail closed without `x-relay-admin-token`.
- Audio sequence numbers dedupe duplicates and expose missing ranges.
- WebRTC signaling state is durable enough for a telescope to recover after reconnect.
- Websocket/HTTP audio fallback works even if WebRTC/SFU media is not ready.

## Prerequisites

From the repo root:

```bash
cd ~/git/Sophia/voice-agent
/usr/bin/python3 -m pip install -e '.[dev]'
```

Environment variables for local smoke tests:

```bash
export TOMMY_RELAY_ADMIN_TOKEN='replace-with-local-admin-token'
# Optional: only set this when the relay server is configured to require enrollment proof.
export TOMMY_RELAY_ENROLLMENT_TOKEN='replace-with-local-enrollment-token'
export SOPHIA_SESSION_SECRET='replace-with-local-session-secret'
export SOPHIA_APP_PASSWORD='replace-with-local-console-password'
```

For public/tailnet testing, prefer the real HTTPS relay URL routed through x1-370/Caddy. For local tests, `http://127.0.0.1:8765` is enough.

## 1. Automated verification

Run the fast relay-focused suite first:

```bash
ruff check .
/usr/bin/python3 -m pytest \
  tests/test_tommy_relay.py \
  tests/test_tommy_relay_next.py \
  tests/test_tommy_relay_security.py \
  tests/test_tommy_relay_agent.py \
  -q
```

Then run the full project suite:

```bash
/usr/bin/python3 -m pytest tests/ -q
```

Expected current baseline after the WebRTC negotiation-status work:

```text
210 passed, 2 skipped
```

Run whitespace/diff hygiene before commit:

```bash
git diff --check
```

Run an added-line security scan before committing code changes:

```bash
git diff --cached | grep '^+' | grep -Ei "(api_key|secret|password|token|passwd)[[:space:]]*=[[:space:]]*['\"][^'\"]{6,}['\"]" || true
git diff --cached | grep '^+' | grep -E 'os\.system\(|subprocess.*shell=True|\beval\(|\bexec\(|pickle\.loads?\(|execute\(f"|\.format\(.*SELECT|\.format\(.*INSERT' || true
```

Any hit requires manual review. Placeholders in documentation are okay; real credentials are not.

## 2. Start a local relay service

Terminal A:

```bash
cd ~/git/Sophia/voice-agent
export TOMMY_RELAY_ADMIN_TOKEN='local-admin'
export SOPHIA_SESSION_SECRET='local-session-secret'
export SOPHIA_APP_PASSWORD='local-console-password'
voice-agent serve --host 127.0.0.1 --port 8765 --config configs/dev.yaml
```

Terminal B:

```bash
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS http://127.0.0.1:8765/readyz
curl -fsS -H 'x-relay-admin-token: local-admin' http://127.0.0.1:8765/relay/health | jq .
curl -fsS -H 'x-relay-admin-token: local-admin' http://127.0.0.1:8765/relay/readiness | jq .
```

Pass criteria:

- `/healthz` and `/readyz` return HTTP 200.
- `/relay/health` reports the relay store path and worker state.
- `/relay/readiness` is ready or reports only an expected local-development dependency gap.

## 3. Register and attach a telescope device

Use a unique device id per real device. For a local smoke test:

```bash
BASE=http://127.0.0.1:8765
DEVICE_ID=scope-local-$(date +%s)

REGISTER_PAYLOAD=$(jq -nc --arg device_id "$DEVICE_ID" --arg enrollment_token "${TOMMY_RELAY_ENROLLMENT_TOKEN:-}" '
  {
    device_id: $device_id,
    name: "Local Test Scope",
    owner_id: "scott",
    capabilities: ["mic", "speaker", "browser_audio"],
    platform: "linux",
    mesh_node: "local",
    audio_source: "pipewire:default",
    location: "test"
  } + (if $enrollment_token != "" then {enrollment_token: $enrollment_token} else {} end)
')
REGISTER_JSON=$(curl -fsS -X POST "$BASE/relay/devices/register" \
  -H 'content-type: application/json' \
  -d "$REGISTER_PAYLOAD")
DEVICE_TOKEN=$(printf '%s' "$REGISTER_JSON" | jq -r .device_token)

ATTACH_JSON=$(curl -fsS -X POST "$BASE/relay/sessions/tommy/attach" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\"}")
LEASE_TOKEN=$(printf '%s' "$ATTACH_JSON" | jq -r .lease_token)
RESUME_TOKEN=$(printf '%s' "$ATTACH_JSON" | jq -r .resume_token)
export DEVICE_ID DEVICE_TOKEN LEASE_TOKEN RESUME_TOKEN
printf 'device=%s\nlease=%s\nresume=%s\n' "$DEVICE_ID" "$LEASE_TOKEN" "$RESUME_TOKEN"
```

Pass criteria:

- Register returns a one-time `device_token`.
- Attach returns `active_device_id`, `lease_token`, `resume_token`, and websocket fallback metadata.
- Admin session status does **not** expose token hashes or raw persisted tokens:

```bash
curl -fsS -H 'x-relay-admin-token: local-admin' "$BASE/relay/sessions/tommy" | jq .
```

## 4. Security fail-closed smoke checks

These must fail without the right credentials:

```bash
# Admin route without admin token: expect 401
curl -s -o /tmp/relay-devices.out -w '%{http_code}\n' "$BASE/relay/devices"

# Audio without device token: expect 401
curl -s -o /tmp/relay-audio.out -w '%{http_code}\n' -X POST "$BASE/relay/sessions/tommy/audio" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"lease_token\":\"$LEASE_TOKEN\",\"seq\":1,\"encoding\":\"pcm_s16le\",\"byte_count\":16}"

# Transcript without device token: expect 401
curl -s -o /tmp/relay-transcript.out -w '%{http_code}\n' -X POST "$BASE/relay/sessions/tommy/transcript" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"lease_token\":\"$LEASE_TOKEN\",\"seq\":1,\"text\":\"hello\"}"
```

Pass criteria: each command prints `401`.

## 5. Audio sequencing, dedupe, and gap checks

Send an out-of-order sequence to create a gap:

```bash
curl -fsS -X POST "$BASE/relay/sessions/tommy/audio" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\",\"lease_token\":\"$LEASE_TOKEN\",\"seq\":1,\"encoding\":\"pcm_s16le\",\"byte_count\":16}" | jq .

curl -fsS -X POST "$BASE/relay/sessions/tommy/audio" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\",\"lease_token\":\"$LEASE_TOKEN\",\"seq\":3,\"encoding\":\"pcm_s16le\",\"byte_count\":16}" | jq .

curl -fsS -H 'x-relay-admin-token: local-admin' "$BASE/relay/sessions/tommy/gaps" | jq .
```

Pass criteria:

- First accepted frame reports a gap if sequence 0 was missing.
- Second accepted frame reports missing range `[2, 2]`.
- `/gaps` includes unresolved missing ranges and `expected_seq`.

Send the missing frame and a duplicate:

```bash
curl -fsS -X POST "$BASE/relay/sessions/tommy/audio" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\",\"lease_token\":\"$LEASE_TOKEN\",\"seq\":2,\"encoding\":\"pcm_s16le\",\"byte_count\":16}" | jq .

curl -fsS -X POST "$BASE/relay/sessions/tommy/audio" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\",\"lease_token\":\"$LEASE_TOKEN\",\"seq\":2,\"encoding\":\"pcm_s16le\",\"byte_count\":16}" | jq .
```

Pass criteria:

- Filling sequence `2` removes `[2, 2]` from missing ranges.
- Duplicate sequence returns `accepted: false` and `reason: duplicate_sequence`.

## 6. Transcript outbox and assistant-turn persistence

```bash
curl -fsS -X POST "$BASE/relay/sessions/tommy/transcript" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\",\"lease_token\":\"$LEASE_TOKEN\",\"seq\":10,\"text\":\"testing Tommy relay\",\"partial\":false,\"source\":\"manual-smoke\"}" | jq .

curl -fsS -H 'x-relay-admin-token: local-admin' "$BASE/relay/outbox/pending" | jq .
curl -fsS -H 'x-relay-admin-token: local-admin' "$BASE/relay/sessions/tommy/timeline" | jq '.events[-5:], .transcripts[-5:]'
```

Pass criteria:

- Final transcript is accepted and queued unless already processed immediately by a live worker.
- Timeline contains the transcript event and related assistant output or error event.

## 7. WebRTC signaling and durable negotiation lookup

Create an offer:

```bash
OFFER_JSON=$(curl -fsS -X POST "$BASE/relay/sessions/tommy/webrtc/offer" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\",\"lease_token\":\"$LEASE_TOKEN\",\"sdp\":\"v=0\\r\\n\",\"type\":\"offer\"}")
OFFER_ID=$(printf '%s' "$OFFER_JSON" | jq -r .signaling.offer_id)
printf 'offer=%s\n' "$OFFER_ID"
```

Check pending offers:

```bash
curl -fsS -H 'x-relay-admin-token: local-admin' "$BASE/relay/sessions/tommy/webrtc/offers/pending" | jq .
```

Record an answer and ICE candidate:

```bash
curl -fsS -X POST "$BASE/relay/sessions/tommy/webrtc/offers/$OFFER_ID/answer" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\",\"lease_token\":\"$LEASE_TOKEN\",\"sdp\":\"v=0\\r\\na=answer\\r\\n\",\"type\":\"answer\"}" | jq .

curl -fsS -X POST "$BASE/relay/sessions/tommy/webrtc/offers/$OFFER_ID/candidate" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\",\"lease_token\":\"$LEASE_TOKEN\",\"candidate\":\"candidate:1 1 udp 1 127.0.0.1 9 typ host\",\"sdp_mid\":\"0\",\"sdp_mline_index\":0}" | jq .
```

Recover negotiation status as the originating telescope:

```bash
curl -fsS -X POST "$BASE/relay/sessions/tommy/webrtc/offers/$OFFER_ID/status" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\",\"lease_token\":\"$LEASE_TOKEN\"}" | jq .
```

Pass criteria:

- Offer returns `status: signaling_ready`, ICE servers, endpoints, and fallback websocket endpoint.
- Pending queue includes the offer before answer and omits it after answer.
- Status lookup returns `status: answered`, the original `offer.sdp`, `answer.sdp`, and candidate list.
- Status lookup without `device_token` returns `401`.

## 8. Websocket fallback smoke test

Use Python so the test is repeatable:

```bash
BASE_WS=ws://127.0.0.1:8765 /usr/bin/python3 - <<'PY'
import asyncio, json, os, websockets
base = os.environ.get('BASE_WS', 'ws://127.0.0.1:8765')
device_id = os.environ['DEVICE_ID']
device_token = os.environ['DEVICE_TOKEN']
lease_token = os.environ['LEASE_TOKEN']

async def main():
    async with websockets.connect(f"{base}/relay/sessions/tommy/stream") as ws:
        await ws.send(json.dumps({
            'type': 'authenticate',
            'device_id': device_id,
            'device_token': device_token,
            'lease_token': lease_token,
        }))
        print(await ws.recv())
        await ws.send(json.dumps({
            'type': 'heartbeat',
            'device_id': device_id,
            'device_token': device_token,
            'lease_token': lease_token,
            'seq': 99,
        }))
        print(await ws.recv())
        await ws.send(json.dumps({
            'type': 'transcript',
            'device_id': device_id,
            'device_token': device_token,
            'lease_token': lease_token,
            'seq': 100,
            'text': 'websocket fallback test',
            'partial': False,
            'source': 'manual-ws',
        }))
        print(await ws.recv())

asyncio.run(main())
PY
```

Pass criteria:

- Auth succeeds.
- Heartbeat renews the lease.
- Transcript frame is accepted and appears in `/relay/sessions/tommy/timeline`.

## 9. Restart and resume drill

1. Attach and save `DEVICE_ID`, `DEVICE_TOKEN`, `LEASE_TOKEN`, and `RESUME_TOKEN`.
2. Stop the service cleanly with `Ctrl-C`.
3. Start it again with the same workspace/artifacts directory.
4. Resume with the saved resume token:

```bash
RESUME_JSON=$(curl -fsS -X POST "$BASE/relay/sessions/tommy/resume" \
  -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE_ID\",\"device_token\":\"$DEVICE_TOKEN\",\"resume_token\":\"$RESUME_TOKEN\",\"last_seen_event_id\":0}")
printf '%s\n' "$RESUME_JSON" | jq .
LEASE_TOKEN="$(jq -r .lease_token <<<"$RESUME_JSON")"
export LEASE_TOKEN
```

Pass criteria:

- Resume returns a fresh `lease_token`.
- Replay contains prior relay events/transcripts.
- Session status still reports `session_id: tommy` and the active device.

## 10. Handoff/failover drills

Register a second trusted fallback device:

```bash
DEVICE_B=scope-fallback-$(date +%s)
REGISTER_B_PAYLOAD=$(jq -nc --arg device_id "$DEVICE_B" --arg enrollment_token "${TOMMY_RELAY_ENROLLMENT_TOKEN:-}" '
  {
    device_id: $device_id,
    name: "Fallback Scope",
    owner_id: "scott",
    capabilities: ["mic", "browser_audio"],
    fallback_priority: 5
  } + (if $enrollment_token != "" then {enrollment_token: $enrollment_token} else {} end)
')
REGISTER_B=$(curl -fsS -X POST "$BASE/relay/devices/register" \
  -H 'content-type: application/json' \
  -d "$REGISTER_B_PAYLOAD")
DEVICE_B_TOKEN=$(printf '%s' "$REGISTER_B" | jq -r .device_token)

curl -fsS -X POST "$BASE/relay/devices/$DEVICE_B/trust" \
  -H 'content-type: application/json' \
  -H 'x-relay-admin-token: local-admin' \
  -d '{"trusted":true}' | jq .
```

Manual handoff:

```bash
curl -fsS -X POST "$BASE/relay/sessions/tommy/handoff" \
  -H 'content-type: application/json' \
  -d "{\"from_device_id\":\"$DEVICE_ID\",\"to_device_id\":\"$DEVICE_B\",\"device_token\":\"$DEVICE_B_TOKEN\",\"lease_token\":\"$LEASE_TOKEN\",\"reason\":\"manual-smoke\"}" | jq .
```

Force/admin takeover, only for emergencies or controlled drills:

```bash
curl -fsS -X POST "$BASE/relay/sessions/tommy/force-handoff" \
  -H 'content-type: application/json' \
  -H 'x-relay-admin-token: local-admin' \
  -d "{\"from_device_id\":\"$DEVICE_ID\",\"to_device_id\":\"$DEVICE_B\",\"device_token\":\"$DEVICE_B_TOKEN\",\"reason\":\"admin-drill\"}" | jq .
```

Pass criteria:

- Old lease is revoked.
- New device receives a fresh lease.
- Timeline contains lease revoked + takeover events.
- Stale old lease cannot send audio or transcript after handoff.

## 11. Browser/telescope UI check

Open:

```text
http://127.0.0.1:8765/tommy
```

On tailnet/Caddy, open the routed `/tommy` URL.

Pass criteria:

- Page title says `Tommy Telescope Relay`.
- Browser asks for microphone permission.
- UI can register/attach the current browser.
- Browser creates an `RTCPeerConnection` offer.
- Websocket fallback streams `MediaRecorder` chunks if WebRTC peer/SFU path is not connected.
- Admin dashboard shows the active browser/device and recent events:

```text
/relay/dashboard
```

Use the browser devtools console/network tab to confirm:

- `/relay/devices/register` returns a `device_token` only once.
- `/relay/sessions/tommy/attach` returns a `lease_token` and `resume_token`.
- `/relay/sessions/tommy/webrtc/offer` returns `signaling_ready`.
- Websocket stays open or reconnects cleanly.

## 12. Public/tailnet deployment checks

On the service host:

```bash
systemctl --user status sophia-voice 2>/dev/null || true
ss -ltnp | grep -E ':8765|:443|:80' || true
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS -H 'x-relay-admin-token: <admin-token>' http://127.0.0.1:8765/relay/health | jq .
```

Through Caddy/tailnet:

```bash
curl -fsS https://<relay-host>/tommy >/tmp/tommy.html
curl -fsS -H 'x-relay-admin-token: <admin-token>' https://<relay-host>/relay/health | jq .
```

Pass criteria:

- Service is listening locally.
- Caddy/tailnet route reaches the same relay service.
- Admin endpoints require the admin token externally.
- Browser `/tommy` loads over HTTPS so microphone APIs are available.

## 13. Release checklist

Before calling a relay change solid:

- [ ] Relay-focused pytest files pass.
- [ ] Full test suite passes.
- [ ] `ruff check .` passes.
- [ ] `git diff --check` passes.
- [ ] Added-line security scan has no real findings.
- [ ] Register/attach smoke test works.
- [ ] Admin routes fail closed without token.
- [ ] Audio gap/dedupe behavior is verified.
- [ ] Transcript outbox and timeline are visible.
- [ ] WebRTC offer/answer/candidate/status flow works.
- [ ] Websocket fallback accepts auth, heartbeat, and transcript frames.
- [ ] Restart + resume drill returns replay state.
- [ ] Handoff or force-handoff drill revokes stale lease.
- [ ] `/tommy` browser path works on the target route.
- [ ] Docs and skill reference are updated when endpoint contracts change.

## Troubleshooting

### `401 Invalid relay device token`
Use the raw `device_token` returned by registration or token rotation. It is only returned once. Re-registering the same device normally preserves the existing hash; rotate the token if it was lost.

### `409 Invalid or stale lease`
Attach/resume again and use the fresh `lease_token`. Leases are intentionally short-lived and revoked on handoff/detach.

### Pending WebRTC offers never disappear
Confirm the answer endpoint was called with the same `offer_id`, `device_id`, `device_token`, and active `lease_token`. Check `/relay/events` for `relay_webrtc_answer_received`.

### Browser microphone does not open
Use HTTPS on tailnet/Caddy or `localhost` for local testing. Most browsers block microphone access on plain HTTP non-localhost origins.

### Resume returns no replay
Confirm the service restarted with the same workspace/artifacts directory and that `last_seen_event_id` is not newer than the events you expect.

### Tests fail with `python` not found
Use `/usr/bin/python3` or `python3`; this host does not guarantee plain `python` on PATH.
