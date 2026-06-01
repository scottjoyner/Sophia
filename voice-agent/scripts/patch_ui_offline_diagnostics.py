#!/usr/bin/env python3
"""Patch Sophia inline UI with an offline diagnostics panel.

Adds a user/operator-visible panel that calls `/diagnostics/offline` and renders
browser/server/Neo4j reliability status in one place.
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = Path("voice-agent/src/voice_agent/server/app.py")
PATCH_MARKER = "offlineDiagnosticsPanel"


class PatchError(RuntimeError):
    pass


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise PatchError(f"Expected exactly one match for {label!r}, found {count}.")
    return content.replace(old, new, 1)


def diagnostics_panel_html() -> str:
    return """

  <section id="offlineDiagnosticsPanel">
    <h2>&#x1F9ED; Reliability Diagnostics <span class="sub">local queue / server retry / Neo4j memory</span></h2>
    <div class="hint">Use this panel when the phone has been offline, Neo4j was unavailable, or a recording appears stuck in pending sync.</div>
    <div id="offlineDiagnosticsStatus" class="status">Diagnostics not loaded.</div>
    <div class="btn-group" style="margin-top:8px;">
      <button id="offlineDiagnosticsRefreshBtn" class="secondary" style="flex:0;min-height:auto;padding:8px 12px;font-size:12px;">Refresh Diagnostics</button>
    </div>
    <pre id="offlineDiagnosticsJson" style="margin-top:10px;max-height:280px;overflow:auto;background:#020617;border:1px solid #1f2937;border-radius:12px;padding:10px;font-size:11px;color:#cbd5e1;"></pre>
  </section>
"""


def diagnostics_js() -> str:
    return r'''  async function refreshOfflineDiagnostics() {
    const status = document.getElementById('offlineDiagnosticsStatus');
    const output = document.getElementById('offlineDiagnosticsJson');
    if (!status || !output) return;
    status.textContent = 'Loading diagnostics...';
    try {
      const res = await fetch('/diagnostics/offline', { cache: 'no-store' });
      const data = await res.json();
      output.textContent = JSON.stringify(data, null, 2);
      const outbox = data.graph_outbox || {};
      const idem = data.capture_idempotency || {};
      const pending = Number(outbox.pending_total || 0);
      const due = Number(outbox.due || 0);
      const active = idem.counts ? Number(idem.counts.active || 0) : 0;
      if (!res.ok || data.ok === false) {
        status.textContent = 'Diagnostics unavailable.';
      } else if (pending > 0) {
        status.textContent = 'Memory sync pending: ' + pending + ' graph writes, ' + due + ' due now. Idempotency cache active: ' + active + '.';
      } else {
        status.textContent = 'Reliability path healthy. Neo4j outbox clear. Idempotency cache active: ' + active + '.';
      }
    } catch (err) {
      status.textContent = 'Diagnostics failed: ' + (err.message || String(err));
      output.textContent = '';
    }
  }

  function installOfflineDiagnosticsHandlers() {
    const btn = document.getElementById('offlineDiagnosticsRefreshBtn');
    if (btn) btn.onclick = refreshOfflineDiagnostics;
  }
'''


def patch_content(content: str) -> str:
    if PATCH_MARKER in content:
        return content
    if "offlineQueueSection" not in content:
        raise PatchError("Offline browser queue patch must be applied before diagnostics UI patch.")

    content = replace_once(
        content,
        """  <section>\n    <h2>&#x1F510; Admin Voiceprint <span class=\"sub\">reviewed clips only</span></h2>\n""",
        diagnostics_panel_html() + "\n  <section>\n    <h2>&#x1F510; Admin Voiceprint <span class=\"sub\">reviewed clips only</span></h2>\n",
        "offline diagnostics panel",
    )

    content = replace_once(
        content,
        """  function updateDispatchActions() {\n""",
        diagnostics_js() + "\n  function updateDispatchActions() {\n",
        "offline diagnostics javascript",
    )

    if "installOfflineQueueHandlers();" in content:
        content = replace_once(
            content,
            """  installOfflineQueueHandlers();\n  renderOfflineQueue();\n""",
            """  installOfflineQueueHandlers();\n  installOfflineDiagnosticsHandlers();\n  renderOfflineQueue();\n  refreshOfflineDiagnostics();\n""",
            "offline diagnostics startup",
        )
    else:
        raise PatchError("Offline queue startup hook not found.")

    return content


def main() -> int:
    if not APP_PATH.exists():
        raise SystemExit(f"Could not find {APP_PATH}; run from the repository root.")
    original = APP_PATH.read_text(encoding="utf-8")
    patched = patch_content(original)
    if patched == original:
        print("Sophia UI offline diagnostics patch already applied.")
        return 0
    APP_PATH.write_text(patched, encoding="utf-8")
    print(f"Patched {APP_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
