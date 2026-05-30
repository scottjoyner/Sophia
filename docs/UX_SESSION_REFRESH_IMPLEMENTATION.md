# Sophia UI Session Refresh Enhancement Plan

## Goal

Improve the Sophia voice-agent customer experience by preventing unnecessary re-authentication, making the top status/icon area refresh reliably, and giving users clear, persistent feedback about whether Sophia is connected, authenticated, recording, processing, or degraded.

This plan targets the active Hermes-facing UI in `voice-agent/src/voice_agent/server/app.py`.

## Current Behavior Observed From Code

- The root capture page is served from the inline `CAPTURE_PAGE` template in `voice-agent/src/voice_agent/server/app.py`.
- The top header already has status pills for graph/auth/LLM/runtime state, but not all of them refresh consistently.
- `verifyVoice(mode = 'manual')` always posts to `/auth/verify` for the selected or recorded audio.
- `recorder.onstop` immediately calls `verifyVoice('auto')`, which makes every new recording stop trigger another verification.
- File upload selection also calls `verifyVoice('auto')` immediately.
- `start()` clears `lastScore`, `lastAccepted`, and `lastAuthResult`, which means previously accepted voice authentication is discarded as soon as a new capture begins.
- `showAuthResult()` updates the card but does not update the top auth pill itself.

## P0 Fixes

### P0.1 Persist a short-lived trusted local auth session

Add a client-side auth session cache in `localStorage` keyed by user/device/session.

Recommended constants:

```js
const AUTH_CACHE_KEY = 'sophia_auth_session_v1';
const AUTH_TTL_MS = 10 * 60 * 1000;
```

Recommended functions:

```js
function getAuthSession() {
  try {
    const data = JSON.parse(localStorage.getItem(AUTH_CACHE_KEY) || 'null');
    if (!data || !data.accepted || !data.expires_at || Date.now() > data.expires_at) return null;
    if (data.user_id !== (userId.value || 'default')) return null;
    if (data.session_id !== (sessionId.value || 'mobile')) return null;
    return data;
  } catch {
    return null;
  }
}

function setAuthSession(data) {
  if (!data || !data.accepted) return;
  const session = {
    accepted: true,
    score: data.score,
    user_id: userId.value || 'default',
    session_id: sessionId.value || 'mobile',
    device_id: data.device_id || '',
    match_source: data.match_source || '',
    verified_at: Date.now(),
    expires_at: Date.now() + AUTH_TTL_MS,
  };
  localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify(session));
}

function clearAuthSession() {
  localStorage.removeItem(AUTH_CACHE_KEY);
}
```

Behavior:

- Manual verify always calls `/auth/verify`.
- Auto verify first checks `getAuthSession()`.
- If a valid auth session exists, do not re-post to `/auth/verify`; update the UI from cache and continue.
- Reverify only when the cache is expired, the user/session changes, or the user explicitly taps Verify.

### P0.2 Stop clearing accepted auth state on every recording start

Current `start()` resets:

```js
lastScore = null;
lastAccepted = null;
lastAuthResult = null;
```

Replace this with session-aware hydration:

```js
const session = getAuthSession();
if (session) {
  lastScore = session.score;
  lastAccepted = true;
  lastAuthResult = session;
  renderAuthSession(session, 'trusted');
} else {
  lastScore = null;
  lastAccepted = null;
  lastAuthResult = null;
}
updateDispatchActions();
```

This prevents the UI from visually logging the user out every time they start another capture.

### P0.3 Make the top auth pill refresh every time auth state changes

`showAuthResult()` should update both the result card and top auth pill:

```js
function renderAuthSession(data, source = 'live') {
  const accepted = Boolean(data && data.accepted);
  const score = Number(data && data.score || 0);
  const deviceLabel = data && data.device_id && data.device_id !== 'default' ? ' [' + data.device_id + ']' : '';
  showAuthResult(score, accepted, deviceLabel);
  setAuthPill(accepted ? 'pass' : 'fail', accepted ? 'verified ' + (score * 100).toFixed(0) + '%' : 'not verified');
  authStatus.title = accepted
    ? 'Verified ' + (source === 'trusted' ? 'from trusted session cache' : 'from live voice check')
    : 'Voice not verified';
}
```

Then call this helper after successful verify and when loading a cached session.

### P0.4 Add a refresh heartbeat for top-of-page status pills

Add a lightweight `refreshTopStatus()` that refreshes graph, runtime, event log summary, speaker list count, and cached auth state.

```js
async function refreshTopStatus() {
  await Promise.allSettled([
    refreshGraph(),
    refreshStatus(),
  ]);
  const session = getAuthSession();
  if (session) renderAuthSession(session, 'trusted');
  else setAuthPill('idle', 'auth needed');
}

refreshTopStatus();
setInterval(refreshTopStatus, 15000);
window.addEventListener('focus', refreshTopStatus);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshTopStatus();
});
```

This resolves the “top icon never refreshes” issue by refreshing on initial load, every 15 seconds, on window focus, and when the tab becomes visible.

## P1 Customer Experience Enhancements

### P1.1 Add clear auth session controls

Add a small action next to the auth pill/card:

- `Re-check voice` — forces `/auth/verify`.
- `Forget this session` — clears the local auth session and changes pill to `auth needed`.

This makes the session behavior understandable instead of mysterious.

### P1.2 Add friendly connection states

Top pills should use consistent states:

- `connected`
- `degraded`
- `offline`
- `auth needed`
- `verified`
- `processing`

Avoid raw backend terms in the primary UI. Keep raw details in the debug panel.

### P1.3 Improve empty/error states

Replace silent catches with visible but non-alarming UI messages:

```js
function setSoftError(target, message) {
  target.textContent = message;
  target.style.color = '#fbbf24';
}
```

Use this for graph/LLM/AssistX refresh failures.

### P1.4 Restore last selected mode and user preferences

Persist and restore:

- selected mode tab
- auto-dispatch toggle
- AssistX URL
- device label
- last successful authenticated user/session

The current hash-based tab persistence is helpful, but localStorage is better for returning users.

## P2 Larger UX Enhancements

### P2.1 Split the inline UI into static assets

Move CSS and JavaScript out of `app.py`:

```text
voice-agent/src/voice_agent/server/static/app.css
voice-agent/src/voice_agent/server/static/app.js
voice-agent/src/voice_agent/server/templates/capture.html
```

Benefits:

- easier review
- smaller FastAPI module
- easier future testing
- proper cache busting
- easier mobile UI iteration

### P2.2 Add a `/session/status` endpoint

Client-side localStorage is useful, but the server should become the source of truth for production.

Endpoint concept:

```http
GET /session/status?user_id=scott&session_id=mobile&device_id=...
```

Response:

```json
{
  "authenticated": true,
  "score": 0.91,
  "expires_at": 1760000000000,
  "device_id": "iphone",
  "match_source": "active_head"
}
```

### P2.3 Add Playwright smoke tests

Critical browser flows:

1. Page loads and shows top pills.
2. Status refresh updates graph/LLM pills.
3. Accepted auth stores a trusted session.
4. Starting a new recording does not clear the trusted auth pill.
5. Manual Verify bypasses cache and rechecks.
6. Forget Session clears cache and disables auth-only actions.

## Suggested Next Commit

Patch `voice-agent/src/voice_agent/server/app.py` with the P0 items first:

1. Add auth session helper functions near `setAuthPill()`.
2. Update `verifyVoice()` to use cache for auto mode and persist accepted sessions.
3. Update `start()` and `audioFile.onchange` to hydrate rather than blindly clear auth state.
4. Update `showAuthResult()` or add `renderAuthSession()` so the top auth pill refreshes with the card.
5. Add `refreshTopStatus()` heartbeat and focus/visibility listeners.

## Acceptance Criteria

- User can authenticate once and complete multiple captures without Sophia visually or functionally reauthenticating on every capture.
- Top auth pill changes immediately after successful verification.
- Top status pills refresh on page load, focus, visibility change, and every 15 seconds.
- Manual verification still forces a fresh check.
- Clearing the capture does not destroy a valid trusted auth session unless the user explicitly selects “Forget this session”.
- Expired auth sessions return the UI to `auth needed`.
