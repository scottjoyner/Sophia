  const enrollBtn = document.getElementById('enrollBtn');
  const clearBtn = document.getElementById('clearBtn');
  const statusEl = document.getElementById('status');
  const saveStatus = document.getElementById('saveStatus');
  const transcriptEl = document.getElementById('transcript');
  const preview = document.getElementById('preview');
  const levelEl = document.getElementById('level');
  const latest = document.getElementById('latest');
  const graph = document.getElementById('graph');
  const authStatus = document.getElementById('authStatus');
  const authResult = document.getElementById('authResult');
  const authScore = document.getElementById('authScore');
  const authAccepted = document.getElementById('authAccepted');
  const enrollCount = document.getElementById('enrollCount');
  const captureCount = document.getElementById('captureCount');
  const userId = document.getElementById('userId');
  const sessionId = document.getElementById('sessionId');
  const activityContext = document.getElementById('activityContext');
  const fallback = document.getElementById('fallback');
  const audioFile = document.getElementById('audioFile');
  const adminKey = document.getElementById('adminKey');
  const voiceprintBtn = document.getElementById('voiceprintBtn');
  const clearKeyBtn = document.getElementById('clearKeyBtn');
  const voiceprintStatus = document.getElementById('voiceprintStatus');
  const agentModeBtn = document.getElementById('agentModeBtn');
  const meetingModeBtn = document.getElementById('meetingModeBtn');
  const agentMode = document.getElementById('agentMode');
  const meetingMode = document.getElementById('meetingMode');
  const meetingFile = document.getElementById('meetingFile');
  const processMeetingBtn = document.getElementById('processMeetingBtn');
  const meetingStatus = document.getElementById('meetingStatus');
  const meetingProgress = document.getElementById('meetingProgress');
  const meetingResults = document.getElementById('meetingResults');
  const meetingMeta = document.getElementById('meetingMeta');
  const meetingTranscript = document.getElementById('meetingTranscript');
  const meetingSummary = document.getElementById('meetingSummary');
  const summaryText = document.getElementById('summaryText');
  const dispatchModeBtn = document.getElementById('dispatchModeBtn');
  const dispatchMode = document.getElementById('dispatchMode');
  const assistxPill = document.getElementById('assistxPill');
  const dispatchUrl = document.getElementById('dispatchUrl');
  const dispatchToken = document.getElementById('dispatchToken');
  const dispatchEventType = document.getElementById('dispatchEventType');
  const dispatchAuto = document.getElementById('dispatchAuto');
  const dispatchText = document.getElementById('dispatchText');
  const dispatchMeta = document.getElementById('dispatchMeta');
  const dispatchSendBtn = document.getElementById('dispatchSendBtn');
  const dispatchLog = document.getElementById('dispatchLog');
  const dispatchAuthBtn = document.getElementById('dispatchAuthBtn');
  const dispatchMeetingBtn = document.getElementById('dispatchMeetingBtn');
  const downloadTranscriptBtn = document.getElementById('downloadTranscriptBtn');
  const meetingHistoryList = document.getElementById('meetingHistoryList');
  const meetingHistoryStatus = document.getElementById('meetingHistoryStatus');
  const speakerList = document.getElementById('speakerList');
  const refreshSpeakersBtn = document.getElementById('refreshSpeakersBtn');
  const autoDispatchToggle = document.getElementById('autoDispatchToggle');
  const autoDispatchPill = document.getElementById('autoDispatchPill');
  const saveDispatchConfigBtn = document.getElementById('saveDispatchConfigBtn');

  let lastScore = null, lastAccepted = null;

  let recorder, stream, chunks = [], blob = null, selectedFile = null, startedAt = 0, recognition = null;
  let audioCtx, analyser, raf, wavSource, wavProcessor, wavGain;
  let wavBuffers = [], wavSampleRate = 0, wavBlob = null;
  let cachedContext = null;
  let lastAuthResult = null, lastMeetingResult = null;

  function getDeviceId() {
    const key = 'sophia_device_id';
    let value = localStorage.getItem(key);
    if (!value) {
      value = (crypto.randomUUID && crypto.randomUUID()) || ('device-' + Date.now() + '-' + Math.random().toString(16).slice(2));
      localStorage.setItem(key, value);
    }
    return value;
  }

  async function sha256(text) {
    if (!crypto.subtle) return btoa(text).slice(0, 64);
    const bytes = new TextEncoder().encode(text);
    const hash = await crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  function getPosition() {
    return new Promise(resolve => {
      if (!navigator.geolocation) return resolve({});
      navigator.geolocation.getCurrentPosition(
        pos => resolve({
          location_lat: pos.coords.latitude,
          location_lng: pos.coords.longitude,
          location_accuracy_m: pos.coords.accuracy
        }),
        () => resolve({}),
        { enableHighAccuracy: false, timeout: 3500, maximumAge: 300000 }
      );
    });
  }

  async function collectContext() {
    const base = {
      device_id: getDeviceId(),
      user_agent: navigator.userAgent,
      platform: navigator.platform || '',
      language: navigator.language || '',
      languages: navigator.languages || [],
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
      timezone_offset_minutes: new Date().getTimezoneOffset(),
      screen: { width: screen.width, height: screen.height, pixel_ratio: window.devicePixelRatio || 1 },
      hardware_concurrency: navigator.hardwareConcurrency || null,
      device_memory_gb: navigator.deviceMemory || null,
      online: navigator.onLine,
      visibility_state: document.visibilityState,
      page_url: location.href,
      referrer: document.referrer || '',
      activity_context: activityContext.value || '',
      captured_at: new Date().toISOString()
    };
    const fingerprintSource = JSON.stringify({
      ua: base.user_agent,
      platform: base.platform,
      language: base.language,
      timezone: base.timezone,
      screen: base.screen,
      cores: base.hardware_concurrency,
      memory: base.device_memory_gb
    });
    base.device_fingerprint = await sha256(fingerprintSource);
    Object.assign(base, await getPosition());
    cachedContext = base;
    return base;
  }

  async function refreshGraph() {
    try {
      const res = await fetch('/memory-graph/status');
      const data = await res.json();
      graph.textContent = data.has_password ? 'graph connected: ' + (data.database || 'default') : 'graph needs credentials';
    } catch {
      graph.textContent = 'graph unavailable';
    }
  }

  async function refreshEventLog() {
    try {
      const res = await fetch('/events?limit=50');
      const data = await res.json();
      const evts = (data.events || []).slice(-50);
      const el = document.getElementById('eventLog');
      if (!evts.length) {
        el.innerHTML = '<div style="color:#64748b;">No events yet.</div>';
        return;
      }
      el.innerHTML = evts.map(e => {
        const p = e.payload || {};
        const text = p.text || p.transcript || '';
        const score = p.score !== undefined ? ' score=' + p.score.toFixed(3) : '';
        const accepted = p.accepted !== undefined ? (p.accepted ? '✓' : '✗') : '';
        const info = text ? ' <span style="color:#94a3b8;">' + text.slice(0, 60) + '</span>' : '';
        return '<div style="color:#7dd3fc;">' + e.type + '</div><div style="color:#64748b;margin-left:12px;">' + score + accepted + info + '</div>';
      }).join('');
    } catch {}
  }
  document.getElementById('refreshEventsBtn').onclick = refreshEventLog;

  async function refreshStatus() {
    try {
      const r = await fetch('/status');
      const d = await r.json();
      const llm = d.llm || {};
      const prov = llm.intent_provider || llm.provider || 'unknown';
      const enabled = llm.intent_draft_enabled ? '&#x2705;' : '&#x274C;';
      document.getElementById('llmStatus').innerHTML = 'LLM: <span style="color:#7dd3fc;">' + prov + '</span>'
        + ' <span style="color:#64748b;">model=' + (llm.intent_model || llm.model) + '</span>'
        + ' <span>' + enabled + '</span>';
    } catch {}
  }
  document.getElementById('refreshStatusBtn').onclick = refreshStatus;

  async function testLlm() {
    const btn = document.getElementById('testLlmBtn');
    const result = document.getElementById('llmTestResult');
    btn.disabled = true;
    result.textContent = 'testing...';
    try {
      const r = await fetch('/llm/test', { method: 'POST' });
      const d = await r.json();
      result.innerHTML = d.ok
        ? '<span style="color:#34d399;">&#x2705; ' + d.provider + ' ok</span>'
        : '<span style="color:#fb7185;">&#x274C; ' + (d.error || 'failed') + '</span>';
    } catch (err) {
      result.innerHTML = '<span style="color:#fb7185;">&#x274C; ' + err.message + '</span>';
    } finally {
      btn.disabled = false;
    }
  }
  document.getElementById('testLlmBtn').onclick = testLlm;

  function hasLiveMic() {
    const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    return Boolean((window.isSecureContext || isLocal) && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
  }

  function setAuthPill(state, text) {
    authStatus.className = 'pill auth ' + state;
    authStatus.textContent = text;
  }

  function showAuthResult(score, accepted, deviceLabel) {
    authResult.classList.remove('hidden');
    authScore.textContent = (score * 100).toFixed(0) + '%';
    authAccepted.textContent = accepted ? 'ACCEPTED' : 'REJECTED';
    authAccepted.className = accepted ? 'auth-accepted pass' : 'auth-accepted fail';
    const devEl = document.getElementById('authDevice');
    if (devEl) {
      devEl.textContent = deviceLabel ? 'via ' + deviceLabel : '';
      devEl.style.display = deviceLabel ? '' : 'none';
    }
  }

  async function verifyVoice() {
    const audioSrc = selectedFile || blob;
    if (!audioSrc) { return; }
    const form = new FormData();
    form.append('audio', audioSrc, audioSrc.name || 'capture.webm');
    form.append('user_id', userId.value || 'default');
    saveStatus.textContent = 'Verifying voice...';
    setAuthPill('enrolling', 'checking...');
    try {
      const res = await fetch('/auth/verify', { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Verify failed');
      
      lastScore = data.score;
      lastAccepted = data.accepted;
      lastAuthResult = { userId: userId.value, score: data.score, accepted: data.accepted, device_id: data.device_id };

      const matchedDevice = data.device_id && data.device_id !== 'default' ? ' [' + data.device_id + ']' : '';
      showAuthResult(data.score, data.accepted, matchedDevice);
      
      const msg = data.accepted
        ? 'Voice verified ✓ score=' + data.score.toFixed(4) + matchedDevice
        : 'Voice rejected ✗ score=' + data.score.toFixed(4) + ' — you can still Force Enroll if this is truly you';
      saveStatus.textContent = msg;
      
      enrollBtn.disabled = false;
      if (data.accepted) {
          enrollBtn.className = 'warning';
          enrollBtn.textContent = '⬆ Enroll Voice';
      } else {
          enrollBtn.className = 'danger';
          enrollBtn.textContent = '⚠ Force Enroll';
      }

      if (data.accepted && autoDispatchToggle.checked) {
        saveStatus.textContent = msg + ' — dispatching to AssistX...';
        const result = await autoDispatch('voice_auth',
          'Voice auth: user=' + userId.value + ' score=' + (data.score * 100).toFixed(0) + '%',
          { score: data.score, accepted: data.accepted, userId: userId.value, device_id: data.device_id }
        );
        saveStatus.textContent = msg + (result.sent ? ' — dispatched ✓' : ' — dispatch failed');
      }
    } catch (err) {
      setAuthPill('fail', 'error');
      saveStatus.textContent = 'Verify error: ' + err.message;
    }
  }

  async function enrollVoice() {
    const audioSrc = selectedFile || blob;
    if (!audioSrc) { return; }
    
    const isForced = !lastAccepted;
    if (isForced && !confirm('Voice verification failed. Are you sure you want to FORCE enroll this sample? Use this only for training data improvements.')) {
        return;
    }

    const form = new FormData();
    form.append('audio', audioSrc, audioSrc.name || 'capture.webm');
    form.append('user_id', userId.value || 'default');
    form.append('force', isForced ? 'true' : 'false');
    const devId = document.getElementById('deviceId').value.trim();
    if (devId) form.append('device_id', devId);
    saveStatus.textContent = isForced ? 'Force enrolling voice sample...' : 'Enrolling voice sample...';
    setAuthPill('enrolling', 'enrolling...');
    try {
      const res = await fetch('/voiceprints/enroll', { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Enroll failed');
      const label = data.device_id && data.device_id !== 'default' ? ' (' + data.device_id + ')' : '';
      setAuthPill('pass', 'enrolled' + label);
      enrollCount.textContent = data.sample_count + ' samples enrolled' + label;
      saveStatus.textContent = (isForced ? 'Forced enrollment successful ✓ ' : 'Voice enrolled ✓ ') + '(' + data.sample_count + ' total samples)' + label;
      loadSpeakers();
    } catch (err) {
      setAuthPill('fail', 'error');
      saveStatus.textContent = 'Enroll error: ' + err.message;
    }
  }

  function enableFallback(message) {
    fallback.classList.add('active');
    startBtn.disabled = true;
    stopBtn.disabled = true;
    if (message) statusEl.textContent = message;
  }

  function updateActionButtons() {
    const hasAudio = Boolean(blob || selectedFile || wavBlob);
    saveBtn.disabled = !hasAudio && !transcriptEl.value.trim();
    voiceprintBtn.disabled = !hasAudio;
  }

  function makeRecognition() {
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Ctor) return null;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-US';
    rec.onresult = (event) => {
      let finalText = '';
      let interim = '';
      for (let i = 0; i < event.results.length; i++) {
        const text = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += text + ' ';
        else interim += text;
      }
      transcriptEl.value = (finalText + interim).trim();
      updateActionButtons();
    };
    rec.onerror = () => { statusEl.textContent = 'Browser transcription paused; audio is still recording.'; };
    return rec;
  }

  function startMeter(inputStream) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioCtx.createMediaStreamSource(inputStream);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    const tick = () => {
      analyser.getByteFrequencyData(data);
      const avg = data.reduce((a, b) => a + b, 0) / data.length;
      levelEl.style.width = Math.min(100, avg * 1.6) + '%';
      raf = requestAnimationFrame(tick);
    };
    tick();
  }

  function startWavCapture(inputStream) {
    if (!audioCtx) return;
    wavBuffers = [];
    wavBlob = null;
    wavSampleRate = audioCtx.sampleRate;
    wavSource = audioCtx.createMediaStreamSource(inputStream);
    wavProcessor = audioCtx.createScriptProcessor(4096, 1, 1);
    wavGain = audioCtx.createGain();
    wavGain.gain.value = 0;
    wavProcessor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      wavBuffers.push(new Float32Array(input));
    };
    wavSource.connect(wavProcessor);
    wavProcessor.connect(wavGain);
    wavGain.connect(audioCtx.destination);
  }

  function stopWavCapture() {
    try { wavProcessor && wavProcessor.disconnect(); } catch {}
    try { wavSource && wavSource.disconnect(); } catch {}
    try { wavGain && wavGain.disconnect(); } catch {}
    wavProcessor = null;
    wavSource = null;
    wavGain = null;
    if (wavBuffers.length && wavSampleRate) wavBlob = encodeWav(wavBuffers, wavSampleRate);
  }

  function encodeWav(buffers, sampleRate) {
    const length = buffers.reduce((total, buf) => total + buf.length, 0);
    const samples = new Float32Array(length);
    let offset = 0;
    buffers.forEach(buf => { samples.set(buf, offset); offset += buf.length; });
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    function writeString(pos, value) {
      for (let i = 0; i < value.length; i++) view.setUint8(pos + i, value.charCodeAt(i));
    }
    writeString(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    let pos = 44;
    for (let i = 0; i < samples.length; i++, pos += 2) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(pos, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([view], { type: 'audio/wav' });
  }

  async function start() {
    chunks = [];
    blob = null;
    selectedFile = null;
    wavBlob = null;
    updateActionButtons();
    saveStatus.textContent = '';
    if (!hasLiveMic()) {
      enableFallback('Live microphone recording needs HTTPS on iOS Safari. Use the audio file picker below to record or upload.');
      return;
    }
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    startMeter(stream);
    startWavCapture(stream);
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
    recorder.onstop = () => {
      blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
      preview.src = URL.createObjectURL(blob);
      preview.hidden = false;
      updateActionButtons();
    };
    recognition = makeRecognition();
    try { recognition && recognition.start(); } catch {}
    recorder.start();
    startedAt = Date.now();
    startBtn.disabled = true;
    stopBtn.disabled = false;
    statusEl.textContent = 'Recording...';
  }

  function stop() {
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    if (recognition) { try { recognition.stop(); } catch {} }
    stopWavCapture();
    if (stream) stream.getTracks().forEach(t => t.stop());
    if (raf) cancelAnimationFrame(raf);
    if (audioCtx) audioCtx.close();
    levelEl.style.width = '0%';
    startBtn.disabled = false;
    stopBtn.disabled = true;
    updateActionButtons();
    statusEl.textContent = 'Recording stopped. Review transcript, then save or append to voiceprint.';
  }

  async function save() {
    const form = new FormData();
    if (selectedFile) form.append('audio', selectedFile, selectedFile.name || 'ios-capture');
    else if (blob) form.append('audio', blob, 'capture.webm');
    form.append('transcript', transcriptEl.value || '');
    form.append('user_id', userId.value || 'default');
    form.append('session_id', sessionId.value || 'mobile');
    form.append('duration_ms', String(startedAt ? Date.now() - startedAt : 0));
    const context = await collectContext();
    form.append('device_id', context.device_id || '');
    form.append('device_fingerprint', context.device_fingerprint || '');
    form.append('client_context', JSON.stringify(context));
    form.append('activity_context', activityContext.value || '');
    if (context.location_lat != null) form.append('location_lat', String(context.location_lat));
    if (context.location_lng != null) form.append('location_lng', String(context.location_lng));
    if (context.location_accuracy_m != null) form.append('location_accuracy_m', String(context.location_accuracy_m));
    saveStatus.textContent = 'Saving...';
    const res = await fetch('/capture', { method: 'POST', body: form });
    const data = await res.json();
    latest.textContent = JSON.stringify(data, null, 2);
    if (!res.ok) throw new Error(data.detail || 'Capture failed');
    const count = parseInt(localStorage.getItem('sophia_capture_count') || '0') + 1;
    localStorage.setItem('sophia_capture_count', String(count));
    captureCount.textContent = count + ' capture' + (count !== 1 ? 's' : '') + ' saved';
    saveStatus.textContent = data.graph_saved ? 'Saved to sidecar and Neo4j.' : 'Saved locally; graph not written.';
  }

  async function appendVoiceprint() {
    const key = adminKey.value || '';
    if (!key.trim()) throw new Error('Admin enrollment key is required.');
    const form = new FormData();
    if (selectedFile) form.append('audio', selectedFile, selectedFile.name || 'owner-admin-upload.wav');
    else if (wavBlob) form.append('audio', wavBlob, 'owner-admin-recording.wav');
    else if (blob) form.append('audio', blob, 'owner-admin-recording.webm');
    else throw new Error('Record or upload an audio clip first.');
    form.append('user_id', userId.value || 'scott');
    form.append('session_id', sessionId.value || 'mobile');
    form.append('admin_key', key);
    form.append('transcript', transcriptEl.value || '');
    form.append('activity_context', activityContext.value || '');
    const context = cachedContext || await collectContext();
    form.append('device_id', context.device_id || '');
    form.append('device_fingerprint', context.device_fingerprint || '');
    form.append('client_context', JSON.stringify(context));
    voiceprintStatus.textContent = 'Appending voiceprint sample...';
    const res = await fetch('/voiceprints/owner-override-enroll', { method: 'POST', body: form });
    const data = await res.json();
    latest.textContent = JSON.stringify(data, null, 2);
    if (!res.ok) throw new Error(data.detail || 'Voiceprint append failed');
    voiceprintStatus.textContent = 'Voiceprint updated. Run a held-out verification clip next.';
  }

  startBtn.onclick = () => start().catch(err => {
    enableFallback('Microphone unavailable here: ' + (err && err.message ? err.message : 'browser blocked live capture'));
  });
  stopBtn.onclick = stop;
  saveBtn.onclick = () => save().catch(err => { saveStatus.textContent = err.message; });
  voiceprintBtn.onclick = () => appendVoiceprint().catch(err => { voiceprintStatus.textContent = err.message; });
  verifyBtn.onclick = () => verifyVoice();
  enrollBtn.onclick = () => enrollVoice();
  transcriptEl.oninput = updateActionButtons;
  audioFile.onchange = () => {
    selectedFile = audioFile.files && audioFile.files[0] ? audioFile.files[0] : null;
    blob = null;
    wavBlob = null;
    if (selectedFile) {
      preview.src = URL.createObjectURL(selectedFile);
      preview.hidden = false;
      updateActionButtons();
      verifyBtn.disabled = false;
      enrollBtn.disabled = true;
      authResult.classList.add('hidden');
      startedAt = Date.now();
      statusEl.textContent = 'Audio selected. Add or edit transcript, then save or append to voiceprint.';
    }
  };
  clearKeyBtn.onclick = () => {
    adminKey.value = '';
    voiceprintStatus.textContent = '';
  };
  clearBtn.onclick = () => {
    transcriptEl.value = '';
    activityContext.value = '';
    preview.hidden = true;
    blob = null;
    selectedFile = null;
    wavBlob = null;
    audioFile.value = '';
    updateActionButtons();
    verifyBtn.disabled = true;
    enrollBtn.disabled = true;
    authResult.classList.add('hidden');
    latest.textContent = '{}';
  };

  function switchMode(mode) {
    [agentMode, meetingMode, dispatchMode].forEach(el => el.style.display = 'none');
    [agentModeBtn, meetingModeBtn, dispatchModeBtn].forEach(el => el.classList.remove('active'));
    if (mode === 'meeting') {
      meetingMode.style.display = '';
      meetingModeBtn.classList.add('active');
      loadMeetingHistory();
    } else if (mode === 'dispatch') {
      dispatchMode.style.display = '';
      dispatchModeBtn.classList.add('active');
      refreshDispatchStatus();
    } else {
      agentMode.style.display = '';
      agentModeBtn.classList.add('active');
    }
  }
  agentModeBtn.onclick = () => switchMode('agent');
  meetingModeBtn.onclick = () => switchMode('meeting');
  dispatchModeBtn.onclick = () => switchMode('dispatch');

  meetingFile.onchange = () => {
    processMeetingBtn.disabled = !(meetingFile.files && meetingFile.files[0]);
    meetingStatus.textContent = meetingFile.files && meetingFile.files[0]
      ? 'Ready to process: ' + meetingFile.files[0].name
      : 'Upload an audio file to process.';
    meetingResults.style.display = 'none';
  };

  async function processMeeting() {
    const file = meetingFile.files && meetingFile.files[0];
    if (!file) return;
    processMeetingBtn.disabled = true;
    meetingProgress.style.width = '5%';
    meetingStatus.textContent = 'Uploading...';
    meetingResults.style.display = 'none';
    const form = new FormData();
    form.append('audio', file, file.name || 'meeting.webm');
    form.append('summarize', 'true');
    const maxSpk = document.getElementById('meetingMaxSpeakers').value;
    if (maxSpk) form.append('max_speakers', maxSpk);
    try {
      const res = await fetch('/meeting/process', { method: 'POST', body: form });
      const init = await res.json();
      if (!res.ok) throw new Error(init.detail || 'Upload failed');
      const taskId = init.task_id;
      meetingStatus.textContent = 'Processing...';
      let done = false;
      while (!done) {
        await new Promise(r => setTimeout(r, 1500));
        const sr = await fetch('/meeting/status/' + taskId);
        const t = await sr.json();
