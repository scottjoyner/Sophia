#!/usr/bin/env python3
"""Patch Sophia's inline FastAPI capture UI with session-aware auth refresh.

The current `voice-agent/src/voice_agent/server/app.py` keeps the entire capture UI as
an inline HTML/CSS/JS string.  This script applies deterministic string
replacements to that file so the runtime UI can:

- preserve a short-lived trusted auth session in localStorage,
- avoid calling `/auth/verify` on every automatic capture stop when the user is
  already trusted,
- keep the top auth/status pills refreshed,
- make manual Verify remain a forced fresh check,
- add an explicit "Forget session" control, and
- show whether durable memory writes are synced to Neo4j or pending in the
  local graph outbox.

Run from repository root:

    python voice-agent/scripts/patch_ui_session_refresh.py

The script is intentionally idempotent.  It is multi-phase: if the auth-session
patch was already applied by an older version, this script can still apply the
newer memory-sync UI patch.
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = Path("voice-agent/src/voice_agent/server/app.py")
AUTH_PATCH_MARKER = "sophia_auth_session_v1"
MEMORY_SYNC_PATCH_MARKER = "memorySync"


class PatchError(RuntimeError):
    """Raised when the expected app.py snippets are not present."""


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise PatchError(f"Expected exactly one match for {label!r}, found {count}.")
    return content.replace(old, new, 1)


def replace_all_existing(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count < 1:
        raise PatchError(f"Expected at least one match for {label!r}, found 0.")
    return content.replace(old, new)


def patch_auth_session_ui(content: str) -> str:
    if AUTH_PATCH_MARKER in content:
        return content

    content = replace_once(
        content,
        """  let cachedContext = null;\n  let lastAuthResult = null, lastMeetingResult = null;\n""",
        """  let cachedContext = null;\n  let lastAuthResult = null, lastMeetingResult = null;\n\n  const AUTH_CACHE_KEY = 'sophia_auth_session_v1';\n  const AUTH_TTL_MS = 10 * 60 * 1000;\n""",
        "auth cache constants",
    )

    content = replace_once(
        content,
        """  function updateDispatchActions() {\n    dispatchAuthBtn.disabled = !(lastAuthResult && lastAuthResult.accepted);\n    dispatchMeetingBtn.disabled = !lastMeetingResult;\n  }\n\n  function getDeviceId() {\n""",
        """  function updateDispatchActions() {\n    dispatchAuthBtn.disabled = !(lastAuthResult && lastAuthResult.accepted);\n    dispatchMeetingBtn.disabled = !lastMeetingResult;\n  }\n\n  function getAuthSession() {\n    try {\n      const data = JSON.parse(localStorage.getItem(AUTH_CACHE_KEY) || 'null');\n      if (!data || !data.accepted || !data.expires_at || Date.now() > data.expires_at) {\n        return null;\n      }\n      if (data.user_id !== (userId.value || 'default')) return null;\n      if (data.session_id !== (sessionId.value || 'mobile')) return null;\n      return data;\n    } catch {\n      return null;\n    }\n  }\n\n  function setAuthSession(data) {\n    if (!data || !data.accepted) return;\n    const session = {\n      accepted: true,\n      score: Number(data.score || 0),\n      userId: userId.value || 'default',\n      user_id: userId.value || 'default',\n      session_id: sessionId.value || 'mobile',\n      device_id: data.device_id || '',\n      voiceprint_version_id: data.voiceprint_version_id || '',\n      voiceprint_group_key: data.voiceprint_group_key || '',\n      voiceprint_scope: data.voiceprint_scope || '',\n      match_source: data.match_source || '',\n      fallback_used: Boolean(data.fallback_used),\n      verified_at: Date.now(),\n      expires_at: Date.now() + AUTH_TTL_MS,\n    };\n    localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify(session));\n  }\n\n  function clearAuthSession() {\n    localStorage.removeItem(AUTH_CACHE_KEY);\n  }\n\n  function hydrateAuthFromCache() {\n    const session = getAuthSession();\n    if (!session) {\n      lastScore = null;\n      lastAccepted = null;\n      lastAuthResult = null;\n      setAuthPill('idle', 'auth needed');\n      updateDispatchActions();\n      return false;\n    }\n    lastScore = session.score;\n    lastAccepted = true;\n    lastAuthResult = session;\n    renderAuthSession(session, 'trusted');\n    updateDispatchActions();\n    return true;\n  }\n\n  function getDeviceId() {\n""",
        "auth cache helpers",
    )

    content = replace_once(
        content,
        """  function showAuthResult(score, accepted, deviceLabel) {\n    authResult.classList.remove('hidden');\n    authScore.textContent = (score * 100).toFixed(0) + '%';\n    authAccepted.textContent = accepted ? 'ACCEPTED' : 'REJECTED';\n    authAccepted.className = accepted ? 'auth-accepted pass' : 'auth-accepted fail';\n    const devEl = document.getElementById('authDevice');\n    if (devEl) {\n      devEl.textContent = deviceLabel ? 'via ' + deviceLabel : '';\n      devEl.style.display = deviceLabel ? '' : 'none';\n    }\n  }\n\n  function renderAuthCandidates(data) {\n""",
        """  function showAuthResult(score, accepted, deviceLabel) {\n    authResult.classList.remove('hidden');\n    authScore.textContent = (score * 100).toFixed(0) + '%';\n    authScore.className = accepted ? 'auth-score pass' : 'auth-score fail';\n    authAccepted.textContent = accepted ? 'ACCEPTED' : 'REJECTED';\n    authAccepted.className = accepted ? 'auth-accepted pass' : 'auth-accepted fail';\n    const devEl = document.getElementById('authDevice');\n    if (devEl) {\n      devEl.textContent = deviceLabel ? 'via ' + deviceLabel : '';\n      devEl.style.display = deviceLabel ? '' : 'none';\n    }\n  }\n\n  function ensureAuthSessionControls() {\n    if (document.getElementById('forgetAuthSessionBtn')) return;\n    const row = document.createElement('div');\n    row.className = 'btn-group';\n    row.style.marginTop = '8px';\n    row.innerHTML = '<button id="forgetAuthSessionBtn" class="secondary" style="flex:0;padding:6px 10px;min-height:auto;font-size:11px;">Forget trusted session</button>';\n    authResult.insertAdjacentElement('afterend', row);\n    document.getElementById('forgetAuthSessionBtn').onclick = () => {\n      clearAuthSession();\n      lastScore = null;\n      lastAccepted = null;\n      lastAuthResult = null;\n      authResult.classList.add('hidden');\n      setAuthPill('idle', 'auth needed');\n      saveStatus.textContent = 'Trusted session cleared. Tap Verify to re-check voice.';\n      updateDispatchActions();\n    };\n  }\n\n  function renderAuthSession(data, source = 'live') {\n    const accepted = Boolean(data && data.accepted);\n    const score = Number((data && data.score) || 0);\n    const deviceLabel = data && data.device_id && data.device_id !== 'default' ? ' [' + data.device_id + ']' : '';\n    showAuthResult(score, accepted, deviceLabel);\n    setAuthPill(accepted ? 'pass' : 'fail', accepted ? 'verified ' + (score * 100).toFixed(0) + '%' : 'not verified');\n    authStatus.title = accepted\n      ? 'Verified ' + (source === 'trusted' ? 'from trusted session cache' : 'from live voice check')\n      : 'Voice not verified';\n    ensureAuthSessionControls();\n  }\n\n  function renderAuthCandidates(data) {\n""",
        "auth result renderer",
    )

    content = replace_once(
        content,
        """  async function verifyVoice(mode = 'manual') {\n    const audioSrc = selectedFile || blob;\n    if (!audioSrc) { return; }\n""",
        """  async function verifyVoice(mode = 'manual') {\n    const audioSrc = selectedFile || blob;\n    if (!audioSrc) { return; }\n    if (mode === 'auto') {\n      const trusted = getAuthSession();\n      if (trusted) {\n        hydrateAuthFromCache();\n        saveStatus.textContent = 'Using trusted voice session. Tap Verify to force a fresh voice check.';\n        return trusted;\n      }\n    }\n""",
        "auto verify cache gate",
    )

    content = replace_once(
        content,
        """      const matchedDevice = data.device_id && data.device_id !== 'default' ? ' [' + data.device_id + ']' : '';\n      showAuthResult(data.score, data.accepted, matchedDevice);\n      renderAuthCandidates(data);\n""",
        """      const matchedDevice = data.device_id && data.device_id !== 'default' ? ' [' + data.device_id + ']' : '';\n      if (data.accepted) {\n        setAuthSession(data);\n      } else if (mode === 'manual') {\n        clearAuthSession();\n      }\n      renderAuthSession(data, 'live');\n      renderAuthCandidates(data);\n""",
        "verify success renderer",
    )

    content = replace_all_existing(
        content,
        """    lastScore = null;\n    lastAccepted = null;\n    lastAuthResult = null;\n    updateDispatchActions();\n""",
        """    hydrateAuthFromCache();\n    updateDispatchActions();\n""",
        "auth reset hydration blocks",
    )

    content = replace_once(
        content,
        """  document.getElementById('refreshStatusBtn').onclick = refreshStatus;\n\n  async function testLlm() {\n""",
        """  async function refreshTopStatus() {\n    await Promise.allSettled([refreshGraph(), refreshStatus()]);\n    hydrateAuthFromCache();\n  }\n  document.getElementById('refreshStatusBtn').onclick = refreshTopStatus;\n\n  async function testLlm() {\n""",
        "top status refresh helper",
    )

    content = replace_once(
        content,
        """  loadSpeakers();\n  refreshGraph();\n  updateActionButtons();\n  updateDispatchActions();\n""",
        """  loadSpeakers();\n  hydrateAuthFromCache();\n  refreshTopStatus();\n  setInterval(refreshTopStatus, 15000);\n  window.addEventListener('focus', refreshTopStatus);\n  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshTopStatus(); });\n  updateActionButtons();\n  updateDispatchActions();\n""",
        "startup status refresh",
    )

    return content


def patch_memory_sync_ui(content: str) -> str:
    if MEMORY_SYNC_PATCH_MARKER in content:
        return content

    content = replace_once(
        content,
        """      <div id="graph" class="pill graph">graph ...</div>\n      <div id="authStatus" class="pill auth idle">voice: idle</div>\n""",
        """      <div id="graph" class="pill graph">graph ...</div>\n      <div id="memorySync" class="pill">memory sync ...</div>\n      <div id="authStatus" class="pill auth idle">voice: idle</div>\n""",
        "memory sync header pill",
    )

    content = replace_once(
        content,
        """  async function refreshEventLog() {\n""",
        """  function setMemorySyncPill(state, text) {\n    const el = document.getElementById('memorySync');\n    if (!el) return;\n    el.textContent = text;\n    el.style.borderColor = state === 'ok' ? '#34d399' : (state === 'pending' ? '#fbbf24' : '#fb7185');\n    el.style.color = state === 'ok' ? '#6ee7b7' : (state === 'pending' ? '#fcd34d' : '#fda4af');\n  }\n\n  async function refreshMemorySync() {\n    try {\n      const res = await fetch('/graph/outbox/status');\n      const data = await res.json();\n      if (!res.ok || data.ok === false) throw new Error(data.detail || 'memory sync unavailable');\n      const counts = data.counts || {};\n      const pending = (counts.pending || 0) + (counts.retry || 0);\n      const failed = counts.failed || 0;\n      if (pending > 0) setMemorySyncPill('pending', 'memory sync: ' + pending + ' pending');\n      else if (failed > 0) setMemorySyncPill('fail', 'memory sync: ' + failed + ' failed');\n      else setMemorySyncPill('ok', 'memory sync: Neo4j');\n    } catch {\n      setMemorySyncPill('fail', 'memory sync: unknown');\n    }\n  }\n\n  async function refreshEventLog() {\n""",
        "memory sync refresh function",
    )

    content = replace_once(
        content,
        """    await Promise.allSettled([refreshGraph(), refreshStatus()]);\n""",
        """    await Promise.allSettled([refreshGraph(), refreshStatus(), refreshMemorySync()]);\n""",
        "memory sync top refresh",
    )

    content = replace_once(
        content,
        """    saveStatus.textContent = data.graph_saved ? 'Saved to sidecar and Neo4j.' : 'Saved locally; graph not written.';\n""",
        """    if (data.graph_saved) saveStatus.textContent = 'Saved to sidecar and Neo4j memory.';\n    else if (data.graph_pending) saveStatus.textContent = 'Saved locally; Neo4j memory sync pending.';\n    else saveStatus.textContent = 'Saved locally; graph not written.';\n    await refreshMemorySync();\n""",
        "capture save memory sync message",
    )

    return content


def patch_content(content: str) -> str:
    content = patch_auth_session_ui(content)
    content = patch_memory_sync_ui(content)
    return content


def main() -> int:
    if not APP_PATH.exists():
        raise SystemExit(f"Could not find {APP_PATH}; run from the repository root.")
    original = APP_PATH.read_text(encoding="utf-8")
    patched = patch_content(original)
    if patched == original:
        print("Sophia UI session refresh patch already applied.")
        return 0
    APP_PATH.write_text(patched, encoding="utf-8")
    print(f"Patched {APP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
