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

    /* Login */
    #login { position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; padding: 20px; background: radial-gradient(1200px 600px at 50% -10%, #16202c, var(--bg)); }
    .login-card { width: min(100%, 380px); background: var(--panel); border: 1px solid var(--border); border-radius: 16px; padding: 28px; box-shadow: 0 24px 60px rgba(0,0,0,.45); }
    .login-card h1 { margin: 0 0 4px; font-size: 24px; letter-spacing: -.02em; }
    .login-card p { margin: 0 0 18px; color: var(--muted); font-size: 13px; }
    .login-card button { width: 100%; background: var(--accent); color: #061014; }
    .login-card button:hover { background: #67c5f0; }
    .login-error { color: var(--red); font-size: 13px; min-height: 18px; margin-top: 10px; }

    /* App shell */
    #app { display: none; height: 100vh; flex-direction: column; }
    header.topbar { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); background: var(--panel); position: sticky; top: 0; z-index: 10; }
    .brand { font-weight: 700; font-size: 18px; letter-spacing: -.02em; }
    .brand span { color: var(--accent); }
    .pills { display: flex; gap: 8px; margin-left: auto; flex-wrap: wrap; }
    .pill { border-radius: 999px; padding: 6px 12px; font-size: 12px; border: 1px solid var(--border-2); background: var(--panel-2); color: var(--muted); white-space: nowrap; }
    .pill.ok { border-color: var(--green); color: #6ee7b7; }
    .pill.bad { border-color: var(--red); color: #fda4af; }
    .pill.warn { border-color: var(--amber); color: #fcd34d; }
    .pill button { padding: 0; margin: 0; background: none; border: 0; color: inherit; font: inherit; min-height: 0; }

    .layout { flex: 1; display: grid; grid-template-columns: 1fr 360px; min-height: 0; }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } #inbox { display: none; } #inbox.show { display: block; } }

    /* Chat */
    .chat { display: flex; flex-direction: column; min-height: 0; }
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
    .composer .mic { background: var(--panel-2); border: 1px solid var(--border-2); color: var(--text); flex: 0 0 auto; min-width: 52px; }
    .composer .mic.rec { background: var(--red); color: #2a0610; border-color: var(--red); }
    .hint { color: var(--muted-2); font-size: 12px; padding: 0 16px 8px; }

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
    .task .t-meta { color: var(--muted-2); font-size: 11px; }

    /* Toast */
    #toast { position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%); background: #1e293b; border: 1px solid var(--border-2); color: var(--text); padding: 10px 16px; border-radius: 10px; font-size: 13px; opacity: 0; transition: opacity .2s; pointer-events: none; z-index: 50; max-width: 90%; }
    #toast.show { opacity: 1; }
    .mobile-tab { display: none; }
    @media (max-width: 900px) {
      .mobile-tab { display: inline-block; }
      .layout { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <!-- Login gate -->
  <div id="login">
    <form class="login-card" id="loginForm">
      <h1>Sophia <span>Console</span></h1>
      <p>Authenticate to access the assistant and task console.</p>
      <label for="pass" style="font-size:12px;color:var(--muted);">Passphrase</label>
      <input id="pass" type="password" autocomplete="current-password" placeholder="enter passphrase" style="margin-top:6px;">
      <button type="submit" id="loginBtn" style="margin-top:14px;">Unlock</button>
      <div class="login-error" id="loginError"></div>
    </form>
  </div>

  <!-- App -->
  <div id="app">
    <header class="topbar">
      <div class="brand">Sophia <span>Console</span></div>
      <div class="pills">
        <div class="pill" id="modelPill">model: …</div>
        <div class="pill" id="assistxPill">assistx: …</div>
        <div class="pill" id="authPill">user: …</div>
        <button class="pill" id="inboxToggle" title="Toggle task inbox">Tasks</button>
        <button class="pill" id="logoutBtn" title="Lock console">Lock</button>
      </div>
    </header>

    <div class="layout">
      <section class="chat">
        <div class="messages" id="messages"></div>
        <div class="hint">Enter to send · Shift+Enter for newline · Ask for anything and tasks are auto-routed to AssistX.</div>
        <div class="composer">
          <button class="mic" id="micBtn" title="Hold to record a voice check" type="button">🎙</button>
          <textarea id="composer" rows="1" placeholder="Message Sophia…"></textarea>
          <button class="send" id="sendBtn" type="button">Send</button>
        </div>
      </section>

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
  const micBtn = $('micBtn');
  const taskList = $('taskList');
  const taskCount = $('taskCount');
  let sessionMeta = {};
  let streaming = false;
  let recorder = null, micStream = null, micChunks = [];

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

  // ---- Boot ----
  async function boot() {
    try {
      const r = await fetch('/auth/session', { credentials: 'same-origin' });
      const data = await r.json();
      if (!data.authenticated) { showLogin(); return; }
      sessionMeta = data;
      enterApp();
    } catch { showLogin(); }
  }

  function showLogin() {
    loginEl.classList.remove('hidden');
    appEl.style.display = 'none';
  }

  async function enterApp() {
    loginEl.classList.add('hidden');
    appEl.style.display = 'flex';
    $('authPill').textContent = 'user: ' + (sessionMeta.user_id || 'scott');
    await refreshStatus();
    addMessage('assistant', "Welcome to Sophia Console. I'm connected to the AssistX auto-router. Ask me anything — if you request a task, I'll extract it and route it to AssistX automatically.");
  }

  async function refreshStatus() {
    try {
      const s = await fetch('/status', { credentials: 'same-origin' }).then(r => r.json());
      const llm = s.llm || {};
      $('modelPill').textContent = 'model: ' + (llm.intent_model || llm.model || 'mock');
    } catch {}
    try {
      const d = await fetch('/dispatch/status', { credentials: 'same-origin' }).then(r => r.json());
      const p = $('assistxPill');
      if (d.assistx_reachable) {
        p.className = 'pill ok';
        p.textContent = 'assistx: connected';
      } else {
        p.className = 'pill bad';
        p.textContent = 'assistx: unreachable';
      }
    } catch {
      const p = $('assistxPill'); p.className = 'pill warn'; p.textContent = 'assistx: unknown';
    }
  }

  // ---- Login ----
  $('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('loginBtn');
    btn.disabled = true;
    $('loginError').textContent = '';
    try {
      const r = await fetch('/auth/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passphrase: $('pass').value }),
      });
      if (r.ok) { location.reload(); }
      else { $('loginError').textContent = 'Invalid passphrase.'; }
    } catch { $('loginError').textContent = 'Login request failed.'; }
    finally { btn.disabled = false; }
  });

  $('logoutBtn').addEventListener('click', async () => {
    await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' });
    location.reload();
  });

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

  async function sendMessage() {
    const text = composer.value.trim();
    if (!text || streaming) return;
    composer.value = '';
    composer.style.height = 'auto';
    addMessage('user', text);
    pushHistory('user', text);

    const assistantEl = addMessage('assistant', '', true);
    let full = '';
    streaming = true;
    sendBtn.disabled = true;

    const messages_payload = [
      { role: 'system', content: 'You are Sophia, a concise and proactive personal assistant connected to the AssistX auto-router. Answer clearly and, when the user asks for something to be done, make the request explicit so it can be turned into a task.' },
      ...history.slice(0, -1),
      { role: 'user', content: text },
    ];

    try {
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: messages_payload, session_id: 'console' }),
      });
      if (res.status === 401) { toast('Session expired — please log in again.'); showLogin(); return; }
      if (!res.ok || !res.body) { throw new Error('stream failed'); }

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
          let evt;
          try { evt = JSON.parse(json); } catch { continue; }
          handleEvent(evt, assistantEl);
        }
      }
      // flush remainder
      if (buffer.startsWith('data:')) {
        try { handleEvent(JSON.parse(buffer.slice(5).trim()), assistantEl); } catch {}
      }
    } catch (err) {
      assistantEl.innerHTML = escapeHtml('⚠ Could not reach the assistant: ' + err.message);
    } finally {
      streaming = false;
      sendBtn.disabled = false;
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
    } else if (evt.type === 'error') {
      assistantEl.querySelector('.caret')?.remove();
      assistantEl.innerHTML = escapeHtml('⚠ ' + (evt.error || 'error'));
    } else if (evt.type === 'done') {
      assistantEl.querySelector('.caret')?.remove();
    }
  }

  function refreshTaskCount() {
    taskCount.textContent = String(taskList.querySelectorAll('.task').length);
  }

  function addTaskCard(result) {
    const task = result.task || {};
    const dispatch = result.dispatch || {};
    const card = document.createElement('div');
    card.className = 'task';
    const prio = (task.priority || 'medium').toLowerCase();
    const title = escapeHtml(task.title || 'Untitled task');
    const desc = escapeHtml(task.description || '');
    const statusBadge = dispatch.sent
      ? '<span class="badge ingested">ingested ✓</span>'
      : '<span class="badge failed">failed</span>';
    const meta = dispatch.task_id
      ? '<span class="t-meta">assistx task_id: ' + escapeHtml(String(dispatch.task_id)) + '</span>'
      : (dispatch.error ? '<span class="t-meta" style="color:#fda4af;">' + escapeHtml(dispatch.error) + '</span>' : '');
    card.innerHTML =
      '<div class="t-head"><span class="prio ' + prio + '">' + prio + '</span><span class="t-title">' + title + '</span></div>' +
      (desc ? '<div class="t-desc">' + desc + '</div>' : '') +
      '<div class="t-status"><span class="badge extracted">extracted</span>' + statusBadge + meta + '</div>';
    const empty = taskList.querySelector('div');
    if (empty && taskList.children.length === 1 && empty.style.color) empty.remove();
    taskList.prepend(card);
  }

  sendBtn.addEventListener('click', sendMessage);
  composer.addEventListener('input', () => { composer.style.height = 'auto'; composer.style.height = Math.min(140, composer.scrollHeight) + 'px'; });
  composer.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });

  // ---- Optional voice identity check ----
  micBtn.addEventListener('mousedown', startVoiceCheck);
  micBtn.addEventListener('mouseup', stopVoiceCheck);
  micBtn.addEventListener('mouseleave', stopVoiceCheck);
  micBtn.addEventListener('touchstart', (e) => { e.preventDefault(); startVoiceCheck(); });
  micBtn.addEventListener('touchend', (e) => { e.preventDefault(); stopVoiceCheck(); });

  async function startVoiceCheck() {
    if (!navigator.mediaDevices || !window.MediaRecorder) { toast('Voice check needs a secure context + mic.'); return; }
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorder = new MediaRecorder(micStream);
      micChunks = [];
      recorder.ondataavailable = (ev) => { if (ev.data.size) micChunks.push(ev.data); };
      recorder.start();
      micBtn.classList.add('rec');
      micBtn.textContent = '●';
    } catch { toast('Microphone blocked.'); }
  }

  async function stopVoiceCheck() {
    if (!recorder || recorder.state === 'inactive') return;
    micBtn.classList.remove('rec');
    micBtn.textContent = '🎙';
    const stopped = new Promise((res) => recorder.onstop = res);
    recorder.stop();
    await stopped;
    if (micStream) micStream.getTracks().forEach(t => t.stop());
    const blob = new Blob(micChunks, { type: recorder.mimeType || 'audio/webm' });
    if (!blob.size) return;
    const form = new FormData();
    form.append('audio', blob, 'voice-check.webm');
    form.append('user_id', sessionMeta.user_id || 'scott');
    form.append('session_id', 'console');
    toast('Verifying voice identity…');
    try {
      const r = await fetch('/auth/verify', { method: 'POST', body: form, credentials: 'same-origin' });
      const d = await r.json();
      const pct = ((d.score || 0) * 100).toFixed(0);
      toast((d.accepted ? 'Voice accepted ' : 'Voice rejected ') + pct + '%', 3200);
    } catch { toast('Voice check failed.'); }
  }

  boot();
})();
</script>
</body>
</html>
"""
