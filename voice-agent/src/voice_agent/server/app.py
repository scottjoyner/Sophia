from __future__ import annotations

import hmac
import json
import uuid
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..auth.enroll import EnrollmentError, enroll_from_files
from ..auth.neo4j_ingest import collect_audio_paths_from_neo4j, save_capture_to_neo4j
from ..config import AppConfig, load_config
from ..llm.intent import detect_voice_intent, intent_from_model_payload
from ..llm.openai_compat_provider import OpenAICompatProvider
from ..util.audio import b64decode
from ..util.time import now_ms
from .events import EventBus, event_to_dict
from .protocols import build_protocol_adapter
from .session_manager import SessionManager


class IntentRequest(BaseModel):
    transcript: str


class VoiceChatRequest(BaseModel):
    transcript: str
    session_id: str = "text"
    user_id: str = "default"


class Neo4jEnrollRequest(BaseModel):
    neo4j_uri: str | None = None
    neo4j_user: str | None = None
    neo4j_pass: str | None = None
    neo4j_database: str | None = None
    user_id: str
    speaker_node_id: str | None = None
    speaker_name: str | None = None
    limit: int = 200


CAPTURE_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Sophia Capture</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #0b0f14; color: #f4f7fb; }
    main { width: min(100%, 760px); margin: 0 auto; padding: max(18px, env(safe-area-inset-top)) 16px 28px; }
    header { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 18px; }
    h1 { margin: 0; font-size: clamp(30px, 10vw, 54px); line-height: .92; letter-spacing: 0; }
    .sub { color: #9aa7b6; margin-top: 8px; font-size: 15px; }
    .pill { border: 1px solid #2e3947; border-radius: 999px; padding: 8px 10px; color: #a8ffcb; background: #102018; font-size: 12px; white-space: nowrap; }
    section { border: 1px solid #253140; border-radius: 8px; padding: 14px; background: #111821; margin: 12px 0; }
    label { display: block; color: #adbac8; font-size: 13px; margin: 0 0 7px; }
    input, textarea { width: 100%; border: 1px solid #334155; border-radius: 8px; background: #071019; color: #f8fafc; padding: 12px; font: inherit; }
    textarea { min-height: 190px; resize: vertical; line-height: 1.45; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }
    button { border: 0; border-radius: 8px; padding: 14px 12px; font: inherit; font-weight: 700; color: #061014; background: #7dd3fc; min-height: 52px; }
    button.secondary { background: #253140; color: #e5edf7; }
    button.danger { background: #fb7185; color: #23060a; }
    button.warn { background: #fbbf24; color: #1f1300; }
    button:disabled { opacity: .45; }
    .fallback { display: none; margin-top: 12px; }
    .fallback.active { display: block; }
    .status { min-height: 22px; color: #9aa7b6; font-size: 14px; margin-top: 10px; }
    .meter { height: 10px; border-radius: 999px; background: #1f2937; overflow: hidden; margin-top: 10px; }
    .meter > div { height: 100%; width: 0%; background: #34d399; transition: width .12s linear; }
    audio { width: 100%; margin-top: 12px; }
    pre { overflow: auto; max-height: 220px; background: #071019; border: 1px solid #253140; border-radius: 8px; padding: 10px; color: #cbd5e1; }
    .hint { color: #9aa7b6; font-size: 13px; line-height: 1.4; margin-top: 8px; }
    @media (max-width: 520px) { .row, .controls { grid-template-columns: 1fr; } header { display: block; } .pill { display: inline-block; margin-top: 12px; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Sophia</h1>
      <div class="sub">Mobile recorder, transcript capture, memory graph ingest, and owner voiceprint recovery.</div>
    </div>
    <div id="graph" class="pill">checking graph...</div>
  </header>

  <section>
    <div class="row">
      <div>
        <label for="userId">Speaker / user id</label>
        <input id="userId" value="scott" autocomplete="username">
      </div>
      <div>
        <label for="sessionId">Session id</label>
        <input id="sessionId" value="mobile">
      </div>
    </div>
    <div class="controls">
      <button id="startBtn">Start recording</button>
      <button id="stopBtn" class="danger" disabled>Stop</button>
    </div>
    <div id="fallback" class="fallback">
      <label for="audioFile">Safari upload / record fallback</label>
      <input id="audioFile" type="file" accept="audio/*,video/*" capture>
    </div>
    <div class="meter"><div id="level"></div></div>
    <div id="status" class="status">Ready.</div>
    <audio id="preview" controls hidden></audio>
  </section>

  <section>
    <label for="transcript">Transcript</label>
    <textarea id="transcript" placeholder="Speak into your phone, or type here if browser transcription is unavailable."></textarea>
    <label for="activityContext">Context</label>
    <input id="activityContext" placeholder="Optional: what are you doing, where are you, why capture this?">
    <div class="controls">
      <button id="saveBtn" disabled>Save capture</button>
      <button id="clearBtn" class="secondary">Clear</button>
    </div>
    <div id="saveStatus" class="status"></div>
  </section>

  <section>
    <label for="adminKey">Admin voiceprint enrollment key</label>
    <input id="adminKey" type="password" autocomplete="off" placeholder="Required only for admin voiceprint enrollment">
    <div class="hint">Use this only for reviewed Scott-only clips. Live browser recordings are converted to WAV before enrollment; uploaded WAV files are preferred when available.</div>
    <div class="controls">
      <button id="voiceprintBtn" class="warn" disabled>Append this clip to owner voiceprint</button>
      <button id="clearKeyBtn" class="secondary">Clear key</button>
    </div>
    <div id="voiceprintStatus" class="status"></div>
  </section>

  <section>
    <label>Latest capture / voiceprint action</label>
    <pre id="latest">{}</pre>
  </section>
</main>
<script>
(() => {
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const saveBtn = document.getElementById('saveBtn');
  const clearBtn = document.getElementById('clearBtn');
  const statusEl = document.getElementById('status');
  const saveStatus = document.getElementById('saveStatus');
  const transcriptEl = document.getElementById('transcript');
  const preview = document.getElementById('preview');
  const levelEl = document.getElementById('level');
  const latest = document.getElementById('latest');
  const graph = document.getElementById('graph');
  const userId = document.getElementById('userId');
  const sessionId = document.getElementById('sessionId');
  const activityContext = document.getElementById('activityContext');
  const fallback = document.getElementById('fallback');
  const audioFile = document.getElementById('audioFile');
  const adminKey = document.getElementById('adminKey');
  const voiceprintBtn = document.getElementById('voiceprintBtn');
  const clearKeyBtn = document.getElementById('clearKeyBtn');
  const voiceprintStatus = document.getElementById('voiceprintStatus');

  let recorder, stream, chunks = [], blob = null, selectedFile = null, startedAt = 0, recognition = null;
  let audioCtx, analyser, raf, wavSource, wavProcessor, wavGain;
  let wavBuffers = [], wavSampleRate = 0, wavBlob = null;
  let cachedContext = null;

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

  function hasLiveMic() {
    return Boolean(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
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
    voiceprintStatus.textContent = '';
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
  transcriptEl.oninput = updateActionButtons;
  audioFile.onchange = () => {
    selectedFile = audioFile.files && audioFile.files[0] ? audioFile.files[0] : null;
    blob = null;
    wavBlob = null;
    if (selectedFile) {
      preview.src = URL.createObjectURL(selectedFile);
      preview.hidden = false;
      updateActionButtons();
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
    latest.textContent = '{}';
  };
  if (!hasLiveMic()) {
    enableFallback(window.isSecureContext
      ? 'Live recording is not available in this browser. Use the upload fallback.'
      : 'Live recording needs HTTPS on iOS Safari. Use the upload fallback, or open the HTTPS endpoint when enabled.');
  }
  refreshGraph();
  updateActionButtons();
})();
</script>
</body>
</html>
"""


def _require_owner_override(config: AppConfig, user_id: str, admin_key: str | None) -> None:
    if user_id != config.auth.owner_user_id:
        raise HTTPException(status_code=403, detail="Owner override can only enroll the configured owner user.")
    if not config.auth.owner_override_enabled:
        raise HTTPException(status_code=403, detail="Owner override enrollment is disabled on this deployment.")
    expected = config.auth.owner_override_token
    if not expected:
        raise HTTPException(status_code=503, detail="Admin enrollment key is not configured on this deployment.")
    if not admin_key or not hmac.compare_digest(str(admin_key), str(expected)):
        raise HTTPException(status_code=403, detail="Invalid admin enrollment key.")


def _safe_upload_suffix(upload: UploadFile, default: str = ".wav") -> str:
    if upload.filename and "." in upload.filename:
        suffix = "." + upload.filename.rsplit(".", 1)[-1].lower()
        if 1 < len(suffix) <= 12:
            return suffix
    return default


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="Sophia Voice Agent", version="0.1.0")
    bus = EventBus()
    manager = SessionManager(
        config,
        Path(config.paths.artifacts_dir),
        event_callback=lambda event_type, payload: bus.publish(event_type, payload),
    )
    protocol = build_protocol_adapter(config.server.protocol)
    app.state.config = config
    app.state.manager = manager
    app.state.events = bus
    intent_provider = None
    if config.llm.intent_provider in {"openai", "hermes"} and config.llm.intent_base_url:
        intent_provider = OpenAICompatProvider(
            config.llm.intent_base_url,
            config.llm.intent_api_key or config.llm.api_key,
            config.llm.intent_model,
        )

    def _detect_intent(transcript: str):
        if not intent_provider:
            return detect_voice_intent(transcript)
        prompt = (
            "Classify this voice transcript for a Hermes voice sidecar. "
            "Return only compact JSON with keys: intent, confidence, transcript, hermes_prompt. "
            "intent must be one of dictation, command, question, chat. "
            "Keep hermes_prompt concise and pass through the user's meaning without adding facts.\n\n"
            f"Transcript: {transcript}"
        )
        try:
            content = intent_provider.complete(prompt).content.strip()
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end >= start:
                content = content[start : end + 1]
            return intent_from_model_payload(transcript, json.loads(content))
        except Exception:
            return detect_voice_intent(transcript)

    def _float_or_none(value: str | None) -> float | None:
        if value in {None, ""}:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _capture_context(
        request: Request,
        *,
        client_context: str,
        device_id: str,
        device_fingerprint: str,
        location_lat: str,
        location_lng: str,
        location_accuracy_m: str,
        activity_context: str,
        detected,
    ) -> Dict[str, Any]:
        try:
            parsed = json.loads(client_context) if client_context else {}
        except json.JSONDecodeError:
            parsed = {"raw_client_context": client_context}
        if not isinstance(parsed, dict):
            parsed = {"raw_client_context": parsed}
        headers = request.headers
        context = {
            **parsed,
            "device_id": device_id or parsed.get("device_id") or "",
            "device_fingerprint": device_fingerprint or parsed.get("device_fingerprint") or "",
            "client_ip": headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else ""),
            "user_agent": headers.get("user-agent", parsed.get("user_agent", "")),
            "language": headers.get("accept-language", parsed.get("language", "")),
            "timezone": parsed.get("timezone", ""),
            "platform": parsed.get("platform", ""),
            "location_lat": _float_or_none(location_lat) if location_lat else parsed.get("location_lat"),
            "location_lng": _float_or_none(location_lng) if location_lng else parsed.get("location_lng"),
            "location_accuracy_m": _float_or_none(location_accuracy_m)
            if location_accuracy_m
            else parsed.get("location_accuracy_m"),
            "activity_context": activity_context or parsed.get("activity_context", ""),
            "intent": detected.name if detected else "",
            "intent_confidence": detected.confidence if detected else None,
            "intent_source": detected.source if detected else "",
        }
        return context

    @app.get("/", response_class=HTMLResponse)
    async def homepage() -> str:
        return CAPTURE_PAGE

    @app.get("/healthz")
    async def healthz() -> Dict[str, Any]:
        return {"ok": True, "service": "sophia-voice-agent"}

    @app.get("/readyz")
    async def readyz() -> Dict[str, Any]:
        artifacts = Path(config.paths.artifacts_dir)
        return {
            "ok": artifacts.exists() and artifacts.is_dir(),
            "protocol": config.server.protocol,
            "artifacts_dir": str(artifacts),
        }

    @app.get("/status")
    async def status() -> Dict[str, Any]:
        return {
            "service": "sophia-voice-agent",
            "protocol": config.server.protocol,
            "sessions": sorted(manager.sessions.keys()),
            "stt": {
                "model_size": config.stt.model_size,
                "compute_type": config.stt.compute_type,
                "cpu_threads": config.stt.cpu_threads,
                "refine_enabled": config.stt.refine_enabled,
            },
            "llm": {
                "provider": config.llm.provider,
                "model": config.llm.model,
                "intent_provider": config.llm.intent_provider,
                "intent_model": config.llm.intent_model,
                "intent_draft_enabled": bool(intent_provider),
            },
            "tts": {"backend": config.tts.backend},
            "memory_graph": {
                "uri": config.neo4j.uri,
                "user": config.neo4j.user,
                "database": config.neo4j.database,
                "has_password": bool(config.neo4j.password),
                "default_speaker_name": config.neo4j.default_speaker_name,
            },
            "voiceprint_override": {
                "owner_user_id": config.auth.owner_user_id,
                "enabled": config.auth.owner_override_enabled,
                "key_configured": bool(config.auth.owner_override_token),
                "min_seconds": config.auth.owner_append_min_seconds,
                "max_seconds": config.auth.owner_append_max_seconds,
            },
            "capture_dir": config.paths.capture_dir or str(Path(config.paths.artifacts_dir) / "captures"),
        }

    @app.get("/memory-graph/status")
    async def memory_graph_status() -> Dict[str, Any]:
        return {
            "uri": config.neo4j.uri,
            "user": config.neo4j.user,
            "database": config.neo4j.database,
            "has_password": bool(config.neo4j.password),
            "default_speaker_name": config.neo4j.default_speaker_name,
        }

    @app.get("/events")
    async def events(after_id: int = 0, session_id: str | None = None) -> Dict[str, Any]:
        records = bus.snapshot(after_id=after_id, session_id=session_id)
        return {"events": [event_to_dict(event) for event in records]}

    @app.post("/intent")
    async def intent(req: IntentRequest) -> Dict[str, Any]:
        detected = _detect_intent(req.transcript)
        return {
            "intent": detected.name,
            "confidence": detected.confidence,
            "source": detected.source,
            "transcript": detected.transcript,
            "hermes_prompt": detected.hermes_prompt,
        }

    @app.post("/voice-chat")
    async def voice_chat(req: VoiceChatRequest) -> Dict[str, Any]:
        detected = _detect_intent(req.transcript)
        intent_payload = {
            "session_id": req.session_id,
            "user_id": req.user_id,
            "intent": detected.name,
            "confidence": detected.confidence,
            "source": detected.source,
            "transcript": detected.transcript,
            "hermes_prompt": detected.hermes_prompt,
        }
        bus.publish("intent_detected", intent_payload)
        answer = manager.pipeline.ralph.run(detected.hermes_prompt)
        output_payload = {"session_id": req.session_id, "user_id": req.user_id, "text": answer}
        bus.publish("llm_output", output_payload)
        return {
            "intent": detected.name,
            "confidence": detected.confidence,
            "source": detected.source,
            "transcript": detected.transcript,
            "hermes_prompt": detected.hermes_prompt,
            "response": answer,
        }

    @app.post("/capture")
    async def capture(
        request: Request,
        audio: UploadFile | None = File(default=None),
        transcript: str = Form(default=""),
        user_id: str = Form(default="default"),
        session_id: str = Form(default="mobile"),
        duration_ms: int = Form(default=0),
        device_id: str = Form(default=""),
        device_fingerprint: str = Form(default=""),
        client_context: str = Form(default=""),
        location_lat: str = Form(default=""),
        location_lng: str = Form(default=""),
        location_accuracy_m: str = Form(default=""),
        activity_context: str = Form(default=""),
    ) -> Dict[str, Any]:
        capture_id = uuid.uuid4().hex
        captures_dir = Path(config.paths.capture_dir or (Path(config.paths.artifacts_dir) / "captures"))
        captures_dir.mkdir(parents=True, exist_ok=True)
        content_type = audio.content_type if audio else "text/plain"
        audio_path = ""
        byte_count = 0
        if audio is not None:
            suffix = _safe_upload_suffix(audio, default=".webm")
            audio_file = captures_dir / f"{capture_id}{suffix}"
            data = await audio.read()
            audio_file.write_bytes(data)
            audio_path = str(audio_file)
            byte_count = len(data)
        transcript_text = " ".join(transcript.strip().split())
        detected = _detect_intent(transcript_text) if transcript_text else None
        context = _capture_context(
            request,
            client_context=client_context,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            location_lat=location_lat,
            location_lng=location_lng,
            location_accuracy_m=location_accuracy_m,
            activity_context=activity_context,
            detected=detected,
        )
        graph_saved = False
        graph_error = None
        if config.neo4j.password:
            try:
                save_capture_to_neo4j(
                    config.neo4j.uri,
                    config.neo4j.user,
                    config.neo4j.password,
                    user_id=user_id,
                    capture_id=capture_id,
                    transcript=transcript_text,
                    audio_path=audio_path,
                    content_type=content_type,
                    database=config.neo4j.database,
                    duration_ms=duration_ms,
                    metadata={"session_id": session_id, "bytes": byte_count},
                    context=context,
                )
                graph_saved = True
            except RuntimeError as exc:
                graph_error = str(exc)
            except Exception as exc:
                graph_error = f"{type(exc).__name__}: {exc}"
        payload = {
            "capture_id": capture_id,
            "session_id": session_id,
            "user_id": user_id,
            "audio_path": audio_path,
            "bytes": byte_count,
            "content_type": content_type,
            "duration_ms": duration_ms,
            "transcript": transcript_text,
            "intent": detected.name if detected else None,
            "confidence": detected.confidence if detected else None,
            "intent_source": detected.source if detected else None,
            "device_id": context.get("device_id"),
            "device_fingerprint": context.get("device_fingerprint"),
            "location": {
                "lat": context.get("location_lat"),
                "lng": context.get("location_lng"),
                "accuracy_m": context.get("location_accuracy_m"),
            },
            "graph_saved": graph_saved,
            "graph_error": graph_error,
            "ts_ms": now_ms(),
        }
        bus.publish("mobile_capture_saved", payload)
        return {"ok": True, **payload}

    @app.post("/voiceprints/owner-override-enroll")
    async def owner_override_enroll(
        request: Request,
        audio: UploadFile = File(...),
        user_id: str = Form(default="scott"),
        session_id: str = Form(default="mobile"),
        admin_key: str = Form(default=""),
        transcript: str = Form(default=""),
        device_id: str = Form(default=""),
        device_fingerprint: str = Form(default=""),
        client_context: str = Form(default=""),
        activity_context: str = Form(default=""),
    ) -> Dict[str, Any]:
        _require_owner_override(config, user_id, admin_key)
        capture_id = uuid.uuid4().hex
        override_dir = Path(config.paths.capture_dir or (Path(config.paths.artifacts_dir) / "captures")) / "voiceprint_override"
        override_dir.mkdir(parents=True, exist_ok=True)
        suffix = _safe_upload_suffix(audio, default=".wav")
        audio_file = override_dir / f"{capture_id}{suffix}"
        data = await audio.read()
        audio_file.write_bytes(data)
        detected = _detect_intent(transcript) if transcript.strip() else None
        context = _capture_context(
            request,
            client_context=client_context,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            location_lat="",
            location_lng="",
            location_accuracy_m="",
            activity_context=activity_context,
            detected=detected,
        )
        try:
            result = enroll_from_files(
                config,
                user_id,
                [str(audio_file)],
                append=True,
                source="ui_owner_override",
                min_seconds=config.auth.owner_append_min_seconds,
                max_seconds=config.auth.owner_append_max_seconds,
            )
        except EnrollmentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
        payload = {
            "session_id": session_id,
            "user_id": user_id,
            "capture_id": capture_id,
            "audio_path": str(audio_file),
            "bytes": len(data),
            "content_type": audio.content_type,
            "device_id": context.get("device_id"),
            "device_fingerprint": context.get("device_fingerprint"),
            "source": "ui_owner_override",
            "ts_ms": now_ms(),
            **result,
        }
        bus.publish("voiceprint_owner_override_enrolled", payload)
        return {"ok": True, **payload}

    @app.post("/voiceprints/train-neo4j")
    async def train_voiceprint_from_neo4j(req: Neo4jEnrollRequest) -> Dict[str, Any]:
        uri = req.neo4j_uri or config.neo4j.uri
        user = req.neo4j_user or config.neo4j.user
        password = req.neo4j_pass or config.neo4j.password
        database = req.neo4j_database or config.neo4j.database
        speaker_name = req.speaker_name or config.neo4j.default_speaker_name
        if not password:
            raise HTTPException(
                status_code=400,
                detail="Neo4j credentials required. Set NEO4J_PASSWORD or include neo4j_pass.",
            )
        try:
            files = collect_audio_paths_from_neo4j(
                uri,
                user,
                password,
                speaker_node_id=req.speaker_node_id,
                speaker_name=speaker_name,
                database=database,
                limit=req.limit,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        if not files:
            raise HTTPException(status_code=404, detail="No audio paths found in Neo4j")
        enroll_from_files(config, req.user_id, files)
        payload = {"user_id": req.user_id, "sample_count": len(files), "source": "neo4j"}
        bus.publish("voiceprint_trained", payload)
        return {"ok": True, **payload}

    @app.websocket("/events")
    async def events_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = bus.subscribe()
        try:
            for event in bus.snapshot():
                await websocket.send_text(json.dumps(event_to_dict(event)))
            while True:
                event = await queue.get()
                await websocket.send_text(json.dumps(event_to_dict(event)))
        except WebSocketDisconnect:
            return
        finally:
            bus.unsubscribe(queue)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_text()
                data: Dict[str, Any] = json.loads(message)
                before_event_id = bus.snapshot()[-1].id if bus.snapshot() else 0
                normalized = protocol.decode(data)
                msg_type = normalized.msg_type
                payload = normalized.payload
                if msg_type == "start_session":
                    manager.start_session(
                        payload["session_id"],
                        payload["sample_rate"],
                        payload.get("channels", 1),
                        payload.get("encoding", "pcm_s16le"),
                        payload.get("user_id", "default"),
                    )
                elif msg_type == "audio_chunk":
                    pcm = b64decode(payload["pcm_bytes"])
                    manager.handle_audio_chunk(
                        payload["session_id"],
                        pcm,
                        payload.get("chunk_ms", config.stt.chunk_ms),
                        payload.get("t_client_ms", 0),
                    )
                elif msg_type == "end_session":
                    manager.end_session(payload["session_id"])
                elif msg_type:
                    raise HTTPException(status_code=400, detail=f"Unsupported message type: {msg_type}")
                await websocket.send_text(json.dumps(protocol.encode("ack", {"received": msg_type})))
                session_id = payload.get("session_id") if isinstance(payload, dict) else None
                for event in bus.snapshot(after_id=before_event_id, session_id=session_id):
                    await websocket.send_text(json.dumps(protocol.encode("event", event_to_dict(event))))
        except WebSocketDisconnect:
            return

    return app


def run() -> None:
    import uvicorn

    config = load_config(None)
    app = create_app(config)
    uvicorn.run(app, host=config.server.host, port=config.server.port)
