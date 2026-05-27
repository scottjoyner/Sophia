# Admin Voice Enrollment UI Deploy Guide

## Purpose

Use the browser capture page to add reviewed Scott-only voice clips when normal speaker matching is rejecting Scott too often. This is a recovery and maintenance workflow, not the normal authentication path.

## UI workflow

1. Open the Sophia voice-agent capture page.
2. Record a clean Scott-only clip or upload a reviewed audio file.
3. Review or add the transcript/context fields.
4. In the admin voiceprint enrollment panel, enter the configured maintenance key.
5. Click `Append this clip to owner voiceprint`.
6. Check the latest-action output for `ok: true`, `sample_count`, and any rejected-file errors.
7. Verify with a held-out Scott clip that was not used for enrollment.

The UI submits a multipart request to:

```text
POST /voiceprints/owner-override-enroll
```

The server saves the submitted clip under the capture directory, appends it to the owner voiceprint, emits a `voiceprint_owner_override_enrolled` event, and returns the enrollment summary.

## Required deployment settings

Set these on the voice-agent deployment host or service environment:

```bash
export SOPHIA_OWNER_USER_ID=scott
export SOPHIA_OWNER_OVERRIDE_ENABLED=true
export SOPHIA_OWNER_OVERRIDE_TOKEN_FILE="$HOME/.config/sophia/owner_override"
```

Create the maintenance key file locally:

```bash
mkdir -p "$HOME/.config/sophia"
python - <<'PY'
import pathlib
import secrets
path = pathlib.Path.home() / ".config/sophia/owner_override"
path.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
print(path)
PY
chmod 600 "$HOME/.config/sophia/owner_override"
```

Optional quality gates:

```bash
export SOPHIA_OWNER_APPEND_MIN_SECONDS=2
export SOPHIA_OWNER_APPEND_MAX_SECONDS=30
```

## Docker Compose notes

Prefer mounting the key file read-only instead of baking it into the image:

```yaml
environment:
  SOPHIA_OWNER_USER_ID: scott
  SOPHIA_OWNER_OVERRIDE_ENABLED: "true"
  SOPHIA_OWNER_OVERRIDE_TOKEN_FILE: /run/secrets/sophia_owner_admin_key
  SOPHIA_OWNER_APPEND_MIN_SECONDS: "2"
  SOPHIA_OWNER_APPEND_MAX_SECONDS: "30"
volumes:
  - ~/.config/sophia/owner_override:/run/secrets/sophia_owner_admin_key:ro
```

After deployment, confirm the server sees the setting:

```bash
curl http://127.0.0.1:8765/status
```

Look for:

```json
"voiceprint_override": {
  "owner_user_id": "scott",
  "enabled": true,
  "key_configured": true
}
```

## Clip quality checklist

Use clips that are:

- Scott-only.
- 2 to 30 seconds long by default.
- free of music, cross-talk, and long silence.
- representative of the devices that fail most often.
- ideally WAV when uploaded manually.

The browser UI converts live recordings to WAV for the enrollment request when possible.

## Post-enrollment verification

Run a held-out file through verification:

```bash
voice-agent verify --config configs/dev.yaml --user scott --wav /path/to/held-out-scott.wav
```

Watch recent UI/server events:

```bash
curl 'http://127.0.0.1:8765/events?after_id=0&session_id=mobile'
```

## Handoff notes

Before handing this to another agent, verify the following:

- The capture page shows the admin voiceprint enrollment panel.
- `/status` exposes `voiceprint_override.enabled` and `key_configured`.
- `/voiceprints/owner-override-enroll` rejects requests when the feature is disabled.
- The route rejects the wrong user id.
- The route rejects an incorrect maintenance key.
- A clean Scott WAV clip appends successfully.
- The response appears in the UI latest-action panel.
- A held-out Scott clip verifies better after appending samples.
