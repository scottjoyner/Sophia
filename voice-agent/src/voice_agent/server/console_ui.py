from __future__ import annotations

CONSOLE_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Sophia Console</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b0f14;
      --panel: #111821;
      --panel-2: #0f172a;
      --border: #253140;
      --border-2: #2e3947;
      --text: #f4f7fb;
      --muted: #9aa7b6;
      --muted-2: #64748b;
      --accent: #7dd3fc;
      --green: #34d399;
      --amber: #fbbf24;
      --red: #fb7185;
      --radius: 12px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; height: 100%; }
    body { background: var(--bg); color: var(--text); min-height: 100vh; }
    button { font: inherit; cursor: pointer; border: 0; border-radius: 10px; padding: 12px 16px; font-weight: 600; min-height: 46px; transition: opacity .15s, background .15s; }
    button:disabled { opacity: .4; cursor: default; }
    input, textarea, select { font: inherit; width: 100%; border: 1px solid var(--border-2); border-radius: 10px; background: #071019; color: var(--text); padding: 12px; }
    input:focus, textarea:focus, select:focus { outline: none; border-color: var(--accent); }
    .hidden { display: none !important; }

    #login { position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; padding: 20px; background: radial-gradient(1200px 600px at 50% -10%, #16202c, var(--bg)); }
    .login-card { width: min(100%, 380px); background: var(--panel); border: 1px solid var(--border); border-radius: 16px; padding: 28px; box-shadow: 0 24px 60px rgba(0,0,0,.45); }
    .login-card h1 { margin: 0 0 4px; font-size: 24px; letter-spacing: -.02em; }
    .login-card p { margin: 0 0 18px; color: var(--muted); font-size: 13px; }
    .login-card button { width: 100%; background: var(--accent); color: #061014; }
    .login-card button:hover { background: #67c5f0; }
    .login-error { color: var(--red); font-size: 13px; min-height: 18px; margin-top: 10px; }
    #loginMic.rec, #micBtn.rec { box-shadow: 0 0 0 2px var(--red); }

    #app { display: none; height: 100vh; flex-direction: column; }
    header.topbar { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); background: var(--panel); position: sticky; top: 0; z-index: 10; flex-wrap: wrap; }
    .brand { font-weight: 700; font-size: 18px; letter-spacing: -.02em; }
    .brand span { color: var(--accent); }
    .live-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted-2); display: inline-block; }
    .live-dot.on { background: var(--green); box-shadow: 0 0 8px var(--green); }
    .pills { display: flex; gap: 8px; margin-left: auto; flex-wrap: wrap; }
    .pill { border-radius: 999px; padding: 6px 12px; font-size: 12px; border: 1px solid var(--border-2); background: var(--panel-2); color: var(--muted); white-space: nowrap; }
    .pill.ok { border-color: var(--green); color: #6ee7b7; }
    .pill.bad { border-color: var(--red); color: #fda4af; }
    .pill.warn { border-color: var(--amber); color: #fcd34d; }
    .pill button { padding: 0; margin: 0; background: none; border: 0; color: inherit; font: inherit; min-height: 0; }

    .layout { flex: 1; display: grid; grid-template-columns: 1fr 360px; min-height: 0; }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } #inbox { display: none; } #inbox.show { display: block; } }

    .main-col { display: flex; flex-direction: column; min-height: 0; }
    nav.tabs { display: flex; gap: 4px; padding: 10px 12px 0; border-bottom: 1px solid var(--border); background: var(--panel); flex-wrap: wrap; }
    .tab { background: transparent; border: 0; border-bottom: 2px solid transparent; color: var(--muted); border-radius: 0; padding: 10px 14px; min-height: 0; font-size: 13px; }
    .tab.active { color: var(--text); border-bottom-color: var(--accent); }
    .tab:hover:not(.active) { color: var(--muted-2); }
    .tab-panel { display: none; flex: 1; min-height: 0; flex-direction: column; }
    .tab-panel.active { display: flex; }

    /* Chat */
    .messages { flex: 1; overflow-y: auto; padding: 18px 16px; display: flex; flex-direction: column; gap: 12px; }
    .msg { max-width: 78%; padding: 12px 14px; border-radius: 14px; line-height: 1.5; font-size: 14px; white-space: pre-wrap; word-wrap: break-word; }
    .msg.user { align-self: flex-end; background: linear-gradient(180deg, #1e3a4d, #16303f); border: 1px solid #2c4a5e; }
    .msg.assistant { align-self: flex-start; background: var(--panel); border: 1px solid var(--border); }
    .msg .caret { display: inline-block; width: 8px; height: 16px; background: var(--accent); margin-left: 2px; vertical-align: -3px; animation: blink 1s steps(2) infinite; }
    @keyframes blink { 50% { opacity: 0; } }
    .composer { border-top: 1px solid var(--border); padding: 12px 16px; background: var(--panel); display: flex; gap: 10px; align-items: flex-end; }
    .composer textarea { resize: none; min-height: 48px; max-height: 140px; }
    .composer .send { background: var(--accent); color: #061014; flex: 0 0 auto; }
    .composer .send:hover { background: #67c5f0; }
    .hint { color: var(--muted-2); font-size: 12px; padding: 0 16px 8px; }

    /* Generic sections */
    .section { padding: 16px; overflow-y: auto; flex: 1; }
    .section h2 { margin: 0 0 12px; font-size: 15px; color: #cbd5e1; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .identity-strip { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 12px; background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 12px; }
    .identity-strip .id-btn { background: var(--panel-2); border: 1px solid var(--border-2); color: var(--text); flex: 0 0 auto; min-height: 42px; padding: 0 14px; }
    .identity-strip .id-btn:hover:not(:disabled) { background: #29394f; }
    .identity-strip .id-btn.warn { border-color: var(--amber); color: #fcd34d; }
    .voice-status { font-size: 12px; color: var(--muted); flex-basis: 100%; }
    .voice-status.ok { color: #6ee7b7; }
    .voice-status.bad { color: #fda4af; }
    .voice-status.link { color: #7dd3fc; }
    .health-card { border: 1px solid var(--border); background: var(--panel-2); border-radius: 10px; padding: 12px; margin-bottom: 10px; }
    .health-card .hc-title { font-weight: 600; font-size: 13px; margin-bottom: 6px; }
    .health-card .hc-row { font-size: 12px; color: var(--muted); margin: 3px 0; }
    .link-item { font-size: 12px; padding: 6px 8px; border-left: 3px solid var(--accent); background: #0f172a; border-radius: 0 6px 6px 0; margin: 4px 0; }
    .link-item .li-score { color: var(--muted-2); }

    /* Meetings */
    .meter { height: 8px; border-radius: 999px; background: #1e293b; overflow: hidden; margin-top: 10px; }
    .meter > div { height: 100%; width: 0%; background: linear-gradient(90deg, #34d399, #7dd3fc); transition: width .15s linear; }
    .seg { padding: 8px 10px; border-left: 3px solid var(--accent); margin: 6px 0; background: #0f172a; border-radius: 0 6px 6px 0; font-size: 13px; line-height: 1.45; }
    .seg .spk { font-weight: 600; color: var(--accent); font-size: 12px; }
    .meeting-item { font-size: 12px; padding: 8px; border: 1px solid var(--border); border-radius: 8px; margin: 6px 0; cursor: pointer; }
    .meeting-item:hover { border-color: var(--accent); }

    /* Dispatch */
    .dispatch-log .dl-item { font-size: 12px; padding: 6px 8px; border-left: 3px solid var(--green); background: #0f172a; border-radius: 0 6px 6px 0; margin: 4px 0; }
    .dispatch-log .dl-item.failed { border-left-color: var(--red); }
    .trace { font-size: 12px; line-height: 1.55; background: #071019; border: 1px solid var(--border); border-radius: 8px; padding: 10px; color: #cbd5e1; min-height: 40px; }

    /* Inbox */
    #inbox { border-left: 1px solid var(--border); background: var(--panel); display: flex; flex-direction: column; min-height: 0; }
    #inbox h2 { margin: 0; padding: 14px 16px; font-size: 14px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; }
    #inbox h2 .count { margin-left: auto; font-size: 12px; color: var(--muted); }
    .task-list { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
    .task { border: 1px solid var(--border); background: var(--panel-2); border-radius: 10px; padding: 12px; }
    .task .t-head { display: flex; align-items: center; gap: 8px; }
    .task .t-title { font-weight: 600; font-size: 14px; }
    .task .prio { font-size: 10px; padding: 2px 8px; border-radius: 999px; text-transform: uppercase; font-weight: 700; letter-spacing: .04em; }
    .prio.low { background: #102018; color: #6ee7b7; }
    .prio.medium { background: #1f1a0a; color: #fcd34d; }
    .prio.high { background: #2a0a14; color: #fda4af; }
    .task .t-desc { color: var(--muted); font-size: 12px; margin-top: 6px; line-height: 1.4; }
    .task .t-status { margin-top: 8px; font-size: 11px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .badge { padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
    .badge.extracted { background: #1e293b; color: #cbd5e1; }
    .badge.ingesting { background: #1f1a0a; color: #fcd34d; }
    .badge.ingested { background: #0a2a1a; color: #6ee7b7; }
    .badge.failed { background: #2a0a14; color: #fda4af; }
    .badge.assigned { background: #10243a; color: #7dd3fc; }
    .task .t-meta { color: var(--muted-2); font-size: 11px; }

    #toast { position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%); background: #1e293b; border: 1px solid var(--border-2); color: var(--text); padding: 10px 16px; border-radius: 10px; font-size: 13px; opacity: 0; transition: opacity .2s; pointer-events: none; z-index: 50; max-width: 90%; }
    #toast.show { opacity: 1; }
    @media (max-width: 900px) { .row { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div id="login">
    <form class="login-card" id="loginForm">
      <h1>Sophia <span>Console</span></h1>
      <p>Voice is your primary key. Speak to unlock — your passphrase is a fallback only.</p>

      <div class="identity-strip" style="border:0;background:transparent;padding:0;margin-bottom:10px;">
        <button class="mic" id="loginMic" type="button" style="background:var(--panel-2);border:1px solid var(--border-2);color:var(--text);flex:0 0 auto;min-width:54px;min-height:48px;font-size:18px;">🎙</button>
        <button type="button" id="voiceLoginBtn" style="flex:1;background:var(--accent);color:#061014;">Unlock with voice</button>
      </div>
      <div id="voiceLoginStatus" class="login-error" style="margin-top:-2px;margin-bottom:6px;color:var(--muted);">Hold 🎙 to record, then Unlock with voice.</div>

      <div style="display:flex;align-items:center;gap:8px;margin:4px 0 10px;">
        <div style="flex:1;height:1px;background:var(--border);"></div>
        <button type="button" id="passToggle" style="background:none;border:0;color:var(--muted);font-weight:500;font-size:12px;min-height:0;padding:4px 6px;">Use passphrase instead</button>
        <div style="flex:1;height:1px;background:var(--border);"></div>
      </div>

      <div id="passRow" class="hidden">
        <label for="pass" style="font-size:12px;color:var(--muted);">Passphrase (fallback)</label>
        <input id="pass" type="password" autocomplete="current-password" placeholder="enter passphrase" style="margin-top:6px;">
        <button type="submit" id="loginBtn" style="margin-top:14px;background:var(--panel-2);color:var(--text);border:1px solid var(--border-2);">Unlock with passphrase</button>
      </div>

      <div class="login-error" id="loginError"></div>
    </form>
  </div>

  <div id="app">
    <header class="topbar">
      <div class="brand">Sophia <span>Console</span> <span id="liveDot" class="live-dot" title="live event stream"></span></div>
      <div class="pills">
        <div class="pill" id="netPill">net: …</div>
        <div class="pill" id="modelPill">model: …</div>
        <div class="pill" id="assistxPill">assistx: …</div>
        <div class="pill" id="authPill">user: …</div>
        <button class="pill" id="inboxToggle" title="Toggle task inbox">Tasks</button>
        <button class="pill" id="logoutBtn" title="Lock console">Lock</button>
      </div>
    </header>

    <div class="layout">
      <div class="main-col">
        <nav class="tabs">
          <button class="tab active" data-tab="chat">Chat</button>
          <button class="tab" data-tab="voice">Voice</button>
          <button class="tab" data-tab="meetings">Meetings</button>
          <button class="tab" data-tab="dispatch">Dispatch</button>
        </nav>

        <section class="tab-panel active" data-panel="chat">
          <div class="messages" id="messages"></div>
          <div class="hint">Enter to send · Shift+Enter for newline · Ask for anything and tasks are auto-routed to AssistX.</div>
          <div class="composer">
            <textarea id="composer" rows="1" placeholder="Message Sophia…"></textarea>
            <button class="send" id="sendBtn" type="button">Send</button>
          </div>
        </section>

        <section class="tab-panel" data-panel="voice">
          <div class="section">
            <h2>🎙 Voice Identity</h2>
            <div class="identity-strip">
              <button class="mic" id="micBtn" title="Hold to record a voice check" type="button" style="background:var(--panel-2);border:1px solid var(--border-2);color:var(--text);flex:0 0 auto;min-width:52px;min-height:42px;">🎙</button>
              <input id="adminKey" type="password" placeholder="owner override key" autocomplete="off" style="flex:1;min-width:120px;">
              <button class="id-btn" id="verifyBtn" type="button">Verify voice</button>
              <button class="id-btn warn" id="overrideBtn" type="button" title="Rejected? Override to add this clip and retrain the speaker embedding">Override &amp; retrain</button>
              <span id="voiceStatus" class="voice-status">Hold 🎙 to record, then Verify.</span>
            </div>
            <div class="identity-strip" style="margin-top:10px;">
              <button class="id-btn" id="backfillBtn" type="button" title="Re-link all enrolled voiceprints into the global Speaker pool">Backfill global speakers</button>
              <span id="backfillStatus" class="voice-status"></span>
            </div>
            <div class="identity-strip" style="margin-top:10px;">
              <input id="enrollName" placeholder="new speaker name (user id)" style="flex:1;min-width:120px;">
              <button class="id-btn" id="enrollBtn" type="button" style="background:var(--accent);color:#061014;">Enroll new speaker</button>
              <span id="enrollStatus" class="voice-status"></span>
            </div>
            <div id="voiceHealth"></div>
          </div>
        </section>

        <section class="tab-panel" data-panel="meetings">
          <div class="section">
            <h2>📅 Meeting Mode</h2>
            <label for="meetingFile">Upload meeting audio</label>
            <input id="meetingFile" type="file" accept="audio/*,video/*">
            <div class="row" style="margin-top:10px;">
              <label style="font-size:11px;display:flex;align-items:center;gap:4px;">Max speakers:
                <select id="meetingMaxSpeakers" style="font-size:11px;padding:2px 4px;">
                  <option value="">auto</option><option value="2">2</option><option value="3">3</option>
                  <option value="4">4</option><option value="5">5</option><option value="6">6</option>
                </select>
              </label>
              <button id="processMeetingBtn" class="id-btn" style="background:var(--amber);color:#1c1900;" disabled>Process Meeting</button>
            </div>
            <div class="meter"><div id="meetingProgress"></div></div>
            <div id="meetingStatus" class="hint" style="padding-left:0;">Upload an audio file to process.</div>
            <div id="meetingResults" style="display:none;">
              <div id="meetingTranscript" style="margin-top:10px;"></div>
              <div id="meetingSummary" style="display:none;margin-top:10px;"><pre id="summaryText" style="max-height:300px;overflow:auto;background:#071019;border:1px solid var(--border);border-radius:8px;padding:10px;color:#cbd5e1;font-size:12px;"></pre></div>
              <button id="dispatchMeetingBtn" class="id-btn" style="background:var(--accent);color:#061014;margin-top:10px;" disabled>Dispatch to AssistX</button>
            </div>
            <h2 style="margin-top:18px;">Past Meetings</h2>
            <div id="meetingHistory"></div>
          </div>
        </section>

        <section class="tab-panel" data-panel="dispatch">
          <div class="section">
            <h2>📤 Auto-Assist Bridge</h2>
            <div class="row" style="margin-bottom:10px;">
              <div><label for="dispatchUrl">AssistX URL</label><input id="dispatchUrl" value="http://host.docker.internal:8000"></div>
              <div><label for="dispatchToken">Webhook Secret</label><input id="dispatchToken" type="password" placeholder="optional"></div>
            </div>
            <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer;">
              <input id="autoDispatchToggle" type="checkbox" style="width:auto;"> Auto-dispatch voice events to AssistX
            </label>
            <button id="saveDispatchConfigBtn" class="id-btn" style="background:var(--panel-2);border:1px solid var(--border-2);color:var(--text);margin-top:8px;">Save Config</button>
            <h2 style="margin-top:16px;">Send Event</h2>
            <div class="row">
              <div><label for="dispatchEventType">Event Type</label>
                <select id="dispatchEventType">
                  <option value="voice_auth">voice_auth</option>
                  <option value="meeting_transcript">meeting_transcript</option>
                  <option value="task_created">task_created</option>
                  <option value="ralph_iteration">ralph_iteration</option>
                </select>
              </div>
              <div><label for="dispatchAuto">Auto Dispatch</label>
                <select id="dispatchAuto"><option value="true">Yes</option><option value="false">No</option></select>
              </div>
            </div>
            <label for="dispatchText" style="margin-top:8px;display:block;">Event Text</label>
            <textarea id="dispatchText" placeholder="Event payload text…" style="min-height:60px;margin-top:4px;"></textarea>
            <button id="dispatchSendBtn" class="id-btn" style="background:var(--accent);color:#061014;margin-top:8px;">Send to AssistX</button>
            <h2 style="margin-top:16px;">Execution Trace</h2>
            <div id="executionTrace" class="trace">No dispatch yet.</div>
            <h2 style="margin-top:16px;">Dispatch Log</h2>
            <div id="dispatchLog" class="dispatch-log"></div>
          </div>
        </section>
      </div>

      <aside id="inbox">
        <h2>📥 Task Inbox → AssistX <span class="count" id="taskCount">0</span></h2>
        <div class="task-list" id="taskList">
          <div style="color:var(--muted-2);font-size:13px;">No tasks yet. Tasks extracted from your conversations appear here and are auto-ingested into AssistX.</div>
        </div>
      </aside>
    </div>
  </div>

  <div id="toast"></div>

<script>
(() => {
  const $ = (id) => document.getElementById(id);
  const loginEl = $('login'), appEl = $('app');
  const messages = $('messages');
  const composer = $('composer');
  const sendBtn = $('sendBtn');
  const taskList = $('taskList');
  const taskCount = $('taskCount');
  const voiceHealth = $('voiceHealth');
  let sessionMeta = {};
  let streaming = false;
  let recorder = null, micStream = null, micChunks = [];
  let lastVoiceBlob = null;
  let eventSocket = null;

  function toast(msg, ms = 2600) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove('show'), ms);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  // ---- Offline-first ----
  let isOffline = !navigator.onLine;
  const offlineQueue = [];
  function updateNetPill() {
    const p = $('netPill');
    if (isOffline) { p.className = 'pill bad'; p.textContent = 'net: offline' + (offlineQueue.length ? ' (' + offlineQueue.length + ' queued)' : ''); }
    else { p.className = 'pill ok'; p.textContent = 'net: online'; }
  }
  function enqueueOffline(fn) { if (!offlineQueue.includes(fn)) offlineQueue.push(fn); updateNetPill(); }
  async function flushOffline() {
    if (!offlineQueue.length) return;
    const pending = offlineQueue.splice(0, offlineQueue.length);
    updateNetPill();
    for (const fn of pending) { try { await fn(); } catch {} }
  }
  window.addEventListener('offline', () => { isOffline = true; updateNetPill(); });
  window.addEventListener('online', () => { isOffline = false; updateNetPill(); flushOffline(); });
  updateNetPill();

  async function api(path, opts = {}) {
    try {
      const r = await fetch(path, { credentials: 'same-origin', ...opts });
      if (isOffline) { isOffline = false; updateNetPill(); }
      return r;
    } catch (err) {
      if (!isOffline) { isOffline = true; updateNetPill(); }
      throw err;
    }
  }

  // ---- Tabs ----
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === tab));
      if (tab === 'voice') refreshVoiceHealth();
      if (tab === 'meetings') loadMeetingHistory();
      if (tab === 'dispatch') refreshDispatchStatus();
    });
  });

  // ---- Boot ----
  async function boot() {
    try {
      const r = await api('/auth/session');
      const data = await r.json();
      if (!data.authenticated) { showLogin(); return; }
      sessionMeta = data;
      enterApp();
    } catch { showLogin(); }
  }
  function showLogin() { loginEl.classList.remove('hidden'); appEl.style.display = 'none'; }
  async function enterApp() {
    loginEl.classList.add('hidden');
    appEl.style.display = 'flex';
    $('authPill').textContent = 'user: ' + (sessionMeta.user_id || 'scott');
    await refreshStatus();
    connectEvents();
    addMessage('assistant', "Welcome to Sophia Console. I'm connected to the AssistX auto-router. Ask me anything — if you request a task, I'll extract it and route it to AssistX automatically.");
  }

  async function refreshStatus() {
    try {
      const s = await (await api('/status')).json();
      const llm = s.llm || {};
      const p = $('modelPill');
      if (llm.assistant_configured) {
        p.className = 'pill ok';
        p.textContent = 'model: ' + (llm.assistant_model || 'auto-router');
      } else {
        p.className = 'pill warn';
        p.textContent = 'model: mock (no LLM configured)';
      }
    } catch {}
    try {
      const d = await (await api('/dispatch/status')).json();
      const p = $('assistxPill');
      if (d.assistx_reachable) { p.className = 'pill ok'; p.textContent = 'assistx: connected'; }
      else { p.className = 'pill bad'; p.textContent = 'assistx: unreachable'; }
    } catch { const p = $('assistxPill'); p.className = 'pill warn'; p.textContent = 'assistx: unknown'; }
  }

  // ---- Login (voice primary, passphrase secondary) ----
  const loginMic = $('loginMic'), voiceLoginBtn = $('voiceLoginBtn'), voiceLoginStatus = $('voiceLoginStatus');
  $('passToggle').addEventListener('click', () => { $('passRow').classList.remove('hidden'); $('passToggle').classList.add('hidden'); $('pass').focus(); });
  loginMic.addEventListener('mousedown', startLoginVoice);
  loginMic.addEventListener('mouseup', stopLoginVoice);
  loginMic.addEventListener('mouseleave', stopLoginVoice);
  loginMic.addEventListener('touchstart', (e) => { e.preventDefault(); startLoginVoice(); });
  loginMic.addEventListener('touchend', (e) => { e.preventDefault(); stopLoginVoice(); });

  async function startLoginVoice() {
    if (!navigator.mediaDevices || !window.MediaRecorder) { toast('Voice unlock needs a secure context + mic.'); return; }
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorder = new MediaRecorder(micStream);
      micChunks = [];
      recorder.ondataavailable = (ev) => { if (ev.data.size) micChunks.push(ev.data); };
      recorder.start();
      loginMic.classList.add('rec'); loginMic.textContent = '●';
      voiceLoginStatus.className = 'login-error'; voiceLoginStatus.style.color = 'var(--muted)'; voiceLoginStatus.textContent = 'Recording… release to finish.';
    } catch { toast('Microphone blocked.'); }
  }
  async function stopLoginVoice() {
    if (!recorder || recorder.state === 'inactive') return;
    loginMic.classList.remove('rec'); loginMic.textContent = '🎙';
    const stopped = new Promise((res) => recorder.onstop = res);
    recorder.stop(); await stopped;
    if (micStream) micStream.getTracks().forEach(t => t.stop());
    const blob = new Blob(micChunks, { type: recorder.mimeType || 'audio/webm' });
    lastVoiceBlob = blob;
    if (!blob.size) return;
    voiceLoginStatus.className = 'login-error'; voiceLoginStatus.style.color = 'var(--muted)'; voiceLoginStatus.textContent = 'Clip ready — tap “Unlock with voice”.';
  }
  voiceLoginBtn.addEventListener('click', async () => {
    if (!lastVoiceBlob || !lastVoiceBlob.size) { voiceLoginStatus.className = 'login-error'; voiceLoginStatus.style.color = 'var(--red)'; voiceLoginStatus.textContent = 'Record a voice clip first (hold 🎙).'; return; }
    voiceLoginBtn.disabled = true; voiceLoginStatus.className = 'login-error'; voiceLoginStatus.style.color = 'var(--muted)'; voiceLoginStatus.textContent = 'Verifying voice identity…';
    const form = new FormData();
    form.append('audio', lastVoiceBlob, 'voice-login.webm');
    form.append('user_id', 'scott');
    form.append('session_id', 'console');
    try {
      const r = await api('/auth/voice-login', { method: 'POST', body: form });
      const d = await r.json();
      if (r.ok && d.authenticated) { location.reload(); return; }
      const pct = ((d.score || 0) * 100).toFixed(0);
      voiceLoginStatus.className = 'login-error'; voiceLoginStatus.style.color = 'var(--red)';
      voiceLoginStatus.textContent = 'Voice rejected ' + pct + '% — try again or use your passphrase.';
    } catch { voiceLoginStatus.className = 'login-error'; voiceLoginStatus.style.color = 'var(--red)'; voiceLoginStatus.textContent = 'Voice unlock failed.'; }
    finally { voiceLoginBtn.disabled = false; }
  });

  $('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('loginBtn'); btn.disabled = true; $('loginError').textContent = '';
    try {
      const r = await api('/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ passphrase: $('pass').value }) });
      if (r.ok) location.reload();
      else $('loginError').textContent = 'Invalid passphrase.';
    } catch { $('loginError').textContent = 'Login request failed.'; }
    finally { btn.disabled = false; }
  });
  $('logoutBtn').addEventListener('click', async () => { await api('/auth/logout', { method: 'POST' }); location.reload(); });
  $('inboxToggle').addEventListener('click', () => $('inbox').classList.toggle('show'));

  // ---- Chat ----
  function addMessage(role, text, withCaret = false) {
    const el = document.createElement('div');
    el.className = 'msg ' + role;
    el.innerHTML = escapeHtml(text) + (withCaret ? '<span class="caret"></span>' : '');
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
    return el;
  }
  const history = [];
  function pushHistory(role, content) { history.push({ role, content }); if (history.length > 24) history.shift(); }

  async function sendRaw(text) {
    if (streaming) return;
    addMessage('user', text);
    pushHistory('user', text);
    const assistantEl = addMessage('assistant', '', true);
    let full = '';
    streaming = true; sendBtn.disabled = true;
    const messages_payload = [
      { role: 'system', content: 'You are Sophia, a concise and proactive personal assistant connected to the AssistX auto-router. Answer clearly and, when the user asks for something to be done, make the request explicit so it can be turned into a task.' },
      ...history.slice(0, -1),
      { role: 'user', content: text },
    ];
    try {
      const res = await api('/api/chat/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ messages: messages_payload, session_id: 'console' }) });
      if (res.status === 401) { toast('Session expired — please log in again.'); showLogin(); return; }
      if (!res.ok || !res.body) throw new Error('stream failed');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf('\\n\\n')) >= 0) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (!chunk.startsWith('data:')) continue;
          const json = chunk.slice(5).trim();
          if (!json) continue;
          let evt; try { evt = JSON.parse(json); } catch { continue; }
          handleEvent(evt, assistantEl);
        }
      }
      if (buffer.startsWith('data:')) { try { handleEvent(JSON.parse(buffer.slice(5).trim()), assistantEl); } catch {} }
    } catch (err) {
      assistantEl.querySelector('.caret')?.remove();
      if (isOffline) {
        assistantEl.innerHTML = escapeHtml('⚠ Offline — message queued, will send when connection returns.');
        enqueueOffline(() => { assistantEl.remove(); sendRaw(text); });
      } else {
        assistantEl.innerHTML = escapeHtml('⚠ Could not reach the assistant: ' + err.message);
        const mp = $('modelPill'); mp.className = 'pill bad'; mp.textContent = 'model: error';
      }
    } finally {
      streaming = false; sendBtn.disabled = false;
      if (!full) full = assistantEl.textContent || '';
      pushHistory('assistant', full);
    }
  }

  function handleEvent(evt, assistantEl) {
    if (evt.type === 'token') {
      assistantEl.querySelector('.caret')?.remove();
      assistantEl.innerHTML = escapeHtml(assistantEl.textContent + evt.text) + '<span class="caret"></span>';
      messages.scrollTop = messages.scrollHeight;
    } else if (evt.type === 'tasks') {
      assistantEl.querySelector('.caret')?.remove();
      if (evt.tasks && evt.tasks.length) toast(evt.tasks.length + ' task(s) extracted — routing to AssistX…');
    } else if (evt.type === 'ingested') {
      (evt.results || []).forEach(addTaskCard);
      refreshTaskCount();
      (evt.results || []).forEach(r => { if (r.correlation_id) pollTrace(r.correlation_id, r.task); });
    } else if (evt.type === 'error') {
      assistantEl.querySelector('.caret')?.remove();
      assistantEl.innerHTML = escapeHtml('⚠ ' + (evt.error || 'error'));
      const mp = $('modelPill'); mp.className = 'pill bad'; mp.textContent = 'model: error';
    } else if (evt.type === 'done') {
      assistantEl.querySelector('.caret')?.remove();
    }
  }

  function refreshTaskCount() { taskCount.textContent = String(taskList.querySelectorAll('.task').length); }

  function addTaskCard(result) {
    const task = result.task || {};
    const dispatch = result.dispatch || {};
    const card = document.createElement('div');
    card.className = 'task';
    card.dataset.cid = result.correlation_id || '';
    const prio = (task.priority || 'medium').toLowerCase();
    const title = escapeHtml(task.title || 'Untitled task');
    const desc = escapeHtml(task.description || '');
    const statusBadge = dispatch.sent ? '<span class="badge ingested">ingested ✓</span>' : '<span class="badge failed">failed</span>';
    const meta = dispatch.task_id ? '<span class="t-meta">assistx task_id: ' + escapeHtml(String(dispatch.task_id)) + '</span>'
      : (dispatch.error ? '<span class="t-meta" style="color:#fda4af;">' + escapeHtml(dispatch.error) + '</span>' : '');
    card.innerHTML =
      '<div class="t-head"><span class="prio ' + prio + '">' + prio + '</span><span class="t-title">' + title + '</span></div>' +
      (desc ? '<div class="t-desc">' + desc + '</div>' : '') +
      '<div class="t-status"><span class="badge extracted">extracted</span>' + statusBadge + meta + '</div>';
    const empty = taskList.querySelector('div');
    if (empty && taskList.children.length === 1 && empty.style.color) empty.remove();
    taskList.prepend(card);
    card._status = card.querySelector('.t-status');
  }

  async function pollTrace(correlationId, task) {
    if (!correlationId) return;
    const card = taskList.querySelector('.task[data-cid="' + cssEscape(correlationId) + '"]');
    for (let i = 0; i < 5; i++) {
      await new Promise(r => setTimeout(r, 1500));
      try {
        const d = await (await api('/dispatch/trace/' + encodeURIComponent(correlationId))).json();
        if (d.error) return;
        const state = d.current_state || 'unknown';
        const last = (d.events && d.events.length) ? d.events[d.events.length - 1].event_type : '';
        if (card && card._status) {
          const assigned = (d.events || []).find(e => /assign|route|worker|claim/.test(e.event_type || ''));
          const assignedBadge = assigned ? '<span class="badge assigned">assigned: ' + escapeHtml(assigned.event_type) + '</span>' : '';
          card._status.innerHTML = '<span class="badge extracted">extracted</span><span class="badge ingested">ingested ✓</span>' + assignedBadge + '<span class="t-meta">state: ' + escapeHtml(state) + (last ? ' · ' + escapeHtml(last) : '') + '</span>';
        }
        if (state === 'completed' || state === 'done' || assigned) return;
      } catch {}
    }
  }
  function cssEscape(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, c => '\\\\' + c); }

  function sendMessage() {
    const text = composer.value.trim();
    if (!text) return;
    composer.value = ''; composer.style.height = 'auto';
    sendRaw(text);
  }
  sendBtn.addEventListener('click', sendMessage);
  composer.addEventListener('input', () => { composer.style.height = 'auto'; composer.style.height = Math.min(140, composer.scrollHeight) + 'px'; });
  composer.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });

  // ---- Voice identity ----
  const micBtn = $('micBtn'), adminKey = $('adminKey'), verifyBtn = $('verifyBtn'), overrideBtn = $('overrideBtn'), voiceStatus = $('voiceStatus');
  try { const k = localStorage.getItem('sophia_admin_key'); if (k) adminKey.value = k; } catch {}
  adminKey.addEventListener('input', () => { try { localStorage.setItem('sophia_admin_key', adminKey.value); } catch {} });
  micBtn.addEventListener('mousedown', startVoiceCheck);
  micBtn.addEventListener('mouseup', stopVoiceCheck);
  micBtn.addEventListener('mouseleave', stopVoiceCheck);
  micBtn.addEventListener('touchstart', (e) => { e.preventDefault(); startVoiceCheck(); });
  micBtn.addEventListener('touchend', (e) => { e.preventDefault(); stopVoiceCheck(); });
  verifyBtn.addEventListener('click', () => verifyVoiceCheck(false));
  overrideBtn.addEventListener('click', overrideAndTrain);
  $('backfillBtn').addEventListener('click', backfillGlobalSpeakers);

  const enrollName = $('enrollName'), enrollBtn = $('enrollBtn'), enrollStatus = $('enrollStatus');
  enrollBtn.addEventListener('click', async () => {
    const name = (enrollName.value || '').trim();
    if (!name) { enrollStatus.className = 'voice-status bad'; enrollStatus.textContent = 'Enter a speaker name (user id).'; return; }
    if (!lastVoiceBlob || !lastVoiceBlob.size) { enrollStatus.className = 'voice-status bad'; enrollStatus.textContent = 'Record a voice clip first (hold 🎙).'; return; }
    const form = new FormData();
    form.append('audio', lastVoiceBlob, 'enroll.webm');
    form.append('user_id', name);
    form.append('force', 'false');
    enrollStatus.className = 'voice-status'; enrollStatus.textContent = 'Enrolling "' + name + '"…';
    try {
      const r = await api('/voiceprints/enroll', { method: 'POST', body: form });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'enroll failed');
      const samples = d.sample_count || '?';
      enrollStatus.className = 'voice-status link';
      enrollStatus.textContent = 'Enrolled "' + name + '" (' + samples + ' samples).';
      refreshVoiceHealth();
    } catch (err) { enrollStatus.className = 'voice-status bad'; enrollStatus.textContent = 'Enroll failed: ' + (err.message || err); }
  });

  async function backfillGlobalSpeakers() {
    const btn = $('backfillBtn'); const status = $('backfillStatus');
    const key = adminKey.value || '';
    if (!key.trim()) { status.className = 'voice-status bad'; status.textContent = 'Owner override key required to backfill.'; return; }
    btn.disabled = true; status.className = 'voice-status'; status.textContent = 'Backfilling global speakers…';
    const form = new FormData();
    form.append('admin_key', key);
    try {
      const r = await api('/voiceprints/backfill-global-speakers', { method: 'POST', body: form });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'backfill failed');
      status.className = 'voice-status link';
      status.textContent = 'Backfill done: linked ' + (d.linked || 0) + ', skipped ' + (d.skipped || 0) + ', errors ' + (d.errors || 0) + '.';
      refreshVoiceHealth();
    } catch (err) { status.className = 'voice-status bad'; status.textContent = 'Backfill failed: ' + (err.message || err); }
    finally { btn.disabled = false; }
  }

  async function startVoiceCheck() {
    if (!navigator.mediaDevices || !window.MediaRecorder) { toast('Voice check needs a secure context + mic.'); return; }
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorder = new MediaRecorder(micStream);
      micChunks = [];
      recorder.ondataavailable = (ev) => { if (ev.data.size) micChunks.push(ev.data); };
      recorder.start();
      micBtn.classList.add('rec'); micBtn.textContent = '●';
      voiceStatus.className = 'voice-status'; voiceStatus.textContent = 'Recording… release to verify.';
    } catch { toast('Microphone blocked.'); }
  }
  async function stopVoiceCheck() {
    if (!recorder || recorder.state === 'inactive') return;
    micBtn.classList.remove('rec'); micBtn.textContent = '🎙';
    const stopped = new Promise((res) => recorder.onstop = res);
    recorder.stop(); await stopped;
    if (micStream) micStream.getTracks().forEach(t => t.stop());
    const blob = new Blob(micChunks, { type: recorder.mimeType || 'audio/webm' });
    lastVoiceBlob = blob;
    if (!blob.size) return;
    await verifyVoiceCheck(false);
  }
  async function verifyVoiceCheck(showToast) {
    if (!lastVoiceBlob || !lastVoiceBlob.size) { voiceStatus.className = 'voice-status bad'; voiceStatus.textContent = 'Record a voice clip first (hold 🎙).'; return; }
    const form = new FormData();
    form.append('audio', lastVoiceBlob, 'voice-check.webm');
    form.append('user_id', sessionMeta.user_id || 'scott');
    form.append('session_id', 'console');
    voiceStatus.className = 'voice-status'; voiceStatus.textContent = 'Verifying voice identity…';
    try {
      const r = await api('/auth/verify', { method: 'POST', body: form });
      const d = await r.json();
      const pct = ((d.score || 0) * 100).toFixed(0);
      if (d.accepted) { voiceStatus.className = 'voice-status ok'; voiceStatus.textContent = 'Voice accepted ' + pct + '%' + (d.match_source === 'historical_fallback' ? ' (fallback)' : ''); }
      else { voiceStatus.className = 'voice-status bad'; voiceStatus.textContent = 'Voice rejected ' + pct + '% — use “Override & retrain” to add this clip and improve the model.'; }
      if (showToast) toast((d.accepted ? 'Voice accepted ' : 'Voice rejected ') + pct + '%', 3200);
      refreshVoiceHealth();
    } catch (err) {
      if (isOffline) { voiceStatus.className = 'voice-status'; voiceStatus.textContent = 'Offline — voice check queued, will retry when connection returns.'; enqueueOffline(() => verifyVoiceCheck(false)); }
      else { voiceStatus.className = 'voice-status bad'; voiceStatus.textContent = 'Voice check failed.'; }
    }
  }
  async function overrideAndTrain() {
    if (!lastVoiceBlob || !lastVoiceBlob.size) { voiceStatus.className = 'voice-status bad'; voiceStatus.textContent = 'Record a voice clip first (hold 🎙).'; return; }
    const key = adminKey.value || '';
    if (!key.trim()) { voiceStatus.className = 'voice-status bad'; voiceStatus.textContent = 'Owner override key required to retrain.'; return; }
    const form = new FormData();
    form.append('audio', lastVoiceBlob, 'owner-override.webm');
    form.append('user_id', sessionMeta.user_id || 'scott');
    form.append('session_id', 'console');
    form.append('admin_key', key);
    voiceStatus.className = 'voice-status'; voiceStatus.textContent = 'Override: adding clip + retraining speaker embedding…';
    try {
      const r = await api('/voiceprints/owner-override-enroll', { method: 'POST', body: form });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'override failed');
      const samples = d.sample_count || '?';
      const link = d.speaker_linkage || {};
      let linkText = '';
      if (link.linked) linkText = ' · linked to global speaker "' + (link.matched_speaker_user_id || 'owner') + '" (' + (link.method || 'linked') + ')';
      else if (link.error) linkText = ' · speaker link: ' + link.error;
      voiceStatus.className = 'voice-status link';
      voiceStatus.textContent = 'Voiceprint strengthened (' + samples + ' samples)' + linkText + '. Re-verifying…';
      await verifyVoiceCheck(false);
      refreshVoiceHealth();
    } catch (err) { voiceStatus.className = 'voice-status bad'; voiceStatus.textContent = 'Override failed: ' + (err.message || err); }
  }

  async function refreshVoiceHealth() {
    if (!voiceHealth) return;
    try {
      const [status, link] = await Promise.all([
        (await api('/voiceprints/status')).json(),
        (await api('/voiceprints/linkage')).json(),
      ]);
      let html = '';
      const users = status.users || [];
      html += '<div class="health-card"><div class="hc-title">Voiceprint Health</div>';
      if (!users.length) html += '<div class="hc-row">No enrolled speakers yet.</div>';
      users.forEach(u => {
        html += '<div class="hc-row"><b>' + escapeHtml(u.user_id) + '</b> — ' + (u.sample_count || 0) + ' samples · base threshold ' + (u.threshold ?? '?');
        if (u.devices && u.devices.length) html += ' · devices: ' + escapeHtml(u.devices.join(', '));
        html += '</div>';
        const calib = u.device_calibration || {};
        Object.keys(calib).forEach(dev => {
          const c = calib[dev];
          const adaptive = (c.accepted_mean != null) ? (c.accepted_mean - (u.adaptive_threshold_margin ?? 0.05)).toFixed(2) : '-';
          html += '<div class="link-item">device <b>' + escapeHtml(dev) + '</b> — adaptive threshold ' + adaptive + ' (accepted mean ' + (c.accepted_mean != null ? c.accepted_mean.toFixed(2) : '-') + ', n=' + ((c.n_accepted || 0) + (c.n_rejected || 0)) + ')</div>';
        });
      });
      html += '</div>';
      const linked = (link.linked_speakers || []);
      html += '<div class="health-card"><div class="hc-title">Global Speaker Linkage</div>';
      html += '<div class="hc-row">enabled: ' + (link.link_enabled ? 'yes' : 'no') + ' · threshold ' + (link.link_threshold ?? '?') + ' · embedding dim ' + (link.embedding_dim || 0) + '</div>';
      if (!linked.length) html += '<div class="hc-row">Not yet linked to a global speaker.</div>';
      linked.forEach(s => {
        html += '<div class="link-item">' + escapeHtml(s.speaker_user_id || '?') + (s.is_owner ? ' (owner)' : '') + ' <span class="li-score">score ' + (s.score != null ? (s.score * 100).toFixed(0) + '%' : '?') + ' · ' + escapeHtml(s.method || '') + '</span></div>';
      });
      html += '</div>';
      voiceHealth.innerHTML = html;
    } catch { voiceHealth.innerHTML = '<div class="health-card"><div class="hc-row">Could not load voice health.</div></div>'; }
  }

  // ---- Meetings ----
  const meetingFile = $('meetingFile'), processMeetingBtn = $('processMeetingBtn'), meetingProgress = $('meetingProgress'),
        meetingStatus = $('meetingStatus'), meetingResults = $('meetingResults'), meetingTranscript = $('meetingTranscript'),
        meetingSummary = $('meetingSummary'), summaryText = $('summaryText'), dispatchMeetingBtn = $('dispatchMeetingBtn'),
        meetingHistory = $('meetingHistory');
  let lastMeetingResult = null;
  meetingFile.addEventListener('change', () => {
    const f = meetingFile.files && meetingFile.files[0];
    processMeetingBtn.disabled = !f;
    meetingStatus.textContent = f ? 'Ready: ' + f.name : 'Upload an audio file to process.';
    meetingResults.style.display = 'none';
  });
  processMeetingBtn.addEventListener('click', processMeeting);
  async function processMeeting() {
    const file = meetingFile.files && meetingFile.files[0];
    if (!file) return;
    processMeetingBtn.disabled = true;
    meetingProgress.style.width = '5%';
    meetingStatus.textContent = 'Uploading…';
    meetingResults.style.display = 'none';
    const form = new FormData();
    form.append('audio', file, file.name || 'meeting.webm');
    form.append('summarize', 'true');
    const maxSpk = $('meetingMaxSpeakers').value; if (maxSpk) form.append('max_speakers', maxSpk);
    try {
      const res = await api('/meeting/process', { method: 'POST', body: form });
      const init = await res.json();
      if (!res.ok) throw new Error(init.detail || 'Upload failed');
      const taskId = init.task_id;
      let done = false;
      while (!done) {
        await new Promise(r => setTimeout(r, 1500));
        const t = await (await api('/meeting/status/' + taskId)).json();
        if (!t || t.status === 'error') throw new Error((t && t.error) || 'Processing failed');
        meetingProgress.style.width = Math.max(5, t.progress_pct || 0) + '%';
        meetingStatus.textContent = t.step || 'Processing…';
        if (t.status === 'completed' && t.result) {
          done = true;
          const data = t.result;
          lastMeetingResult = data;
          meetingProgress.style.width = '100%';
          meetingStatus.textContent = 'Done. ' + (data.segments || []).length + ' segments, ' + (data.num_speakers || 0) + ' speaker(s).';
          renderMeeting(data);
          meetingResults.style.display = '';
          dispatchMeetingBtn.disabled = !(autoDispatchOn() && data.transcript);
        }
      }
    } catch (err) { meetingStatus.textContent = 'Error: ' + err.message; meetingProgress.style.width = '0%'; }
    finally { processMeetingBtn.disabled = false; }
  }
  function renderMeeting(data) {
    const segs = data.segments || [];
    meetingTranscript.innerHTML = segs.map(s => '<div class="seg"><span class="spk">' + escapeHtml(s.name || ('Speaker ' + ((s.speaker||0)+1))) + '</span> ' + escapeHtml(s.transcript || '(no speech)') + '</div>').join('');
    if (data.summary) { meetingSummary.style.display = ''; summaryText.textContent = data.summary; }
    else meetingSummary.style.display = 'none';
  }
  dispatchMeetingBtn.addEventListener('click', async () => {
    if (!lastMeetingResult) return;
    dispatchMeetingBtn.disabled = true;
    try {
      const r = await api('/dispatch/to-assistx', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ event_type: 'meeting_transcript', text: (lastMeetingResult.transcript || '').slice(0, 500), metadata: { meeting_id: lastMeetingResult.meeting_id, num_speakers: lastMeetingResult.num_speakers }, auto_dispatch: true }) });
      const d = await r.json();
      toast(d.sent ? 'Meeting dispatched to AssistX ✓' : 'Dispatch failed: ' + (d.error || ''));
      if (d.sent) appendDispatchLog('meeting_transcript', true, d);
    } catch (e) { toast('Dispatch error: ' + e.message); }
    finally { dispatchMeetingBtn.disabled = false; }
  });
  async function loadMeetingHistory() {
    meetingHistory.innerHTML = '<div class="hc-row">Loading…</div>';
    try {
      const d = await (await api('/meeting/history?limit=20')).json();
      if (d.error) { meetingHistory.innerHTML = '<div class="hc-row">' + escapeHtml(d.error) + '</div>'; return; }
      const ms = d.meetings || [];
      if (!ms.length) { meetingHistory.innerHTML = '<div class="hc-row">No meetings yet.</div>'; return; }
      meetingHistory.innerHTML = ms.map(m => {
        const date = m.created_at ? new Date(m.created_at).toLocaleDateString() : '?';
        return '<div class="meeting-item" data-id="' + escapeHtml(String(m.id || '')) + '">[' + date + '] ' + m.duration_s + 's · ' + m.num_speakers + ' spk · ' + m.segment_count + ' segs' + (m.has_summary ? ' 📝' : '') + '</div>';
      }).join('');
      meetingHistory.querySelectorAll('.meeting-item').forEach(el => el.addEventListener('click', () => loadMeetingDetail(el.dataset.id)));
    } catch { meetingHistory.innerHTML = '<div class="hc-row">Error loading history.</div>'; }
  }
  async function loadMeetingDetail(id) {
    try {
      const d = await (await api('/meeting/history/' + encodeURIComponent(id))).json();
      if (d.error) { toast(d.error); return; }
      lastMeetingResult = d;
      meetingTranscript.innerHTML = (d.segments || []).map(s => '<div class="seg"><span class="spk">' + escapeHtml(s.speaker || 'Speaker ?') + '</span> ' + escapeHtml(s.transcript || '') + '</div>').join('');
      if (d.summary) { meetingSummary.style.display = ''; summaryText.textContent = d.summary; } else meetingSummary.style.display = 'none';
      meetingResults.style.display = '';
      dispatchMeetingBtn.disabled = !autoDispatchOn();
      document.querySelector('.tab[data-tab="meetings"]').click();
    } catch {}
  }

  // ---- Dispatch ----
  const dispatchUrl = $('dispatchUrl'), dispatchToken = $('dispatchToken'), dispatchEventType = $('dispatchEventType'),
        dispatchAuto = $('dispatchAuto'), dispatchText = $('dispatchText'), dispatchSendBtn = $('dispatchSendBtn'),
        dispatchLog = $('dispatchLog'), executionTrace = $('executionTrace'), autoDispatchToggle = $('autoDispatchToggle'),
        saveDispatchConfigBtn = $('saveDispatchConfigBtn');
  function autoDispatchOn() { return autoDispatchToggle.checked; }
  function loadDispatchConfig() {
    try {
      const cfg = JSON.parse(localStorage.getItem('sophia_dispatch_config') || '{}');
      if (cfg.url) dispatchUrl.value = cfg.url;
      if (cfg.token) dispatchToken.value = cfg.token;
      autoDispatchToggle.checked = cfg.auto_dispatch === true;
      updateAutoPill();
    } catch {}
  }
  function updateAutoPill() { /* reflected in trace */ }
  saveDispatchConfigBtn.addEventListener('click', () => {
    localStorage.setItem('sophia_dispatch_config', JSON.stringify({ url: dispatchUrl.value, token: dispatchToken.value, auto_dispatch: autoDispatchToggle.checked }));
    saveDispatchConfigBtn.textContent = 'Saved!';
    setTimeout(() => saveDispatchConfigBtn.textContent = 'Save Config', 1500);
  });
  async function refreshDispatchStatus() {
    try {
      const d = await (await api('/dispatch/status')).json();
      const p = $('assistxPill');
      if (d.assistx_reachable) { p.className = 'pill ok'; p.textContent = 'assistx: connected'; }
      else { p.className = 'pill bad'; p.textContent = 'assistx: unreachable'; }
    } catch {}
  }
  function appendDispatchLog(type, sent, data) {
    const entry = document.createElement('div');
    entry.className = 'dl-item' + (sent ? '' : ' failed');
    const time = new Date().toLocaleTimeString();
    entry.innerHTML = '<b>' + escapeHtml(type) + '</b> ' + time + ' — ' + (sent ? '✓ sent' : '✗ failed') + (data && data.error ? ' <span style="color:#fda4af;">' + escapeHtml(data.error) + '</span>' : '');
    dispatchLog.prepend(entry);
  }
  dispatchSendBtn.addEventListener('click', async () => {
    const payload = { event_type: dispatchEventType.value, text: dispatchText.value, metadata: {}, auto_dispatch: dispatchAuto.value === 'true', target_url: dispatchUrl.value, target_token: dispatchToken.value };
    dispatchSendBtn.disabled = true;
    try {
      const r = await api('/dispatch/to-assistx', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const d = await r.json();
      appendDispatchLog(dispatchEventType.value, d.sent, d);
      if (d.sent) renderExecutionTrace(d);
    } catch (e) { appendDispatchLog(dispatchEventType.value, false, { error: e.message }); }
    finally { dispatchSendBtn.disabled = false; }
  });
  function renderExecutionTrace(ack) {
    let html = 'Dispatch: ' + (ack.sent ? 'accepted' : 'failed');
    if (ack.correlation_id) html += '<br>correlation_id: ' + escapeHtml(ack.correlation_id);
    if (ack.task_id) html += '<br>task_id: ' + escapeHtml(String(ack.task_id));
    if (ack.intent_id) html += '<br>intent_id: ' + escapeHtml(String(ack.intent_id));
    executionTrace.innerHTML = html;
    if (ack.correlation_id && ack.sent) pollDispatchTrace(ack.correlation_id);
  }
  async function pollDispatchTrace(cid) {
    for (let i = 0; i < 4; i++) {
      await new Promise(r => setTimeout(r, 1500));
      try {
        const d = await (await api('/dispatch/trace/' + encodeURIComponent(cid))).json();
        if (d.error) return;
        let html = 'State: ' + escapeHtml(d.current_state || 'unknown');
        (d.events || []).forEach(e => { html += '<br>· ' + escapeHtml(e.event_type || '') + (e.ts ? ' @ ' + new Date(e.ts).toLocaleTimeString() : ''); });
        executionTrace.innerHTML = html;
      } catch {}
    }
  }

  // ---- Live event stream (/events WS) ----
  function connectEvents() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    try {
      const ws = new WebSocket(proto + '//' + location.host + '/events');
      eventSocket = ws;
      ws.onopen = () => { $('liveDot').classList.add('on'); };
      ws.onclose = () => { $('liveDot').classList.remove('on'); setTimeout(() => { if (appEl.style.display !== 'none') connectEvents(); }, 3000); };
      ws.onerror = () => { $('liveDot').classList.remove('on'); };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          const type = msg.type || (msg.payload && msg.payload.event_type);
          if (type === 'voiceprint_owner_override_enrolled' || type === 'ui_voice_auth_verified' || type === 'voiceprint_enrolled') {
            refreshVoiceHealth();
          } else if (type === 'dispatch_to_assistx' || type === 'ui_voice_auth_verified' && false) {
            // no-op; trace polling covers dispatch feedback
          }
        } catch {}
      };
    } catch { /* WS optional */ }
  }

  loadDispatchConfig();
  boot();
})();
</script>
</body>
</html>
"""
