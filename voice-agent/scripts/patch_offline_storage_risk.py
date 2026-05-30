#!/usr/bin/env python3
"""Patch Sophia offline queue UI with storage persistence/risk controls.

This follow-up patch runs after `patch_offline_browser_queue.py` and adds:

- local queue size thresholds
- StorageManager.estimate quota visibility
- StorageManager.persist protection request
- a Protect Storage button
- clearer storage-risk status text

Run from repository root:

    python voice-agent/scripts/patch_offline_storage_risk.py
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = Path("voice-agent/src/voice_agent/server/app.py")
OFFLINE_QUEUE_MARKER = "sophia_offline_capture_v1"
PATCH_MARKER = "OFFLINE_WARN_QUEUE_MB"


class PatchError(RuntimeError):
    pass


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise PatchError(f"Expected exactly one match for {label!r}, found {count}.")
    return content.replace(old, new, 1)


def patch_content(content: str) -> str:
    if PATCH_MARKER in content:
        return content
    if OFFLINE_QUEUE_MARKER not in content:
        raise PatchError("Offline browser queue patch must be applied before storage risk patch.")

    content = replace_once(
        content,
        """      <button id="offlineSyncBtn" class="primary" style="flex:0;min-height:auto;padding:8px 12px;font-size:12px;">Retry Sync</button>\n      <button id="offlineExportBtn" class="secondary" style="flex:0;min-height:auto;padding:8px 12px;font-size:12px;">Export Unsynced</button>\n""",
        """      <button id="offlineSyncBtn" class="primary" style="flex:0;min-height:auto;padding:8px 12px;font-size:12px;">Retry Sync</button>\n      <button id="offlineProtectStorageBtn" class="secondary" style="flex:0;min-height:auto;padding:8px 12px;font-size:12px;">Protect Storage</button>\n      <button id="offlineExportBtn" class="secondary" style="flex:0;min-height:auto;padding:8px 12px;font-size:12px;">Export Unsynced</button>\n""",
        "protect storage button",
    )

    content = replace_once(
        content,
        """  const OFFLINE_SYNC_INTERVAL_MS = 30000;\n  let offlineDbPromise = null;\n""",
        """  const OFFLINE_SYNC_INTERVAL_MS = 30000;\n  const OFFLINE_WARN_QUEUE_MB = 350;\n  const OFFLINE_MAX_QUEUE_MB = 500;\n  let offlineStoragePersisted = false;\n  let offlineDbPromise = null;\n""",
        "offline storage thresholds",
    )

    content = replace_once(
        content,
        """  async function estimateOfflineStorage(records) {\n    const queueBytes = records.reduce((n, r) => n + Number(r.size_bytes || 0), 0);\n    let quota = null, usage = null;\n    try {\n      if (navigator.storage && navigator.storage.estimate) {\n        const est = await navigator.storage.estimate();\n        quota = est.quota || null;\n        usage = est.usage || null;\n      }\n    } catch {}\n    return { queueBytes, quota, usage };\n  }\n\n""",
        """  async function requestOfflineStoragePersistence() {\n    try {\n      if (!(navigator.storage && navigator.storage.persist)) return false;\n      offlineStoragePersisted = await navigator.storage.persist();\n      await renderOfflineQueue();\n      return offlineStoragePersisted;\n    } catch {\n      return false;\n    }\n  }\n\n  async function estimateOfflineStorage(records) {\n    const queueBytes = records.reduce((n, r) => n + Number(r.size_bytes || 0), 0);\n    let quota = null, usage = null, persisted = offlineStoragePersisted;\n    try {\n      if (navigator.storage && navigator.storage.persisted) {\n        persisted = await navigator.storage.persisted();\n        offlineStoragePersisted = Boolean(persisted);\n      }\n      if (navigator.storage && navigator.storage.estimate) {\n        const est = await navigator.storage.estimate();\n        quota = est.quota || null;\n        usage = est.usage || null;\n      }\n    } catch {}\n    return { queueBytes, quota, usage, persisted: Boolean(persisted) };\n  }\n\n""",
        "storage persistence estimate function",
    )

    content = replace_once(
        content,
        """      if (!records.length) setOfflineQueuePill('ok', 'offline queue: empty');\n      else if (uploading.length) setOfflineQueuePill('pending', 'offline queue: syncing ' + uploading.length);\n      else if (failed.length) setOfflineQueuePill('fail', 'offline queue: upload failed');\n      else if (unsynced.length) setOfflineQueuePill('pending', 'offline queue: ' + unsynced.length + ' local');\n      else setOfflineQueuePill('ok', 'offline queue: synced');\n      status.textContent = records.length\n        ? (unsynced.length + ' unsynced / ' + records.length + ' total, ' + humanBytes(storage.queueBytes) + ' stored locally')\n        : 'No local offline recordings.';\n""",
        """      const queueMb = storage.queueBytes / (1024 * 1024);\n      const nearQuota = storage.quota && storage.usage && ((storage.quota - storage.usage) < Math.max(storage.queueBytes, 50 * 1024 * 1024));\n      const storageRisk = queueMb >= OFFLINE_WARN_QUEUE_MB || nearQuota;\n      if (storageRisk) setOfflineQueuePill('fail', 'offline queue: storage risk');\n      else if (!records.length) setOfflineQueuePill('ok', 'offline queue: empty');\n      else if (uploading.length) setOfflineQueuePill('pending', 'offline queue: syncing ' + uploading.length);\n      else if (failed.length) setOfflineQueuePill('fail', 'offline queue: upload failed');\n      else if (unsynced.length) setOfflineQueuePill('pending', 'offline queue: ' + unsynced.length + ' local');\n      else setOfflineQueuePill('ok', 'offline queue: synced');\n      const quotaText = storage.quota ? (' / quota ' + humanBytes(storage.quota)) : '';\n      const persistedText = storage.persisted ? ' · protected storage' : ' · storage not protected';\n      const riskText = storageRisk ? ' · STORAGE RISK: sync/export/delete soon' : '';\n      status.textContent = records.length\n        ? (unsynced.length + ' unsynced / ' + records.length + ' total, ' + humanBytes(storage.queueBytes) + ' stored locally' + quotaText + persistedText + riskText)\n        : ('No local offline recordings.' + persistedText);\n""",
        "offline queue storage risk render",
    )

    content = replace_once(
        content,
        """    const retry = document.getElementById('offlineSyncBtn');\n    const del = document.getElementById('offlineDeleteSyncedBtn');\n    const exp = document.getElementById('offlineExportBtn');\n    if (retry) retry.onclick = () => syncOfflineQueue({ force: true });\n""",
        """    const retry = document.getElementById('offlineSyncBtn');\n    const protect = document.getElementById('offlineProtectStorageBtn');\n    const del = document.getElementById('offlineDeleteSyncedBtn');\n    const exp = document.getElementById('offlineExportBtn');\n    if (retry) retry.onclick = () => syncOfflineQueue({ force: true });\n    if (protect) protect.onclick = async () => {\n      const ok = await requestOfflineStoragePersistence();\n      saveStatus.textContent = ok ? 'Browser granted protected storage for offline recordings.' : 'Browser did not grant protected storage. Export important unsynced recordings.';\n    };\n""",
        "offline protect storage handler",
    )

    return content


def main() -> int:
    if not APP_PATH.exists():
        raise SystemExit(f"Could not find {APP_PATH}; run from the repository root.")
    original = APP_PATH.read_text(encoding="utf-8")
    patched = patch_content(original)
    if patched == original:
        print("Sophia offline storage risk patch already applied.")
        return 0
    APP_PATH.write_text(patched, encoding="utf-8")
    print(f"Patched {APP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
