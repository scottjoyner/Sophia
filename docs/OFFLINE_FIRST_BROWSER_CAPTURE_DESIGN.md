# Offline-First Browser Capture Design

## Purpose

Sophia must remain usable when the phone has no service, weak service, or cannot reach the backend/Neo4j. The user should be able to record safely, see exactly what is stored locally, and trust that queued recordings will upload when connectivity returns.

This design extends the current Sophia web UI with an offline-first capture queue.

## Core Rule

Neo4j remains Sophia's durable memory and brain.

The browser is only a temporary offline capture queue.

SQLite on the server remains only runtime/outbox state.

Filesystem storage remains only artifact storage.

## Problem Statement

Current capture flow assumes the browser can reach the Sophia server when saving a recording. If the phone is offline or the service is unreachable, a recorded blob can be lost unless the user manually downloads it or keeps the tab alive.

We need a browser-side queue that stores recordings before upload and retries later.

## Design Goals

1. A user can record when completely offline.
2. A recording is committed to local browser storage before any network upload attempt.
3. The UI clearly shows local-only, uploading, uploaded, failed, and synced-to-Neo4j states.
4. The sync process retries automatically when the app is open and network returns.
5. The user has manual controls to retry, export, or delete queued recordings.
6. The system does not imply that browser storage is durable memory.
7. Risky states are visible and actionable.

## Non-Goals

- Do not use browser storage as Sophia memory.
- Do not require Background Sync to work for correctness.
- Do not rely on the phone keeping a web page alive in the background.
- Do not store long-term transcripts or memory only in the browser.
- Do not silently delete unsynced recordings.

## Browser Platform Strategy

Use IndexedDB as the primary browser-side storage layer because it supports larger structured records and files/blobs better than localStorage. Use localStorage only for tiny UI preferences such as selected mode and toggles.

Use `navigator.onLine`, `online`, and `offline` events only as hints, not proof that the Sophia backend is reachable. Real sync readiness must be confirmed by calling `/healthz` or `/readyz`.

Use Service Worker and Background Sync as progressive enhancement. Background Sync can help on browsers that support it, but it is not universally available. The reliable baseline must be foreground sync while the app is open or visible.

## User Experience Model

Add a new top status pill:

```text
offline queue: empty
offline queue: 2 local
offline queue: syncing 1/2
offline queue: upload failed
offline queue: storage risk
```

Add a new collapsible panel:

```text
Offline Queue
- 2 recordings waiting on this device
- 1 upload failed, tap Retry
- Oldest unsynced: 2026-05-30 14:22
- Estimated storage used: 42 MB / 980 MB available

[Retry Sync] [Export All] [Delete Synced] [Clear Failed]
```

Each queued item should show:

- created time
- duration if available
- size
- status
- retry count
- last error
- whether uploaded to sidecar
- whether server says graph sync is pending
- whether Neo4j memory write succeeded

## Recording State Machine

```text
recording
  -> local_committing
  -> local_saved
  -> upload_pending
  -> uploading
  -> uploaded_to_sidecar
  -> server_graph_pending
  -> synced_to_neo4j
```

Failure states:

```text
local_save_failed
upload_failed
server_rejected
storage_quota_risk
user_deleted
```

Important invariant:

A recording should never be removed from browser IndexedDB until one of these is true:

1. Server confirms `graph_saved=true`, or
2. Server confirms `graph_pending=true` and the user has explicitly enabled "remove after sidecar accepted", or
3. User explicitly deletes or exports it.

Recommended default:

Keep local copy until `graph_saved=true`.

## IndexedDB Schema

Database name:

```text
sophia_offline_capture_v1
```

Object stores:

```text
recordings
sync_events
settings
```

### recordings

Primary key:

```text
client_capture_id
```

Record shape:

```json
{
  "client_capture_id": "uuid",
  "created_at_ms": 1760000000000,
  "updated_at_ms": 1760000000000,
  "status": "upload_pending",
  "blob": "Blob(audio/webm)",
  "blob_type": "audio/webm",
  "size_bytes": 1234567,
  "duration_ms": 12000,
  "user_id": "scott",
  "session_id": "mobile",
  "device_id": "iphone",
  "device_fingerprint": "...",
  "location": {
    "lat": 35.0,
    "lng": -80.0,
    "accuracy_m": 25
  },
  "client_context": {},
  "retry_count": 0,
  "next_retry_at_ms": 1760000010000,
  "last_error": "",
  "server_capture_id": null,
  "server_response": null,
  "graph_saved": false,
  "graph_pending": false,
  "graph_outbox_id": null,
  "local_delete_allowed": false
}
```

Indexes:

```text
status
created_at_ms
next_retry_at_ms
graph_saved
graph_pending
```

### sync_events

Append-only operational log for debugging sync behavior.

```json
{
  "id": "uuid",
  "ts_ms": 1760000000000,
  "client_capture_id": "uuid",
  "event": "upload_failed",
  "detail": "NetworkError when attempting to fetch resource"
}
```

### settings

Small local settings:

```json
{
  "key": "offline_capture_settings",
  "value": {
    "auto_sync": true,
    "delete_after_graph_saved": false,
    "max_local_queue_mb": 500,
    "warn_at_queue_mb": 350,
    "allow_cellular_sync": true
  }
}
```

## Sync Trigger Strategy

Sync attempts should run from multiple triggers:

1. On app load.
2. When the browser fires `online`.
3. When the tab becomes visible.
4. When the user taps `Retry Sync`.
5. On a conservative interval while the page is open, for example every 30 seconds.
6. Via Background Sync when supported.

Before upload, call:

```http
GET /healthz
```

Then preferably:

```http
GET /readyz
```

If the service is unavailable, keep queue status local-only.

## Upload Algorithm

Pseudo-flow:

```js
async function saveRecordingOfflineFirst(blob, metadata) {
  const record = buildRecord(blob, metadata);
  await offlineDb.recordings.put(record);
  renderOfflineQueue();
  trySyncSoon();
}

async function syncDueRecordings() {
  if (!(await canReachSophia())) return;
  const due = await offlineDb.recordings.getDueUploadRecords(Date.now());
  for (const record of due) {
    await syncOne(record);
  }
}

async function syncOne(record) {
  mark(record, 'uploading');
  const form = new FormData();
  form.append('audio', record.blob, record.client_capture_id + '.webm');
  form.append('user_id', record.user_id);
  form.append('session_id', record.session_id);
  form.append('client_capture_id', record.client_capture_id);
  form.append('device_id', record.device_id || '');
  form.append('device_fingerprint', record.device_fingerprint || '');
  form.append('client_context', JSON.stringify(record.client_context || {}));

  const res = await fetch('/capture', { method: 'POST', body: form });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Upload failed');

  updateRecordWithServerResponse(record, data);
  if (data.graph_saved) mark(record, 'synced_to_neo4j');
  else if (data.graph_pending) mark(record, 'server_graph_pending');
  else mark(record, 'uploaded_to_sidecar');
}
```

## Server Contract Additions

The existing `/capture` response already includes graph-related fields after the graph outbox work:

```json
{
  "capture_id": "...",
  "graph_saved": true,
  "graph_pending": false,
  "graph_outbox_id": null,
  "graph_error": null
}
```

Recommended addition:

Accept optional `client_capture_id` in `/capture` and persist it into Neo4j metadata/outbox payload.

This gives idempotency across retry attempts. If the same client recording is uploaded twice, the server can avoid duplicate SophiaCapture memory.

Recommended backend behavior:

```text
client_capture_id present
  -> idempotency key = browser_capture:<client_capture_id>
  -> duplicate upload returns existing server capture status if known
```

## UI Risk Register

| Risk | User-facing mitigation | Technical mitigation |
| --- | --- | --- |
| Browser storage evicted | Show storage risk warning; export option | `navigator.storage.estimate()`, optional `navigator.storage.persist()` |
| Phone closes tab before upload | Recording already stored in IndexedDB | Commit blob before network call |
| Background sync unsupported | Do not rely on it | Foreground sync, online event, visibility event, manual retry |
| Multiple uploads of same recording | Show one queue item | `client_capture_id` idempotency |
| User thinks local queue equals memory | Use wording: local-only / pending memory sync | Neo4j remains source of truth |
| Sensitive audio stored on phone | Warning and explicit delete/export controls | Optional passphrase/WebCrypto phase later |
| Storage fills up | Queue size indicator and max queue cap | Storage estimate and pre-record warnings |
| Auth expired while offline | Save recording anyway; verify/upload later | Do not require auth to preserve local artifact |
| Upload succeeds but Neo4j fails | Show server graph pending | Server graph outbox retry worker |

## Security and Privacy

Offline recordings are sensitive. The UI must make this explicit.

Minimum viable security:

- Do not store auth tokens in queue records.
- Do not store more metadata than needed.
- Allow user to delete local recordings.
- Add visible warning when unsynced audio exists.
- Prefer HTTPS/PWA install context.

P1 security enhancement:

- Optional local encryption using WebCrypto.
- User passphrase or device-bound key.
- Encrypt blob before writing to IndexedDB.
- Tradeoff: if passphrase is lost, local unsynced recordings cannot be recovered.

## Storage Management

Use `navigator.storage.estimate()` when available to show usage/quota. If storage usage crosses warning threshold, disable new long recordings or warn before recording.

Recommended thresholds:

```text
warn_at_queue_mb = 350
max_local_queue_mb = 500
max_single_recording_mb = 50
```

When over warning threshold, show:

```text
Offline storage is getting full. Sync or export recordings before continuing.
```

When over hard threshold:

```text
Recording disabled until you sync, export, or delete local recordings.
```

## P0 Implementation Plan

### P0.1 Offline queue module

Add browser-side JS module inside the current inline UI first, then split into static assets later.

Functions:

```js
openOfflineDb()
putOfflineRecording(record)
listOfflineRecordings()
updateOfflineRecording(id, patch)
deleteOfflineRecording(id)
getDueOfflineRecordings(now)
```

### P0.2 Offline-first save path

Change recording stop/save behavior:

```text
blob captured
  -> save to IndexedDB first
  -> show "Saved locally"
  -> try upload
```

### P0.3 Queue UI

Add:

- offline queue pill
- queue panel
- retry button
- delete synced button
- export all button

### P0.4 Sync loop

Add foreground sync loop:

- on load
- online event
- visibility event
- every 30 seconds while visible
- manual retry

### P0.5 Server idempotency field

Add optional `client_capture_id` to `/capture` form.

Include it in:

- local artifact metadata
- Neo4j metadata
- graph outbox payload
- API response

## P1 Implementation Plan

### P1.1 Service Worker

Add service worker to cache the UI shell and static assets. Once the UI is split out of inline `app.py`, cache:

```text
/
/static/app.js
/static/app.css
/manifest.webmanifest
```

### P1.2 Background Sync enhancement

Where supported:

```js
registration.sync.register('sophia-offline-capture-sync')
```

Fallback remains foreground sync.

### P1.3 Local encryption

Add optional encryption layer for blobs before IndexedDB write.

### P1.4 Better conflict handling

Add server endpoint:

```http
GET /capture/by-client-id/{client_capture_id}
```

The UI can reconcile uncertain uploads after network failures.

## P2 Implementation Plan

### P2.1 Split UI assets

Move inline app into:

```text
voice-agent/src/voice_agent/server/templates/capture.html
voice-agent/src/voice_agent/server/static/app.js
voice-agent/src/voice_agent/server/static/offline_queue.js
voice-agent/src/voice_agent/server/static/app.css
```

### P2.2 PWA install support

Add:

```text
manifest.webmanifest
service-worker.js
icons
```

### P2.3 Dedicated offline diagnostics panel

Show:

- service reachable
- Neo4j status
- graph outbox pending
- browser queue pending
- storage quota estimate
- last sync attempt
- last sync error

## Acceptance Criteria

P0 is complete when:

1. Airplane mode recording creates a durable IndexedDB queue item.
2. Refreshing the page still shows the queued item.
3. Returning online uploads the item automatically while the app is open.
4. If server accepts the capture but Neo4j is unavailable, UI shows `server graph pending`.
5. If Neo4j later syncs, UI can reconcile and mark item synced.
6. User can manually retry sync.
7. User can export unsynced audio before deleting it.
8. User sees storage warnings before the browser queue becomes risky.

## Recommended Next Commit

Implement P0 with a focused patcher:

```text
voice-agent/scripts/patch_offline_browser_queue.py
```

The patcher should inject:

- IndexedDB helpers
- offline queue UI panel
- offline queue status pill
- foreground sync loop
- `client_capture_id` upload field
- tests that assert the patch is idempotent and injects queue primitives

## References

- MDN IndexedDB API: browser-side storage for larger structured data and blobs.
- MDN Background Synchronization API: useful progressive enhancement, but not baseline across all major browsers.
- MDN Navigator.onLine: network status hint, not backend reachability guarantee.
- MDN StorageManager.estimate(): estimate origin storage usage and quota.
