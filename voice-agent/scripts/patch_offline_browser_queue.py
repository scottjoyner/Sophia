#!/usr/bin/env python3
"""Patch Sophia's inline UI with an offline-first browser capture queue.

This injects a P0 browser-side IndexedDB queue for recordings captured while the
phone is offline or the Sophia service is unreachable.  The queue is temporary
browser-side operational state; Neo4j remains Sophia's durable memory.

Run from repository root:

    python voice-agent/scripts/patch_offline_browser_queue.py
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = Path("voice-agent/src/voice_agent/server/app.py")
PATCH_MARKER = "sophia_offline_capture_v1"
CLIENT_ID_MARKER = "client_capture_id: str = Form"


class PatchError(RuntimeError):
    pass


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise PatchError(f"Expected exactly one match for {label!r}, found {count}.")
    return content.replace(old, new, 1)


def offline_queue_panel_html() -> str:
    return """

  <section id="offlineQueueSection">
    <h2>&#x1F4F1; Offline Queue <span class="sub">stored on this device until synced</span></h2>
    <div class="hint">Recordings saved here are local-only until uploaded to Sophia and written to Neo4j memory. Export important clips before deleting them.</div>
    <div id="offlineQueueStatus" class="status">Checking offline queue...</div>
    <div class="btn-group" style="margin-top:8px;">
      <button id="offlineSyncBtn" class="primary" style="flex:0;min-height:auto;padding:8px 12px;font-size:12px;">Retry Sync</button>
      <button id="offlineExportBtn" class="secondary" style="flex:0;min-height:auto;padding:8px 12px;font-size:12px;">Export Unsynced</button>
      <button id="offlineDeleteSyncedBtn" class="secondary" style="flex:0;min-height:auto;padding:8px 12px;font-size:12px;">Delete Synced</button>
    </div>
    <div id="offlineQueueList" style="margin-top:10px;display:grid;gap:8px;"></div>
  </section>
"""


def offline_queue_js() -> str:
    return r'''  const OFFLINE_DB_NAME = 'sophia_offline_capture_v1';
  const OFFLINE_DB_VERSION = 1;
  const OFFLINE_STORE = 'recordings';
  const OFFLINE_SYNC_INTERVAL_MS = 30000;
  let offlineDbPromise = null;
  let offlineSyncRunning = false;

  function offlineId() {
    return (crypto.randomUUID && crypto.randomUUID()) || ('offline-' + Date.now() + '-' + Math.random().toString(16).slice(2));
  }

  function openOfflineDb() {
    if (offlineDbPromise) return offlineDbPromise;
    offlineDbPromise = new Promise((resolve, reject) => {
      if (!window.indexedDB) return reject(new Error('IndexedDB is not available in this browser'));
      const req = indexedDB.open(OFFLINE_DB_NAME, OFFLINE_DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        const store = db.objectStoreNames.contains(OFFLINE_STORE)
          ? req.transaction.objectStore(OFFLINE_STORE)
          : db.createObjectStore(OFFLINE_STORE, { keyPath: 'client_capture_id' });
        ['status', 'created_at_ms', 'next_retry_at_ms', 'graph_saved', 'graph_pending'].forEach(name => {
          if (!store.indexNames.contains(name)) store.createIndex(name, name, { unique: false });
        });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('Failed to open offline capture queue'));
    });
    return offlineDbPromise;
  }

  async function offlineStore(mode = 'readonly') {
    const db = await openOfflineDb();
    return db.transaction(OFFLINE_STORE, mode).objectStore(OFFLINE_STORE);
  }

  function reqToPromise(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('IndexedDB request failed'));
    });
  }

  async function putOfflineRecording(record) {
    const store = await offlineStore('readwrite');
    return reqToPromise(store.put(record));
  }

  async function listOfflineRecordings() {
    const store = await offlineStore('readonly');
    const rows = await reqToPromise(store.getAll());
    return rows.sort((a, b) => (b.created_at_ms || 0) - (a.created_at_ms || 0));
  }

  async function getOfflineRecording(id) {
    const store = await offlineStore('readonly');
    return reqToPromise(store.get(id));
  }

  async function patchOfflineRecording(id, patch) {
    const existing = await getOfflineRecording(id);
    if (!existing) return null;
    const updated = { ...existing, ...patch, updated_at_ms: Date.now() };
    await putOfflineRecording(updated);
    return updated;
  }

  async function deleteOfflineRecording(id) {
    const store = await offlineStore('readwrite');
    return reqToPromise(store.delete(id));
  }

  function statusIsUnsynced(status) {
    return !['synced_to_neo4j', 'user_deleted'].includes(status);
  }

  async function canReachSophia() {
    try {
      const res = await fetch('/healthz', { cache: 'no-store' });
      return res.ok;
    } catch {
      return false;
    }
  }

  async function estimateOfflineStorage(records) {
    const queueBytes = records.reduce((n, r) => n + Number(r.size_bytes || 0), 0);
    let quota = null, usage = null;
    try {
      if (navigator.storage && navigator.storage.estimate) {
        const est = await navigator.storage.estimate();
        quota = est.quota || null;
        usage = est.usage || null;
      }
    } catch {}
    return { queueBytes, quota, usage };
  }

  function humanBytes(n) {
    if (!n) return '0 B';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function setOfflineQueuePill(state, text) {
    const el = document.getElementById('offlineQueue');
    if (!el) return;
    el.textContent = text;
    el.style.borderColor = state === 'ok' ? '#34d399' : (state === 'pending' ? '#fbbf24' : '#fb7185');
    el.style.color = state === 'ok' ? '#6ee7b7' : (state === 'pending' ? '#fcd34d' : '#fda4af');
  }

  async function renderOfflineQueue() {
    const pill = document.getElementById('offlineQueue');
    const list = document.getElementById('offlineQueueList');
    const status = document.getElementById('offlineQueueStatus');
    if (!pill || !list || !status) return;
    try {
      const records = await listOfflineRecordings();
      const unsynced = records.filter(r => statusIsUnsynced(r.status));
      const failed = records.filter(r => String(r.status || '').includes('failed'));
      const uploading = records.filter(r => r.status === 'uploading');
      const storage = await estimateOfflineStorage(records);
      if (!records.length) setOfflineQueuePill('ok', 'offline queue: empty');
      else if (uploading.length) setOfflineQueuePill('pending', 'offline queue: syncing ' + uploading.length);
      else if (failed.length) setOfflineQueuePill('fail', 'offline queue: upload failed');
      else if (unsynced.length) setOfflineQueuePill('pending', 'offline queue: ' + unsynced.length + ' local');
      else setOfflineQueuePill('ok', 'offline queue: synced');
      status.textContent = records.length
        ? (unsynced.length + ' unsynced / ' + records.length + ' total, ' + humanBytes(storage.queueBytes) + ' stored locally')
        : 'No local offline recordings.';
      list.innerHTML = records.slice(0, 20).map(r => {
        const created = r.created_at_ms ? new Date(r.created_at_ms).toLocaleString() : '?';
        const graph = r.graph_saved ? 'Neo4j saved' : (r.graph_pending ? 'server graph pending' : 'local only');
        return '<div class="meeting-segment" style="font-size:12px;">'
          + '<div class="speaker-label">' + (r.status || 'unknown') + ' <span class="seg-time">' + created + '</span></div>'
          + '<div>' + humanBytes(r.size_bytes || 0) + ' · ' + graph + ' · retries ' + (r.retry_count || 0) + '</div>'
          + (r.last_error ? '<div class="de-error">' + String(r.last_error).slice(0, 160) + '</div>' : '')
          + '</div>';
      }).join('') || '<div style="color:#64748b;font-size:12px;">Queue is empty.</div>';
    } catch (err) {
      setOfflineQueuePill('fail', 'offline queue: unavailable');
      status.textContent = 'Offline queue unavailable: ' + err.message;
    }
  }

  async function buildOfflineCaptureRecord() {
    const audioBlob = selectedFile || blob;
    if (!audioBlob && !(transcriptEl.value || '').trim()) throw new Error('Record, upload audio, or enter transcript first.');
    const context = await collectContext();
    const id = offlineId();
    const now = Date.now();
    const filename = selectedFile ? (selectedFile.name || (id + '.webm')) : (id + '.webm');
    return {
      client_capture_id: id,
      created_at_ms: now,
      updated_at_ms: now,
      status: 'upload_pending',
      blob: audioBlob || new Blob([], { type: 'text/plain' }),
      blob_name: filename,
      blob_type: audioBlob ? (audioBlob.type || 'audio/webm') : 'text/plain',
      size_bytes: audioBlob ? audioBlob.size : 0,
      duration_ms: startedAt ? Date.now() - startedAt : 0,
      transcript: transcriptEl.value || '',
      user_id: userId.value || 'default',
      session_id: sessionId.value || 'mobile',
      device_id: context.device_id || '',
      device_fingerprint: context.device_fingerprint || '',
      location_lat: context.location_lat,
      location_lng: context.location_lng,
      location_accuracy_m: context.location_accuracy_m,
      activity_context: activityContext.value || '',
      client_context: context,
      retry_count: 0,
      next_retry_at_ms: now,
      last_error: '',
      server_capture_id: null,
      server_response: null,
      graph_saved: false,
      graph_pending: false,
      graph_outbox_id: null,
      local_delete_allowed: false,
    };
  }

  function captureRecordToForm(record) {
    const form = new FormData();
    if (record.blob && record.blob.size) form.append('audio', record.blob, record.blob_name || (record.client_capture_id + '.webm'));
    form.append('client_capture_id', record.client_capture_id);
    form.append('transcript', record.transcript || '');
    form.append('user_id', record.user_id || 'default');
    form.append('session_id', record.session_id || 'mobile');
    form.append('duration_ms', String(record.duration_ms || 0));
    form.append('device_id', record.device_id || '');
    form.append('device_fingerprint', record.device_fingerprint || '');
    form.append('client_context', JSON.stringify(record.client_context || {}));
    form.append('activity_context', record.activity_context || '');
    if (record.location_lat != null) form.append('location_lat', String(record.location_lat));
    if (record.location_lng != null) form.append('location_lng', String(record.location_lng));
    if (record.location_accuracy_m != null) form.append('location_accuracy_m', String(record.location_accuracy_m));
    return form;
  }

  function incrementCaptureCountOnce(record) {
    if (record.server_capture_id) return;
    const count = parseInt(localStorage.getItem('sophia_capture_count') || '0') + 1;
    localStorage.setItem('sophia_capture_count', String(count));
    captureCount.textContent = count + ' capture' + (count !== 1 ? 's' : '') + ' saved';
  }

  async function syncOfflineRecord(record, { force = false } = {}) {
    if (!force && record.next_retry_at_ms && record.next_retry_at_ms > Date.now()) return record;
    if (!(await canReachSophia())) throw new Error('Sophia service is not reachable');
    await patchOfflineRecording(record.client_capture_id, { status: 'uploading', last_error: '' });
    const res = await fetch('/capture', { method: 'POST', body: captureRecordToForm(record) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || 'Upload failed');
    incrementCaptureCountOnce(record);
    const status = data.graph_saved ? 'synced_to_neo4j' : (data.graph_pending ? 'server_graph_pending' : 'uploaded_to_sidecar');
    const updated = await patchOfflineRecording(record.client_capture_id, {
      status,
      server_capture_id: data.capture_id || null,
      server_response: data,
      graph_saved: Boolean(data.graph_saved),
      graph_pending: Boolean(data.graph_pending),
      graph_outbox_id: data.graph_outbox_id || null,
      local_delete_allowed: Boolean(data.graph_saved),
      last_error: data.graph_error || '',
    });
    latest.textContent = JSON.stringify(data, null, 2);
    return updated;
  }

  async function syncOfflineQueue({ force = false } = {}) {
    if (offlineSyncRunning) return;
    offlineSyncRunning = true;
    try {
      const records = await listOfflineRecordings();
      const due = records.filter(r => statusIsUnsynced(r.status) && (force || !r.next_retry_at_ms || r.next_retry_at_ms <= Date.now()));
      for (const record of due) {
        try {
          await syncOfflineRecord(record, { force });
        } catch (err) {
          const retry = Number(record.retry_count || 0) + 1;
          const delay = Math.min(30 * 60 * 1000, 15000 * Math.pow(2, Math.min(6, retry - 1)));
          await patchOfflineRecording(record.client_capture_id, {
            status: 'upload_failed',
            retry_count: retry,
            next_retry_at_ms: Date.now() + delay,
            last_error: err.message || String(err),
          });
        }
      }
    } finally {
      offlineSyncRunning = false;
      await renderOfflineQueue();
    }
  }

  async function deleteSyncedOfflineRecordings() {
    const records = await listOfflineRecordings();
    for (const r of records) {
      if (r.status === 'synced_to_neo4j' || r.local_delete_allowed) await deleteOfflineRecording(r.client_capture_id);
    }
    await renderOfflineQueue();
  }

  async function exportUnsyncedOfflineRecordings() {
    const records = (await listOfflineRecordings()).filter(r => statusIsUnsynced(r.status));
    for (const r of records) {
      if (!r.blob || !r.blob.size) continue;
      const a = document.createElement('a');
      a.href = URL.createObjectURL(r.blob);
      a.download = r.blob_name || (r.client_capture_id + '.webm');
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    }
  }

  async function saveOfflineFirst() {
    const record = await buildOfflineCaptureRecord();
    await putOfflineRecording(record);
    await renderOfflineQueue();
    saveStatus.textContent = 'Saved locally on this device. Trying to sync to Sophia...';
    try {
      const updated = await syncOfflineRecord(record, { force: true });
      if (updated && updated.graph_saved) saveStatus.textContent = 'Saved to sidecar and Neo4j memory.';
      else if (updated && updated.graph_pending) saveStatus.textContent = 'Saved to sidecar; Neo4j memory sync pending.';
      else saveStatus.textContent = 'Saved to sidecar; graph status unknown.';
    } catch (err) {
      await patchOfflineRecording(record.client_capture_id, {
        status: 'upload_failed',
        retry_count: 1,
        next_retry_at_ms: Date.now() + 15000,
        last_error: err.message || String(err),
      });
      saveStatus.textContent = 'Saved locally. Upload will retry when Sophia is reachable.';
    }
    await renderOfflineQueue();
  }

  function installOfflineQueueHandlers() {
    const retry = document.getElementById('offlineSyncBtn');
    const del = document.getElementById('offlineDeleteSyncedBtn');
    const exp = document.getElementById('offlineExportBtn');
    if (retry) retry.onclick = () => syncOfflineQueue({ force: true });
    if (del) del.onclick = deleteSyncedOfflineRecordings;
    if (exp) exp.onclick = exportUnsyncedOfflineRecordings;
    window.addEventListener('online', () => syncOfflineQueue({ force: true }));
    document.addEventListener('visibilitychange', () => { if (!document.hidden) syncOfflineQueue(); });
    setInterval(() => { if (!document.hidden) syncOfflineQueue(); }, OFFLINE_SYNC_INTERVAL_MS);
  }
'''


def patch_header(content: str) -> str:
    if 'id="offlineQueue"' in content:
        return content
    with_memory = """      <div id=\"graph\" class=\"pill graph\">graph ...</div>\n      <div id=\"memorySync\" class=\"pill\">memory sync ...</div>\n      <div id=\"authStatus\" class=\"pill auth idle\">voice: idle</div>\n"""
    if with_memory in content:
        return replace_once(
            content,
            with_memory,
            """      <div id=\"graph\" class=\"pill graph\">graph ...</div>\n      <div id=\"memorySync\" class=\"pill\">memory sync ...</div>\n      <div id=\"offlineQueue\" class=\"pill\">offline queue ...</div>\n      <div id=\"authStatus\" class=\"pill auth idle\">voice: idle</div>\n""",
            "offline queue header pill after memory sync",
        )
    return replace_once(
        content,
        """      <div id=\"graph\" class=\"pill graph\">graph ...</div>\n      <div id=\"authStatus\" class=\"pill auth idle\">voice: idle</div>\n""",
        """      <div id=\"graph\" class=\"pill graph\">graph ...</div>\n      <div id=\"offlineQueue\" class=\"pill\">offline queue ...</div>\n      <div id=\"authStatus\" class=\"pill auth idle\">voice: idle</div>\n""",
        "offline queue header pill",
    )


def patch_ui(content: str) -> str:
    if PATCH_MARKER not in content:
        content = patch_header(content)
        content = replace_once(
            content,
            """    <div class=\"inline-meta\">\n      <span id=\"captureCount\">no captures saved</span>\n    </div>\n  </section>\n\n  <section>\n    <h2>&#x1F510; Admin Voiceprint <span class=\"sub\">reviewed clips only</span></h2>\n""",
            """    <div class=\"inline-meta\">\n      <span id=\"captureCount\">no captures saved</span>\n    </div>\n  </section>\n""" + offline_queue_panel_html() + """\n  <section>\n    <h2>&#x1F510; Admin Voiceprint <span class=\"sub\">reviewed clips only</span></h2>\n""",
            "offline queue panel",
        )
        content = replace_once(
            content,
            """  function updateDispatchActions() {\n    dispatchAuthBtn.disabled = !(lastAuthResult && lastAuthResult.accepted);\n    dispatchMeetingBtn.disabled = !lastMeetingResult;\n  }\n\n  function getDeviceId() {\n""",
            offline_queue_js() + "\n  function updateDispatchActions() {\n    dispatchAuthBtn.disabled = !(lastAuthResult && lastAuthResult.accepted);\n    dispatchMeetingBtn.disabled = !lastMeetingResult;\n  }\n\n  function getDeviceId() {\n",
            "offline queue javascript",
        )
        content = replace_once(
            content,
            """    const form = new FormData();\n    if (selectedFile) form.append('audio', selectedFile, selectedFile.name || 'ios-capture');\n    else if (blob) form.append('audio', blob, 'capture.webm');\n    form.append('transcript', transcriptEl.value || '');\n    form.append('user_id', userId.value || 'default');\n    form.append('session_id', sessionId.value || 'mobile');\n    form.append('duration_ms', String(startedAt ? Date.now() - startedAt : 0));\n    const context = await collectContext();\n    form.append('device_id', context.device_id || '');\n    form.append('device_fingerprint', context.device_fingerprint || '');\n    form.append('client_context', JSON.stringify(context));\n    form.append('activity_context', activityContext.value || '');\n    if (context.location_lat != null) form.append('location_lat', String(context.location_lat));\n    if (context.location_lng != null) form.append('location_lng', String(context.location_lng));\n    if (context.location_accuracy_m != null) form.append('location_accuracy_m', String(context.location_accuracy_m));\n    saveStatus.textContent = 'Saving...';\n    const res = await fetch('/capture', { method: 'POST', body: form });\n    const data = await res.json();\n    latest.textContent = JSON.stringify(data, null, 2);\n    if (!res.ok) throw new Error(data.detail || 'Capture failed');\n    const count = parseInt(localStorage.getItem('sophia_capture_count') || '0') + 1;\n    localStorage.setItem('sophia_capture_count', String(count));\n    captureCount.textContent = count + ' capture' + (count !== 1 ? 's' : '') + ' saved';\n    saveStatus.textContent = data.graph_saved ? 'Saved to sidecar and Neo4j.' : 'Saved locally; graph not written.';\n""",
            """    await saveOfflineFirst();\n""",
            "offline-first save body",
        )
        content = replace_once(
            content,
            """  loadSpeakers();\n  refreshGraph();\n  updateActionButtons();\n  updateDispatchActions();\n""",
            """  loadSpeakers();\n  installOfflineQueueHandlers();\n  renderOfflineQueue();\n  syncOfflineQueue();\n  refreshGraph();\n  updateActionButtons();\n  updateDispatchActions();\n""",
            "offline queue startup",
        )
    return content


def patch_server_client_capture_id(content: str) -> str:
    if CLIENT_ID_MARKER not in content:
        content = replace_once(
            content,
            """        activity_context: str = Form(default=\"\"),\n    ) -> Dict[str, Any]:\n""",
            """        activity_context: str = Form(default=\"\"),\n        client_capture_id: str = Form(default=\"\"),\n    ) -> Dict[str, Any]:\n""",
            "capture client id form field",
        )
        content = replace_once(
            content,
            """        capture_id = uuid.uuid4().hex\n""",
            """        capture_id = uuid.uuid4().hex\n        client_capture_id_clean = client_capture_id.strip()[:128]\n""",
            "capture client id clean",
        )
        content = replace_once(
            content,
            """                    metadata={\"session_id\": session_id, \"bytes\": byte_count},\n""",
            """                    metadata={\"session_id\": session_id, \"bytes\": byte_count, \"client_capture_id\": client_capture_id_clean or None},\n""",
            "capture client id neo4j metadata",
        )
        content = replace_once(
            content,
            """            \"capture_id\": capture_id,\n""",
            """            \"capture_id\": capture_id,\n            \"client_capture_id\": client_capture_id_clean or None,\n""",
            "capture client id response",
        )
    return content


def patch_content(content: str) -> str:
    content = patch_ui(content)
    content = patch_server_client_capture_id(content)
    return content


def main() -> int:
    if not APP_PATH.exists():
        raise SystemExit(f"Could not find {APP_PATH}; run from the repository root.")
    original = APP_PATH.read_text(encoding="utf-8")
    patched = patch_content(original)
    if patched == original:
        print("Sophia offline browser queue patch already applied.")
        return 0
    APP_PATH.write_text(patched, encoding="utf-8")
    print(f"Patched {APP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
