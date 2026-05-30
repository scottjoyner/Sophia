# Sophia Neo4j Memory Contract

## Position

Neo4j is Sophia's brain and the durable memory source of truth.

SQLite is allowed only as local operational state: cache, outbox, idempotency, task progress, and temporary recovery journal. It must not become an alternate memory database.

Filesystem storage is allowed for large binary artifacts such as raw audio/video files. Neo4j stores the identity, relationships, metadata, transcript text, provenance, and artifact paths.

## Storage Responsibilities

| Layer | Role | Examples | Durable memory? |
| --- | --- | --- | --- |
| Neo4j | Brain / memory graph | SophiaCapture, Transcript, Meeting, MeetingSegment, Speaker, Device, VoiceIdentity, Voiceprint, actions, approvals, provenance | Yes |
| SQLite | Runtime cache / local outbox | trusted sessions, pending graph writes, task progress, retry metadata, idempotency keys | No |
| Filesystem | Artifact blob store | WAV/WEBM/MP4 files, exported clips, manifests | Artifact only |

## Current Capture Memory Shape

The existing `save_capture_to_neo4j` path is the correct durable memory model:

- `MERGE (speaker:Speaker {user_id})`
- `MERGE (audio:Audio {path})`
- `CREATE (capture:SophiaCapture {...})`
- `MERGE (speaker)-[:RECORDED]->(audio)`
- `MERGE (audio)-[:CAPTURED_AS]->(capture)`
- optionally `MERGE (device:Device {device_id})`
- optionally `CREATE (transcript:Transcript {text})`
- `CREATE (speaker)-[:SAID]->(transcript)`
- `CREATE (transcript)-[:CAPTURED_IN]->(capture)`

That means capture memory belongs in Neo4j. Local files only hold the audio bytes. SQLite must not hold long-term transcript memory except as a retry payload waiting to be written to Neo4j.

## SQLite Usage Rules

SQLite tables must be categorized as one of:

1. **cache** — safe to delete, recomputed from Neo4j or runtime.
2. **outbox** — pending work that must be replayed into Neo4j/AssistX.
3. **task_state** — temporary job progress; completed outputs still go to Neo4j.
4. **idempotency** — dedupe keys for preventing duplicate graph writes.

If deleting a SQLite file would delete Sophia memory, the design is wrong.

## Graph Write Policy

All durable memory writes should follow this policy:

1. Write to Neo4j immediately when configured and reachable.
2. If Neo4j write succeeds, mark result as `graph_saved=true`.
3. If Neo4j write fails but local artifact was saved, enqueue a local outbox event with an idempotency key.
4. UI/API response should report `graph_pending=true`, not silently call the capture complete.
5. A retry worker should replay pending outbox items to Neo4j.
6. When replay succeeds, mark the outbox row `succeeded`; do not keep it as memory.

## User-Facing Status Language

Use these labels in UI/API payloads:

- `Saved to Neo4j memory`
- `Saved locally, pending graph sync`
- `Graph unavailable`
- `Graph sync failed; retry queued`
- `Graph sync complete`

Avoid language implying SQLite is memory.

## Auth Session Policy

Short-lived trusted auth sessions may use SQLite because they are runtime access state, not memory.

Accepted voice verification should still publish a Neo4j event when available, because authentication events are useful historical/provenance memory. The active trust window may live in SQLite and expire.

## Meeting Policy

Meeting processing state may use SQLite while a job is running. Completed meeting memory must live in Neo4j:

- `Meeting`
- `MeetingSegment`
- speakers / identities
- transcript
- summary
- action items
- linked devices/sessions/source files

If Neo4j is unavailable after processing, completed meeting writes should enter the graph outbox and show as pending.

## Implementation Direction

P0 next step:

- Add `GraphOutbox` as a local SQLite replay queue.
- Enqueue failed capture graph writes using `kind='capture'` and idempotency key `capture:<capture_id>`.
- Add `/graph/outbox/status` for operator visibility.
- Add a retry command or endpoint that replays queued writes into Neo4j.

P1 next step:

- Move meeting completion writes to the same outbox path.
- Add startup reconciliation so the service announces pending memory writes on boot.
- Add UI badge: `memory sync: ok/pending/degraded`.

## Design Guardrail

Neo4j knows what Sophia remembers.

SQLite only helps Sophia survive the moments before Neo4j can be updated.
