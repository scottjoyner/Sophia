from __future__ import annotations

import asyncio
import json
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..auth.enroll import enroll_from_files
from ..auth.neo4j_ingest import collect_audio_paths_from_neo4j, save_capture_to_neo4j
from ..auth.diarization import diarize, identify_speakers
from ..auth.verify import verify_audio_segment
from ..auth.registry import VoiceprintRegistry
from ..auth.speaker_embedder import SpeakerEmbedder
from ..config import AppConfig, load_config
from ..llm.intent import detect_voice_intent, intent_from_model_payload
from ..llm.openai_compat_provider import OpenAICompatProvider
from ..stt.faster_whisper_batch import refine_transcript
from ..util.audio import b64decode, read_wav
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


class DispatchRequest(BaseModel):
    event_type: str = "voice_auth"
    text: str = ""
    metadata: Dict[str, Any] = {}
    auto_dispatch: bool = True
    target_url: str = "http://host.docker.internal:8000"
    target_token: str = ""
    session_id: str | None = "mobile"


CAPTURE_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Sophia Voice</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #0b0f14; color: #f4f7fb; }
    main { width: min(100%, 760px); margin: 0 auto; padding: max(18px, env(safe-area-inset-top)) 16px 28px; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
    .logo h1 { margin: 0; font-size: 28px; letter-spacing: -.02em; }
    .status-bar { display: flex; gap: 8px; flex-wrap: wrap; }
    .pill { border-radius: 999px; padding: 6px 12px; font-size: 12px; white-space: nowrap; border: 1px solid #2e3947; background: #111821; color: #9aa7b6; }
    .pill.graph { color: #a8ffcb; background: #102018; }
    .pill.auth { min-width: 110px; text-align: center; transition: all .3s; }
    .pill.auth.pass { border-color: #34d399; background: #0a2a1a; color: #6ee7b7; }
    .pill.auth.fail { border-color: #fb7185; background: #2a0a14; color: #fda4af; }
    .pill.auth.enrolling { border-color: #fbbf24; background: #1f1a0a; color: #fcd34d; }
    .pill.auth.idle { border-color: #555; color: #9aa7b6; }
    .badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
    .badge.pass { background: #0a2a1a; color: #6ee7b7; }
    .badge.fail { background: #2a0a14; color: #fda4af; }
    section { border: 1px solid #253140; border-radius: 10px; padding: 16px; background: #111821; margin: 12px 0; }
    section.auth-section { border-color: #2e3947; }
    section.capture-section { border-color: #253140; }
    h2 { margin: 0 0 12px; font-size: 15px; font-weight: 600; color: #cbd5e1; display: flex; align-items: center; gap: 8px; }
    h2 .sub { color: #64748b; font-weight: 400; font-size: 13px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    label { display: block; color: #94a3b8; font-size: 12px; margin-bottom: 6px; font-weight: 500; }
    input, textarea { width: 100%; border: 1px solid #334155; border-radius: 8px; background: #071019; color: #f8fafc; padding: 10px 12px; font: inherit; font-size: 14px; }
    input:focus, textarea:focus { outline: none; border-color: #7dd3fc; }
    textarea { min-height: 100px; resize: vertical; line-height: 1.45; }
    .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; }
    .btn-group { display: flex; gap: 8px; flex-wrap: wrap; }
    button { border: 0; border-radius: 8px; padding: 12px 16px; font: inherit; font-weight: 600; font-size: 14px; cursor: pointer; min-height: 48px; flex: 1; transition: opacity .15s, background .15s; }
    button:disabled { opacity: .35; cursor: default; }
    button.primary { background: #7dd3fc; color: #061014; }
    button.primary:hover:not(:disabled) { background: #67c5f0; }
    button.danger { background: #fb7185; color: #23060a; }
    button.danger:hover:not(:disabled) { background: #f05e73; }
    button.secondary { background: #1e293b; color: #e2e8f0; }
    button.secondary:hover:not(:disabled) { background: #29394f; }
    button.success { background: #34d399; color: #052e16; }
    button.success:hover:not(:disabled) { background: #2bc48a; }
    button.warning { background: #fbbf24; color: #1c1900; }
    button.warning:hover:not(:disabled) { background: #f0b51c; }
    .auth-result { display: flex; align-items: center; gap: 12px; padding: 12px; border-radius: 8px; margin-top: 12px; background: #0f172a; min-height: 52px; }
    .auth-result.hidden { display: none; }
    .auth-score { font-size: 28px; font-weight: 700; letter-spacing: -.02em; }
    .auth-score.pass { color: #34d399; }
    .auth-score.fail { color: #fb7185; }
    .auth-label { font-size: 13px; color: #94a3b8; }
    .auth-accepted { font-size: 14px; font-weight: 600; }
    .enroll-count { font-size: 12px; color: #64748b; margin-left: auto; }
    .fallback { display: none; margin-top: 12px; }
    .fallback.active { display: block; }
    .status { min-height: 20px; color: #94a3b8; font-size: 13px; margin-top: 8px; transition: color .2s; }
    .meter { height: 8px; border-radius: 999px; background: #1e293b; overflow: hidden; margin-top: 10px; }
    .meter > div { height: 100%; width: 0%; background: linear-gradient(90deg, #34d399, #7dd3fc); transition: width .12s linear; }
    audio { width: 100%; margin-top: 10px; border-radius: 6px; }
    pre { overflow: auto; max-height: 180px; background: #071019; border: 1px solid #1e293b; border-radius: 8px; padding: 10px; color: #94a3b8; font-size: 12px; margin: 0; }
    .inline-meta { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 8px; font-size: 12px; color: #64748b; }
    .mode-toggle { display: flex; gap: 0; margin-bottom: 16px; border: 1px solid #2e3947; border-radius: 10px; overflow: hidden; background: #111821; }
    .mode-btn { flex: 1; border: 0; border-radius: 0; padding: 10px 16px; font: inherit; font-weight: 600; font-size: 13px; cursor: pointer; background: transparent; color: #64748b; transition: all .15s; min-height: auto; }
    .mode-btn.active { background: #1e293b; color: #e2e8f0; }
    .mode-btn:hover:not(.active) { background: #1a2330; color: #94a3b8; }
    .meeting-segment { padding: 8px 10px; border-left: 3px solid #7dd3fc; margin: 6px 0; background: #0f172a; border-radius: 0 6px 6px 0; font-size: 13px; line-height: 1.45; cursor: pointer; transition: opacity .15s; }
    .meeting-segment.filtered-out { opacity: .25; }
    .meeting-segment .speaker-label { font-weight: 600; color: #7dd3fc; font-size: 12px; margin-bottom: 2px; }
    .meeting-segment .seg-time { color: #64748b; font-size: 11px; margin-left: 8px; }
    .speaker-timeline { display: flex; height: 24px; border-radius: 6px; overflow: hidden; margin: 6px 0; background: #1e293b; cursor: pointer; }
    .speaker-timeline .tl-seg { height: 100%; display: flex; align-items: center; justify-content: center; font-size: 9px; font-weight: 700; color: #000c; transition: opacity .15s; min-width: 4px; }
    .speaker-timeline .tl-seg:hover { opacity: .8; }
    .speaker-filter { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
    .speaker-filter button { flex: 0; min-height: auto; padding: 4px 10px; font-size: 11px; border-radius: 999px; border: 1px solid #334155; background: transparent; color: #94a3b8; cursor: pointer; transition: all .15s; }
    .speaker-filter button:hover { background: #1e293b; }
    .speaker-filter button.active { background: #334155; color: #e2e8f0; border-color: #7dd3fc; }
    .dispatch-entry { padding: 6px 8px; border-left: 3px solid #555; margin: 4px 0; background: #0f172a; border-radius: 0 4px 4px 0; font-size: 12px; line-height: 1.4; }
    .dispatch-entry.sent { border-left-color: #34d399; }
    .dispatch-entry.failed { border-left-color: #fb7185; }
    .dispatch-entry .de-type { font-weight: 600; color: #7dd3fc; font-size: 11px; }
    .dispatch-entry .de-time { color: #64748b; font-size: 10px; margin-left: 8px; }
    .dispatch-entry .de-status { font-size: 11px; }
    .dispatch-entry .de-error { color: #fb7185; font-size: 11px; }
    select { width: 100%; border: 1px solid #334155; border-radius: 8px; background: #071019; color: #f8fafc; padding: 10px 12px; font: inherit; font-size: 14px; }
    @media (max-width: 520px) { .row { grid-template-columns: 1fr; } .controls { grid-template-columns: 1fr; } .btn-group { flex-direction: column; } button { width: 100%; } header { flex-direction: column; align-items: stretch; } }
  </style>
</head>
<body>
<main>
  <header>
    <div class="logo">
      <h1>Sophia</h1>
    </div>
    <div class="status-bar">
      <div id="graph" class="pill graph">graph ...</div>
      <div id="authStatus" class="pill auth idle">voice: idle</div>
    </div>
  </header>

  <div class="mode-toggle">
    <button id="agentModeBtn" class="mode-btn active">&#x1F916; Agent</button>
    <button id="meetingModeBtn" class="mode-btn">&#x1F91D; Meeting</button>
    <button id="dispatchModeBtn" class="mode-btn">&#x1F4E4; Dispatch</button>
  </div>

  <div id="agentMode">
  <section class="auth-section">
    <h2>&#x1F3A4; Voice Authentication <span class="sub">enroll &amp; verify</span></h2>
    <div class="row">
      <div>
        <label for="userId">Speaker</label>
        <input id="userId" value="scott" autocomplete="username">
      </div>
      <div>
        <label for="sessionId">Session</label>
        <input id="sessionId" value="mobile">
      </div>
      <div>
        <label for="deviceId">Device/Mic</label>
        <input id="deviceId" placeholder="phone, headset, etc." style="font-size:13px;padding:8px 10px;">
      </div>
    </div>
    <div class="btn-group">
      <button id="startBtn" class="primary">&#x23FA; Record</button>
      <button id="stopBtn" class="danger" disabled>&#x25A0; Stop</button>
    </div>
    <div id="fallback" class="fallback">
      <label for="audioFile">Upload audio file</label>
      <input id="audioFile" type="file" accept="audio/*,video/*" capture>
    </div>
    <div class="meter"><div id="level"></div></div>
    <div id="status" class="status">Tap Record, then speak naturally.</div>
    <audio id="preview" controls hidden></audio>
    <div id="authResult" class="auth-result hidden">
      <div>
        <div class="auth-label">Voice Match</div>
        <div class="auth-score" id="authScore">--%</div>
      </div>
      <div>
        <div class="auth-label">Status</div>
        <div id="authAccepted" class="auth-accepted">--</div>
      </div>
      <div id="enrollCount" class="enroll-count"></div>
      <div id="authDevice" class="enroll-count" style="font-size:11px;color:#64748b;margin-top:2px;"></div>
    </div>
    <div class="btn-group">
      <button id="verifyBtn" class="secondary" disabled>&#x1F50D; Verify Voice</button>
      <button id="enrollBtn" class="warning" disabled>&#x2B06; Enroll Voice</button>
      <button id="dispatchAuthBtn" class="primary" disabled style="flex:0.6;">&#x1F4E4; Dispatch</button>
    </div>
  </section>

  <section class="capture-section">
    <details style="margin-bottom:8px;">
      <summary style="cursor:pointer;color:#64748b;font-size:12px;font-weight:600;">&#x2699; Settings &amp; Status</summary>
      <div id="settingsPanel" style="padding:8px 0;font-size:12px;color:#94a3b8;">
        <div id="llmStatus"><span style="color:#64748b;">LLM: checking...</span></div>
        <div style="margin-top:6px;">
          <button id="testLlmBtn" class="secondary" style="flex:0;padding:4px 10px;min-height:auto;font-size:11px;">&#x1F50C; Test LLM</button>
          <span id="llmTestResult" style="margin-left:8px;font-size:11px;"></span>
        </div>
        <div style="margin-top:6px;">
          <button id="refreshStatusBtn" class="secondary" style="flex:0;padding:4px 10px;min-height:auto;font-size:11px;">&#x21BB; Refresh Status</button>
        </div>
      </div>
    </details>
    <details style="margin-bottom:8px;">
      <summary style="cursor:pointer;color:#64748b;font-size:12px;font-weight:600;">&#x1F50D; Debug Log</summary>
      <div id="eventLog" style="max-height:200px;overflow-y:auto;background:#071019;border:1px solid #1e293b;border-radius:6px;padding:8px;margin-top:6px;font-size:11px;font-family:monospace;line-height:1.6;">
        <div style="color:#64748b;">No events yet.</div>
      </div>
      <div class="btn-group" style="margin-top:4px;">
        <button id="refreshEventsBtn" class="secondary" style="flex:0;padding:4px 10px;min-height:auto;font-size:10px;">&#x21BB; Refresh</button>
      </div>
    </details>
    <h2>&#x1F4DD; Capture &amp; Save</h2>
    <label for="transcript">Transcript</label>
    <textarea id="transcript" placeholder="Speak or type what was said."></textarea>
    <label for="activityContext">Context</label>
    <input id="activityContext" placeholder="Where / what / why?">
    <div class="btn-group">
      <button id="saveBtn" class="success" disabled>&#x1F4BE; Save</button>
      <button id="clearBtn" class="secondary">&#x1F5D1; Clear</button>
    </div>
    <div id="saveStatus" class="status"></div>
    <div class="inline-meta">
      <span id="captureCount">no captures saved</span>
    </div>
  </section>

  <section>
    <h2>&#x1F4CB; Latest Response</h2>
    <pre id="latest">{}</pre>
  </section>
  <section>
    <h2>&#x1F464; Enrolled Speakers</h2>
    <div id="speakerList"><span class="status">Loading speakers...</span></div>
    <div class="inline-meta" style="margin-top:6px;">
      <button id="refreshSpeakersBtn" class="secondary" style="flex:0;padding:4px 10px;min-height:auto;font-size:11px;">&#x21BB; Refresh</button>
    </div>
  </section>
  </div>

  <div id="meetingMode" style="display:none;">
  <section>
    <h2>&#x1F91D; Meeting Mode <span class="sub">diarize, transcribe &amp; summarize</span></h2>
    <label for="meetingFile">Upload meeting audio</label>
    <input id="meetingFile" type="file" accept="audio/*,video/*">
    <div class="inline-meta" style="margin-top:8px;gap:12px;">
      <label style="font-size:11px;display:flex;align-items:center;gap:4px;">
        Max speakers:
        <select id="meetingMaxSpeakers" style="font-size:11px;padding:2px 4px;">
          <option value="">auto</option>
          <option value="2">2</option>
          <option value="3">3</option>
          <option value="4">4</option>
          <option value="5">5</option>
          <option value="6">6</option>
        </select>
      </label>
    </div>
    <div class="btn-group" style="margin-top:10px;">
      <button id="processMeetingBtn" class="warning" disabled>&#x2699; Process Meeting</button>
    </div>
    <div class="meter"><div id="meetingProgress"></div></div>
    <div id="meetingStatus" class="status">Upload an audio file to process.</div>
  </section>
  <section id="meetingResults" style="display:none;">
    <h2>&#x1F4CA; Results</h2>
    <div id="meetingMeta" class="inline-meta"></div>
    <div id="speakerTimeline" class="speaker-timeline"></div>
    <div id="speakerFilter" class="speaker-filter"></div>
    <div id="meetingTranscript" style="margin-top:12px;"></div>
    <div id="meetingSummary" style="margin-top:12px;display:none;">
      <h3>&#x1F4DD; Summary</h3>
      <pre id="summaryText" style="max-height:400px;"></pre>
    </div>
    <div class="btn-group" style="margin-top:10px;">
      <button id="dispatchMeetingBtn" class="primary">&#x1F4E4; Dispatch to AssistX</button>
      <button id="downloadTranscriptBtn" class="secondary">&#x1F4E5; Download</button>
    </div>
  </section>
  <section id="meetingHistory">
    <h2>&#x1F4C2; Past Meetings</h2>
    <div id="meetingHistoryList" style="max-height:300px;overflow-y:auto;"></div>
    <div id="meetingHistoryStatus" class="status">Loading history...</div>
  </section>
  </div>

  <div id="dispatchMode" style="display:none;">
  <section>
    <h2>&#x1F4E4; Auto-Assist Bridge <span class="sub">dispatch voice events to assistx</span></h2>
    <div class="status-bar" id="dispatchStatusBar">
      <div id="assistxPill" class="pill" style="border-color:#555;color:#9aa7b6;">assistx: checking...</div>
      <div id="autoDispatchPill" class="pill" style="border-color:#555;color:#9aa7b6;display:none;">auto: off</div>
    </div>
    <div class="row" style="margin-top:10px;">
      <div>
        <label for="dispatchUrl">AssistX URL</label>
        <input id="dispatchUrl" value="http://host.docker.internal:8000">
      </div>
      <div>
        <label for="dispatchToken">Webhook Secret</label>
        <input id="dispatchToken" type="password" placeholder="optional">
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:10px;margin-top:8px;">
      <input id="autoDispatchToggle" type="checkbox" style="width:auto;">
      <label for="autoDispatchToggle" style="margin:0;font-size:13px;cursor:pointer;">Auto-dispatch voice events to AssistX</label>
      <button id="saveDispatchConfigBtn" class="secondary" style="flex:0;padding:6px 12px;min-height:auto;font-size:12px;">Save Config</button>
    </div>
  </section>
  <section>
    <h2>&#x1F514; Dispatch Event</h2>
    <div class="row">
      <div>
        <label for="dispatchEventType">Event Type</label>
        <select id="dispatchEventType">
          <option value="voice_auth">voice_auth</option>
          <option value="meeting_transcript">meeting_transcript</option>
          <option value="ralph_iteration">ralph_iteration</option>
          <option value="tts_chunk">tts_chunk</option>
          <option value="barge_in">barge_in</option>
          <option value="task_created">task_created</option>
        </select>
      </div>
      <div>
        <label for="dispatchAuto">Auto Dispatch</label>
        <select id="dispatchAuto">
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      </div>
    </div>
    <label for="dispatchText">Event Text</label>
    <textarea id="dispatchText" placeholder="Enter text payload to dispatch to assistx..." style="min-height:60px;"></textarea>
    <label for="dispatchMeta">Metadata (JSON)</label>
    <input id="dispatchMeta" placeholder='{"source":"sophia","confidence":0.95}'>
    <div class="btn-group" style="margin-top:10px;">
      <button id="dispatchSendBtn" class="primary">&#x1F4E4; Send to AssistX</button>
    </div>
  </section>
  <section id="dispatchLogSection">
    <h2>&#x1F4CB; Dispatch Log</h2>
    <div id="dispatchLog" style="max-height:300px;overflow-y:auto;"></div>
  </section>
  </div>
</main>
<script>
(() => {
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const saveBtn = document.getElementById('saveBtn');
  const verifyBtn = document.getElementById('verifyBtn');
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
  let audioCtx, analyser, raf;
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
    return Boolean(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
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
      const matchedDevice = data.device_id && data.device_id !== 'default' ? ' [' + data.device_id + ']' : '';
      showAuthResult(data.score, data.accepted, matchedDevice);
      const msg = data.accepted
        ? 'Voice verified ✓ score=' + data.score.toFixed(4) + matchedDevice
        : 'Voice rejected ✗ score=' + data.score.toFixed(4) + ' — try recording again in a quiet space';
      saveStatus.textContent = msg;
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
    if (!lastAccepted) {
      saveStatus.textContent = 'Verify your voice first before enrolling.';
      return;
    }
    const form = new FormData();
    form.append('audio', audioSrc, audioSrc.name || 'capture.webm');
    form.append('user_id', userId.value || 'default');
    const devId = document.getElementById('deviceId').value.trim();
    if (devId) form.append('device_id', devId);
    saveStatus.textContent = 'Enrolling voice sample...';
    setAuthPill('enrolling', 'enrolling...');
    try {
      const res = await fetch('/voiceprints/enroll', { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Enroll failed');
      const label = data.device_id && data.device_id !== 'default' ? ' (' + data.device_id + ')' : '';
      setAuthPill('pass', 'enrolled' + label);
      enrollCount.textContent = data.sample_count + ' samples enrolled' + label;
      saveStatus.textContent = 'Voice enrolled ✓ (' + data.sample_count + ' total samples)' + label;
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
      saveBtn.disabled = !blob && !selectedFile && !transcriptEl.value.trim();
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

  async function start() {
    chunks = [];
    blob = null;
    selectedFile = null;
    saveBtn.disabled = true;
    saveStatus.textContent = '';
    authResult.classList.add('hidden');
    if (!hasLiveMic()) {
      enableFallback('Live microphone recording needs HTTPS on iOS Safari. Use the audio file picker below to record or upload.');
      return;
    }
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    startMeter(stream);
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
    recorder.onstop = () => {
      blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
      preview.src = URL.createObjectURL(blob);
      preview.hidden = false;
      saveBtn.disabled = false;
      verifyBtn.disabled = false;
      enrollBtn.disabled = true;
      authResult.classList.add('hidden');
      statusEl.textContent = 'Recording stopped. Verify your voice, then enroll or save.';
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
    if (stream) stream.getTracks().forEach(t => t.stop());
    if (raf) cancelAnimationFrame(raf);
    if (audioCtx) audioCtx.close();
    levelEl.style.width = '0%';
    startBtn.disabled = false;
    stopBtn.disabled = true;
    statusEl.textContent = 'Recording stopped. Review transcript, then save.';
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

  startBtn.onclick = () => start().catch(err => {
    enableFallback('Microphone unavailable here: ' + (err && err.message ? err.message : 'browser blocked live capture'));
  });
  stopBtn.onclick = stop;
  saveBtn.onclick = () => save().catch(err => { saveStatus.textContent = err.message; });
  verifyBtn.onclick = () => verifyVoice();
  enrollBtn.onclick = () => enrollVoice();
  transcriptEl.oninput = () => { saveBtn.disabled = !blob && !selectedFile && !transcriptEl.value.trim(); };
  audioFile.onchange = () => {
    selectedFile = audioFile.files && audioFile.files[0] ? audioFile.files[0] : null;
    blob = null;
    if (selectedFile) {
      preview.src = URL.createObjectURL(selectedFile);
      preview.hidden = false;
      saveBtn.disabled = false;
      verifyBtn.disabled = false;
      enrollBtn.disabled = true;
      authResult.classList.add('hidden');
      startedAt = Date.now();
      statusEl.textContent = 'Audio selected. Add or edit transcript, then save.';
    }
  };
  clearBtn.onclick = () => {
    transcriptEl.value = '';
    activityContext.value = '';
    preview.hidden = true;
    blob = null;
    selectedFile = null;
    audioFile.value = '';
    saveBtn.disabled = true;
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
        if (!sr.ok) throw new Error(t.detail || 'Status check failed');
        meetingProgress.style.width = Math.max(5, t.progress_pct || 0) + '%';
        meetingStatus.textContent = t.step || 'Processing...';
        if (t.status === 'completed' && t.result) {
          done = true;
          const data = t.result;
          meetingProgress.style.width = '100%';
          lastMeetingResult = data;
          const graphBadge = data.graph_saved ? '&#x1F5C4; graph saved' : (data.graph_error ? '&#x26A0; graph error' : '');
          meetingStatus.textContent = 'Done. ' + data.segments.length + ' segments, ' + data.num_speakers + ' speaker(s).';
          meetingMeta.innerHTML = '<span>' + data.duration_s + 's audio</span><span>' + data.num_speakers + ' speaker(s)</span><span>' + data.segments.length + ' segments</span>' + (graphBadge ? '<span>' + graphBadge + '</span>' : '');
          renderTimeline(data);
          meetingResults.style.display = '';
          if (autoDispatchToggle.checked && data.transcript) {
            meetingStatus.textContent = 'Done. Dispatching to AssistX...';
            const dispatchMeta = {
              duration_s: data.duration_s,
              num_speakers: data.num_speakers,
              meeting_id: data.meeting_id,
              graph_saved: data.graph_saved,
            };
            const dispatchResult = await autoDispatch('meeting_transcript', data.transcript.slice(0, 500), dispatchMeta);
            if (dispatchResult.sent) {
              meetingMeta.innerHTML += '<span>&#x1F4E4; dispatched</span>';
            }
          }
          meetingStatus.textContent = 'Done. ' + data.segments.length + ' segments, ' + data.num_speakers + ' speaker(s).';
        } else if (t.status === 'error') {
          throw new Error(t.error || 'Processing failed');
        }
      }
    } catch (err) {
      meetingStatus.textContent = 'Error: ' + err.message;
      meetingProgress.style.width = '0%';
    } finally {
      processMeetingBtn.disabled = false;
    }
  }

  const SPEAKER_COLORS = ['#7dd3fc','#34d399','#fbbf24','#fb7185','#a78bfa','#f472b6','#fb923c','#4ade80'];
  let meetingFilterSpeaker = null;

  function renderTimeline(data) {
    const dur = data.duration_s || 1;
    const segments = data.segments || [];
    const tl = document.getElementById('speakerTimeline');
    tl.innerHTML = segments.map(s => {
      const w = Math.max(2, ((s.end - s.start) / dur) * 100);
      const color = SPEAKER_COLORS[(s.speaker + 1) % SPEAKER_COLORS.length];
      return '<div class="tl-seg" style="width:' + w + '%;background:' + color + ';" title="' + (s.name || 'Spk ' + (s.speaker+1)) + ' ' + s.start.toFixed(1) + 's-' + s.end.toFixed(1) + 's"></div>';
    }).join('');

    const names = [...new Set(segments.map(s => s.name || 'Speaker ' + (s.speaker + 1)))];
    const filter = document.getElementById('speakerFilter');
    filter.innerHTML = '<button class="active" data-spk="">All</button>' + names.map(n => {
      const seg = segments.find(s => (s.name || 'Speaker ' + (s.speaker + 1)) === n);
      const idx = seg ? (seg.speaker + 1) % SPEAKER_COLORS.length : 0;
      return '<button data-spk="' + n.replace(/"/g,'&quot;') + '" style="border-color:' + SPEAKER_COLORS[idx] + ';">' + n + '</button>';
    }).join('');
    filter.querySelectorAll('button').forEach(btn => {
      btn.onclick = () => {
        meetingFilterSpeaker = btn.dataset.spk || null;
        filter.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderSegments(data);
      };
    });
    meetingFilterSpeaker = null;
    renderSegments(data);
  }

  function renderSegments(data) {
    const segments = data.segments || [];
    const html = segments.map(s => {
      const name = s.name || 'Speaker ' + (s.speaker + 1);
      const match = meetingFilterSpeaker && name !== meetingFilterSpeaker;
      const time = s.start.toFixed(1) + 's - ' + s.end.toFixed(1) + 's';
      const color = SPEAKER_COLORS[(s.speaker + 1) % SPEAKER_COLORS.length];
      const clusterConf = s.cluster_confidence ? 'c:' + (s.cluster_confidence*100).toFixed(0) + '%' : '';
      const verifConf = s.confidence ? 'v:' + (s.confidence*100).toFixed(0) + '%' : '';
      const confs = [clusterConf, verifConf].filter(Boolean).join(' ');
      return '<div class="meeting-segment' + (match ? ' filtered-out' : '') + '" style="border-left-color:' + color + ';"><div class="speaker-label">' + name + ' <span class="seg-time">' + time + (confs ? ' (' + confs + ')' : '') + '</span></div><div>' + (s.transcript || '(no speech)') + '</div></div>';
    }).join('');
    meetingTranscript.innerHTML = html;
    if (data.summary) {
      meetingSummary.style.display = '';
      summaryText.textContent = data.summary;
    } else {
      meetingSummary.style.display = 'none';
    }
  }
  processMeetingBtn.onclick = processMeeting;

  async function loadMeetingHistory() {
    meetingHistoryStatus.textContent = 'Loading history...';
    try {
      const res = await fetch('/meeting/history?limit=20');
      const data = await res.json();
      if (data.error) { meetingHistoryStatus.textContent = 'Error: ' + data.error; return; }
      if (!data.meetings || data.meetings.length === 0) {
        meetingHistoryList.innerHTML = '<div style="color:#64748b;font-size:13px;padding:8px;">No meetings yet.</div>';
        meetingHistoryStatus.textContent = '';
        return;
      }
      meetingHistoryList.innerHTML = data.meetings.map(m => {
        const date = m.created_at ? new Date(m.created_at).toLocaleDateString() : '?';
        const label = '[' + date + '] ' + m.duration_s + 's, ' + m.num_speakers + ' spk, ' + m.segment_count + ' segs';
        return '<div class="meeting-segment" style="cursor:pointer;font-size:12px;position:relative;" onclick="loadMeetingDetail(\'' + m.id + '\')">'
          + '<div class="speaker-label">' + label + (m.has_summary ? ' &#x1F4DD;' : '') + '</div>'
          + '<div>' + (m.transcript || '(no transcript)') + '</div>'
          + '<span class="del-meeting" data-id="' + m.id + '" style="position:absolute;top:4px;right:6px;cursor:pointer;color:#fb7185;font-size:15px;line-height:1;" title="Delete meeting">&times;</span></div>';
      }).join('');
      meetingHistoryList.querySelectorAll('.del-meeting').forEach(el => {
        el.onclick = async (ev) => {
          ev.stopPropagation();
          if (!confirm('Delete this meeting from Neo4j?')) return;
          try {
            const r = await fetch('/meeting/history/' + encodeURIComponent(el.dataset.id), { method: 'DELETE' });
            if (r.ok) loadMeetingHistory();
          } catch {}
        };
      });
      meetingHistoryStatus.textContent = data.meetings.length + ' meetings';
    } catch (err) {
      meetingHistoryStatus.textContent = 'Error loading history: ' + err.message;
    }
  }

  window.loadMeetingDetail = async function(meetingId) {
    meetingHistoryStatus.textContent = 'Loading meeting...';
    try {
      const res = await fetch('/meeting/history/' + encodeURIComponent(meetingId));
      const data = await res.json();
      if (data.error) { meetingHistoryStatus.textContent = 'Error: ' + data.error; return; }
      meetingMeta.innerHTML = '<span>' + data.duration_s + 's audio</span><span>' + data.num_speakers + ' speaker(s)</span><span>' + (data.segments || []).length + ' segments</span>';
      meetingTranscript.innerHTML = (data.segments || []).map(s => {
        const name = s.speaker || 'Speaker ?';
        const time = (s.start || 0).toFixed(1) + 's - ' + (s.end || 0).toFixed(1) + 's';
        return '<div class="meeting-segment"><div class="speaker-label">' + name + ' <span class="seg-time">' + time + '</span></div><div>' + (s.transcript || '(no speech)') + '</div></div>';
      }).join('');
      if (data.summary) {
        meetingSummary.style.display = '';
        summaryText.textContent = data.summary;
      } else {
        meetingSummary.style.display = 'none';
      }
      lastMeetingResult = data;
      meetingResults.style.display = '';
      meetingHistoryStatus.textContent = 'Loaded meeting from history.';
      switchMode('meeting');
    } catch (err) {
      meetingHistoryStatus.textContent = 'Error: ' + err.message;
    }
  };

  async function loadSpeakers() {
    try {
      const res = await fetch('/voiceprints/status');
      const data = await res.json();
      if (data.error) { speakerList.innerHTML = '<div style="color:#fb7185;font-size:13px;">Error: ' + data.error + '</div>'; return; }
      if (!data.users || data.users.length === 0) {
        speakerList.innerHTML = '<div style="color:#64748b;font-size:13px;">No enrolled speakers. Record and verify your voice, then Enroll.</div>';
        return;
      }
      speakerList.innerHTML = data.users.map(u => {
        const devs = u.devices && u.devices.length
          ? ' <div style="margin-top:4px;font-size:11px;color:#64748b;">devices: ' + u.devices.map(d =>
              '<span style="display:inline-flex;align-items:center;gap:2px;margin-right:6px;">' + d +
              ' <span class="del-device" data-user="' + u.user_id + '" data-device="' + d + '" style="cursor:pointer;color:#fb7185;font-size:13px;line-height:1;" title="Delete device">&times;</span></span>'
            ).join('') + '</div>'
          : '';
        return '<div class="meeting-segment" style="font-size:13px;"><span class="speaker-label">' + u.user_id + '</span>'
          + ' <span style="color:#94a3b8;">' + u.sample_count + ' samples</span>'
          + ' <span style="color:#64748b;font-size:11px;">threshold: ' + u.threshold + '</span>'
          + devs + '</div>';
      }).join('');
      speakerList.querySelectorAll('.del-device').forEach(el => {
        el.onclick = async () => {
          if (!confirm('Delete device "' + el.dataset.device + '" for ' + el.dataset.user + '?')) return;
          try {
            const r = await fetch('/voiceprints/device/' + encodeURIComponent(el.dataset.user) + '/' + encodeURIComponent(el.dataset.device), { method: 'DELETE' });
            if (r.ok) loadSpeakers();
          } catch {}
        };
      });
    } catch (err) {
      speakerList.innerHTML = '<div style="color:#fb7185;font-size:13px;">Error: ' + err.message + '</div>';
    }
  }

  downloadTranscriptBtn.onclick = () => {
    if (!lastMeetingResult) return;
    const text = 'Meeting Transcript\n' + '='.repeat(40) + '\n\n'
      + 'Duration: ' + (lastMeetingResult.duration_s || '?') + 's\n'
      + 'Speakers: ' + (lastMeetingResult.num_speakers || '?') + '\n\n'
      + (lastMeetingResult.transcript || '') + '\n\n'
      + (lastMeetingResult.summary ? 'Summary:\n' + lastMeetingResult.summary : '');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'meeting_' + (lastMeetingResult.meeting_id || Date.now()) + '.txt';
    a.click();
    URL.revokeObjectURL(url);
  };

  refreshSpeakersBtn.onclick = loadSpeakers;

  async function refreshDispatchStatus() {
    try {
      assistxPill.textContent = 'assistx: checking...';
      assistxPill.style.borderColor = '#555';
      assistxPill.style.color = '#9aa7b6';
      const res = await fetch('/dispatch/status');
      const data = await res.json();
      if (data.assistx_reachable) {
        assistxPill.textContent = 'assistx: connected (' + data.dispatched_count + ' sent)';
        assistxPill.style.borderColor = '#34d399';
        assistxPill.style.color = '#6ee7b7';
      } else {
        assistxPill.textContent = 'assistx: unreachable';
        assistxPill.style.borderColor = '#fb7185';
        assistxPill.style.color = '#fda4af';
      }
    } catch {
      assistxPill.textContent = 'assistx: error';
      assistxPill.style.borderColor = '#fb7185';
      assistxPill.style.color = '#fda4af';
    }
  }

  async function sendDispatch() {
    const payload = {
      event_type: dispatchEventType.value,
      text: dispatchText.value,
      metadata: {},
      auto_dispatch: dispatchAuto.value === 'true',
      target_url: dispatchUrl.value,
      target_token: dispatchToken.value,
    };
    try {
      const meta = JSON.parse(dispatchMeta.value || '{}');
      if (typeof meta === 'object' && !Array.isArray(meta)) payload.metadata = meta;
    } catch {}
    dispatchSendBtn.disabled = true;
    dispatchSendBtn.textContent = 'Sending...';
    try {
      const res = await fetch('/dispatch/to-assistx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      const entry = document.createElement('div');
      const statusClass = data.sent ? 'sent' : 'failed';
      entry.className = 'dispatch-entry ' + statusClass;
      const time = new Date().toLocaleTimeString();
      entry.innerHTML = '<div><span class="de-type">' + payload.event_type + '</span><span class="de-time">' + time + '</span></div>'
        + '<div class="de-status">' + (data.sent ? '&#10003; Sent' : '&#10007; Failed') + '</div>'
        + (data.error ? '<div class="de-error">' + data.error + '</div>' : '')
        + (payload.text ? '<div style="color:#94a3b8;font-size:11px;">' + payload.text.slice(0, 80) + '</div>' : '');
      dispatchLog.prepend(entry);
      while (dispatchLog.children.length > 50) dispatchLog.removeChild(dispatchLog.lastChild);
      refreshDispatchStatus();
    } catch (err) {
      const entry = document.createElement('div');
      entry.className = 'dispatch-entry failed';
      entry.innerHTML = '<div><span class="de-type">' + payload.event_type + '</span><span class="de-time">' + new Date().toLocaleTimeString() + '</span></div>'
        + '<div class="de-error">Network error: ' + err.message + '</div>';
      dispatchLog.prepend(entry);
    } finally {
      dispatchSendBtn.disabled = false;
      dispatchSendBtn.textContent = 'Send to AssistX';
    }
  }
  dispatchSendBtn.onclick = sendDispatch;

  function loadDispatchConfig() {
    try {
      const saved = localStorage.getItem('sophia_dispatch_config');
      if (saved) {
        const cfg = JSON.parse(saved);
        if (cfg.url) dispatchUrl.value = cfg.url;
        if (cfg.token) dispatchToken.value = cfg.token;
        autoDispatchToggle.checked = cfg.auto_dispatch === true;
        updateAutoPill();
      }
    } catch {}
  }

  function saveDispatchConfig() {
    const cfg = {
      url: dispatchUrl.value,
      token: dispatchToken.value,
      auto_dispatch: autoDispatchToggle.checked,
    };
    localStorage.setItem('sophia_dispatch_config', JSON.stringify(cfg));
    saveDispatchConfigBtn.textContent = 'Saved!';
    setTimeout(() => { saveDispatchConfigBtn.textContent = 'Save Config'; }, 2000);
  }
  saveDispatchConfigBtn.onclick = saveDispatchConfig;

  function updateAutoPill() {
    if (autoDispatchToggle.checked) {
      autoDispatchPill.style.display = '';
      autoDispatchPill.textContent = 'auto: on';
      autoDispatchPill.style.borderColor = '#34d399';
      autoDispatchPill.style.color = '#6ee7b7';
    } else {
      autoDispatchPill.style.display = 'none';
    }
  }
  autoDispatchToggle.onchange = updateAutoPill;

  function getDispatchConfig() {
    return {
      target_url: dispatchUrl.value,
      target_token: dispatchToken.value,
      auto_dispatch: autoDispatchToggle.checked,
    };
  }

  async function autoDispatch(eventType, text, metadata) {
    const cfg = getDispatchConfig();
    if (!cfg.auto_dispatch) return { sent: false, skipped: 'auto_dispatch_off' };
    const payload = { event_type: eventType, text, metadata, ...cfg };
    try {
      const res = await fetch('/dispatch/to-assistx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      const entry = document.createElement('div');
      entry.className = 'dispatch-entry ' + (data.sent ? 'sent' : 'failed');
      entry.innerHTML = '<div><span class="de-type">auto: ' + eventType + '</span><span class="de-time">' + new Date().toLocaleTimeString() + '</span></div>'
        + '<div class="de-status">' + (data.sent ? '&#10003; Auto-sent' : '&#10007; Failed') + '</div>'
        + (data.error ? '<div class="de-error">' + data.error + '</div>' : '')
        + (text ? '<div style="color:#94a3b8;font-size:11px;">' + text.slice(0, 80) + '</div>' : '');
      dispatchLog.prepend(entry);
      while (dispatchLog.children.length > 50) dispatchLog.removeChild(dispatchLog.lastChild);
      refreshDispatchStatus();
      return data;
    } catch (err) {
      return { sent: false, error: err.message };
    }
  }
  loadDispatchConfig();

  function populateAndSwitchToDispatch(eventType, text, meta) {
    dispatchEventType.value = eventType;
    dispatchText.value = text;
    dispatchMeta.value = JSON.stringify(meta || {}, null, 2);
    switchMode('dispatch');
  }

  dispatchAuthBtn.onclick = () => {
    if (!lastAuthResult || !lastAuthResult.accepted) return;
    populateAndSwitchToDispatch('voice_auth',
      'Voice auth: user=' + lastAuthResult.userId + ' score=' + (lastAuthResult.score * 100).toFixed(0) + '%',
      { score: lastAuthResult.score, accepted: lastAuthResult.accepted, userId: lastAuthResult.userId }
    );
  };

  dispatchMeetingBtn.onclick = () => {
    if (!lastMeetingResult) return;
    const meta = {
      duration_s: lastMeetingResult.duration_s,
      num_speakers: lastMeetingResult.num_speakers,
      meeting_id: lastMeetingResult.meeting_id,
      graph_saved: lastMeetingResult.graph_saved,
    };
    const text = lastMeetingResult.transcript ? lastMeetingResult.transcript.slice(0, 500) : 'Meeting processed: ' + lastMeetingResult.duration_s + 's';
    populateAndSwitchToDispatch('meeting_transcript', text, meta);
  };

  if (!hasLiveMic()) {
    enableFallback(window.isSecureContext
      ? 'Live recording is not available in this browser. Use the upload fallback.'
      : 'Live recording needs HTTPS on iOS Safari. Use the upload fallback, or open the HTTPS endpoint when enabled.');
  }
  const savedCount = parseInt(localStorage.getItem('sophia_capture_count') || '0');
  if (savedCount > 0) captureCount.textContent = savedCount + ' capture' + (savedCount !== 1 ? 's' : '') + ' saved';
  loadSpeakers();
  refreshGraph();
  refreshEventLog();
  refreshStatus();
  setInterval(refreshEventLog, 5000);
})();
</script>
</body>
</html>
"""


class MeetingTaskManager:
    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}

    def create(self, task_id: str) -> None:
        self._tasks[task_id] = {
            "task_id": task_id,
            "status": "queued",
            "progress_pct": 0,
            "step": "queued",
            "result": None,
            "error": None,
        }

    def update(self, task_id: str, status: str, step: str, pct: int, result: Any = None, error: str | None = None) -> None:
        t = self._tasks.get(task_id)
        if t is None:
            return
        t["status"] = status
        t["step"] = step
        t["progress_pct"] = pct
        if result is not None:
            t["result"] = result
        if error is not None:
            t["error"] = error

    def get(self, task_id: str) -> Dict[str, Any] | None:
        return self._tasks.get(task_id)


async def _process_meeting_background(
    config: AppConfig,
    bus: EventBus,
    meeting_tasks: MeetingTaskManager,
    manager: SessionManager,
    intent_provider: Any | None,
    task_id: str,
    audio_data: bytes,
    summarize: bool,
    max_speakers: int | None,
) -> None:
    tmp_dir = Path(config.paths.artifacts_dir) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    in_path = tmp_dir / f"meeting_{task_id}.webm"
    wav_path = in_path.with_suffix(".wav")
    try:
        meeting_tasks.update(task_id, "processing", "converting audio", 5)
        bus.publish("meeting_progress", {"task_id": task_id, "step": "converting", "pct": 5})
        in_path.write_bytes(audio_data)
        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(in_path), "-f", "wav", "-acodec", "pcm_s16le",
             "-ac", "1", "-ar", "16000", str(wav_path)],
            capture_output=True, timeout=60,
        )
        if not wav_path.exists():
            raise RuntimeError("ffmpeg conversion failed")
        samples, sr = read_wav(wav_path)
        duration_s = len(samples) / sr
        if duration_s < 1.0:
            raise RuntimeError("Audio too short (<1s)")

        meeting_tasks.update(task_id, "processing", "diarizing speakers", 20)
        bus.publish("meeting_progress", {"task_id": task_id, "step": "diarizing", "pct": 20})
        segments = diarize(samples, sr, max_speakers=max_speakers)

        meeting_tasks.update(task_id, "processing", "identifying speakers", 35)
        bus.publish("meeting_progress", {"task_id": task_id, "step": "identifying", "pct": 35})
        registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite")
        enrolled = {}
        for uid in ["scott", "default"]:
            rec = registry.get(uid)
            if rec:
                enrolled[uid] = rec
        if enrolled:
            embedder = SpeakerEmbedder()
            segments = identify_speakers(segments, enrolled, embedder, sr, samples)

        n_segs = len(segments)
        full_transcript_parts = []
        for idx, seg in enumerate(segments):
            start_samp = int(seg["start"] * sr)
            end_samp = int(seg["end"] * sr)
            chunk = samples[start_samp:end_samp]
            if len(chunk) < int(sr * 0.3):
                seg["transcript"] = ""
                continue
            pct = 40 + int(50 * (idx / n_segs)) if n_segs else 40
            meeting_tasks.update(task_id, "processing", f"transcribing segment {idx+1}/{n_segs}", pct)
            bus.publish("meeting_progress", {"task_id": task_id, "step": "transcribing", "pct": pct, "segment": idx + 1, "total": n_segs})
            text = refine_transcript(chunk, sr, config.stt)
            seg["transcript"] = text
            name = seg.get("name", f"Speaker {seg['speaker'] + 1}")
            full_transcript_parts.append(f"[{name}]: {text}")

        summary = None
        if summarize and full_transcript_parts:
            meeting_tasks.update(task_id, "processing", "summarizing", 92)
            bus.publish("meeting_progress", {"task_id": task_id, "step": "summarizing", "pct": 92})
            meeting_text = "\n".join(full_transcript_parts)
            prompt = (
                "Summarize this meeting transcript. Extract:\n"
                "- Key decisions made\n"
                "- Action items with owner if mentioned\n"
                "- Main discussion topics\n\n"
                f"Transcript:\n{meeting_text}"
            )
            try:
                if intent_provider:
                    summary = intent_provider.complete(prompt).content.strip()
                else:
                    summary = manager.pipeline.ralph.run(prompt)
            except Exception:
                summary = None

        meeting_id = uuid.uuid4().hex
        full_transcript = "\n".join(full_transcript_parts)
        graph_saved = False
        graph_error = None
        if config.neo4j.password:
            meeting_tasks.update(task_id, "processing", "saving to graph", 95)
            bus.publish("meeting_progress", {"task_id": task_id, "step": "saving", "pct": 95})
            try:
                from ..auth.neo4j_ingest import save_meeting_to_neo4j
                save_meeting_to_neo4j(
                    config.neo4j.uri,
                    config.neo4j.user,
                    config.neo4j.password,
                    meeting_id=meeting_id,
                    transcript=full_transcript,
                    segments=segments,
                    duration_s=duration_s,
                    num_speakers=len(set(s["speaker"] for s in segments if s["speaker"] >= 0)),
                    summary=summary,
                    database=config.neo4j.database,
                )
                graph_saved = True
            except Exception as exc:
                graph_error = f"{type(exc).__name__}: {exc}"

        num_speakers = len(set(s["speaker"] for s in segments if s["speaker"] >= 0))
        result = {
            "ok": True,
            "meeting_id": meeting_id,
            "duration_s": round(duration_s, 1),
            "num_speakers": num_speakers,
            "segments": segments,
            "transcript": full_transcript,
            "summary": summary,
            "graph_saved": graph_saved,
            "graph_error": graph_error,
        }
        meeting_tasks.update(task_id, "completed", "done", 100, result=result)
        bus.publish("meeting_progress", {"task_id": task_id, "step": "done", "pct": 100})
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        meeting_tasks.update(task_id, "error", "failed", 0, error=error_msg)
        bus.publish("meeting_progress", {"task_id": task_id, "step": "error", "error": error_msg})
    finally:
        for p in [in_path, wav_path]:
            if p.exists():
                p.unlink(missing_ok=True)


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="Sophia Voice Agent", version="0.1.0")
    bus = EventBus()
    manager = SessionManager(
        config,
        Path(config.paths.artifacts_dir),
        event_callback=lambda event_type, payload: bus.publish(event_type, payload),
    )
    protocol = build_protocol_adapter(config.server.protocol)
    meeting_tasks = MeetingTaskManager()
    app.state.config = config
    app.state.manager = manager
    app.state.events = bus
    app.state.meeting_tasks = meeting_tasks
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

    @app.post("/llm/test")
    async def llm_test() -> Dict[str, Any]:
        try:
            prov = intent_provider or manager.pipeline.ralph.provider
            resp = prov.complete("Say exactly: LLM_OK")
            ok = "LLM_OK" in (resp.content or "")
            return {"ok": ok, "response": (resp.content or "")[:200], "provider": type(prov).__name__}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "provider": type(intent_provider or manager.pipeline.ralph.provider).__name__}

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
            suffix = ".webm"
            if audio.filename and "." in audio.filename:
                suffix = "." + audio.filename.rsplit(".", 1)[-1].lower()
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

    @app.post("/auth/verify")
    async def auth_verify(
        audio: UploadFile | None = File(default=None),
        user_id: str = Form(default="scott"),
    ) -> Dict[str, Any]:
        if audio is None:
            raise HTTPException(status_code=400, detail="audio file is required")
        tmp_dir = Path(config.paths.artifacts_dir) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        in_path = tmp_dir / f"verify_{uuid.uuid4().hex}.webm"
        wav_path = in_path.with_suffix(".wav")
        try:
            data = await audio.read()
            in_path.write_bytes(data)
            import subprocess
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(in_path), "-f", "wav", "-acodec", "pcm_s16le",
                 "-ac", "1", "-ar", "16000", str(wav_path)],
                capture_output=True, timeout=30,
            )
            if not wav_path.exists():
                raise RuntimeError("ffmpeg conversion failed")
            from voice_agent.util.audio import read_wav
            samples, sr = read_wav(wav_path)
            result = verify_audio_segment(config, "", user_id, samples, sr)
            return {
                "user_id": user_id,
                "score": result.get("score", 0.0),
                "accepted": result.get("accepted", False),
            }
        finally:
            for p in [in_path, wav_path]:
                if p.exists():
                    p.unlink(missing_ok=True)

    @app.post("/voiceprints/enroll")
    async def voiceprints_enroll(
        audio: UploadFile | None = File(default=None),
        user_id: str = Form(default="scott"),
        device_id: str = Form(default=""),
    ) -> Dict[str, Any]:
        if audio is None:
            raise HTTPException(status_code=400, detail="audio file is required")
        tmp_dir = Path(config.paths.artifacts_dir) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        ext = ".webm"
        if audio.filename and "." in audio.filename:
            ext = "." + audio.filename.rsplit(".", 1)[-1].lower()
        in_path = tmp_dir / f"enroll_{uuid.uuid4().hex}{ext}"
        wav_path = in_path.with_suffix(".wav")
        try:
            data = await audio.read()
            in_path.write_bytes(data)
            import subprocess
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(in_path), "-f", "wav", "-acodec", "pcm_s16le",
                 "-ac", "1", "-ar", "16000", str(wav_path)],
                capture_output=True, timeout=30,
            )
            if not wav_path.exists():
                raise RuntimeError("ffmpeg conversion failed")
            from voice_agent.util.audio import read_wav
            samples, sr = read_wav(wav_path)
            from voice_agent.auth.speaker_embedder import SpeakerEmbedder
            embedder = SpeakerEmbedder()
            embedding = embedder.embed(samples, sr)
            from ..auth.registry import VoiceprintRegistry
            registry = VoiceprintRegistry(Path(config.paths.artifacts_dir) / "results.sqlite")

            did = device_id.strip() or "default"
            if did == "default":
                existing = registry.get(user_id)
                if existing:
                    old_emb = existing["embedding"]
                    n = existing.get("samples", {}).get("count", 1)
                    merged = [(old_emb[i] * n + embedding[i]) / (n + 1) for i in range(len(embedding))]
                    samples_info = existing.get("samples", {})
                    samples_info["count"] = n + 1
                    samples_info.setdefault("files", []).append(str(wav_path))
                    registry.save(user_id, merged, samples_info, existing["threshold"])
                    sample_count = n + 1
                else:
                    registry.save(user_id, embedding, {"count": 1, "files": [str(wav_path)]}, 0.60)
                    sample_count = 1
            else:
                existing = registry.get_devices(user_id).get(did)
                if existing:
                    old_emb = existing["embedding"]
                    n = existing.get("samples", {}).get("count", 1)
                    merged = [(old_emb[i] * n + embedding[i]) / (n + 1) for i in range(len(embedding))]
                    samples_info = existing.get("samples", {})
                    samples_info["count"] = n + 1
                    samples_info.setdefault("files", []).append(str(wav_path))
                    registry.save_device(user_id, did, merged, samples_info, existing.get("threshold", 0.60))
                    sample_count = n + 1
                else:
                    registry.save_device(user_id, did, embedding, {"count": 1, "files": [str(wav_path)]}, 0.60)
                    sample_count = 1
            return {
                "user_id": user_id,
                "device_id": did,
                "sample_count": sample_count,
            }
        finally:
            for p in [in_path, wav_path]:
                if p.exists():
                    p.unlink(missing_ok=True)

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

    @app.post("/meeting/process")
    async def meeting_process(
        audio: UploadFile | None = File(default=None),
        summarize: bool = Form(default=True),
        max_speakers: int | None = Form(default=None),
    ) -> Dict[str, Any]:
        if audio is None:
            raise HTTPException(status_code=400, detail="audio file is required")
        task_id = uuid.uuid4().hex
        meeting_tasks.create(task_id)
        data = await audio.read()
        asyncio.create_task(
            _process_meeting_background(
                config, bus, meeting_tasks, manager, intent_provider,
                task_id, data, summarize, max_speakers,
            )
        )
        return {"ok": True, "task_id": task_id}

    @app.get("/meeting/status/{task_id}")
    async def meeting_status(task_id: str) -> Dict[str, Any]:
        t = meeting_tasks.get(task_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return t

    @app.get("/meeting/history")
    async def meeting_history(limit: int = 20) -> Dict[str, Any]:
        if not config.neo4j.password:
            return {"meetings": [], "error": "Neo4j not configured"}
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(config.neo4j.uri, auth=(config.neo4j.user, config.neo4j.password))
            meetings = []
            with driver.session(database=config.neo4j.database) as sess:
                result = sess.run(
                    "MATCH (m:Meeting) "
                    "OPTIONAL MATCH (m)-[:HAS_SEGMENT]->(seg:MeetingSegment) "
                    "WITH m, count(seg) AS seg_count "
                    "RETURN m.id AS id, m.duration_s AS duration_s, m.num_speakers AS num_speakers, "
                    "m.transcript AS transcript, m.summary AS summary, m.created_at AS created_at, seg_count "
                    "ORDER BY m.created_at DESC LIMIT $limit",
                    limit=limit,
                )
                for rec in result:
                    meetings.append({
                        "id": rec["id"],
                        "duration_s": rec.get("duration_s"),
                        "num_speakers": rec.get("num_speakers"),
                        "transcript": (rec.get("transcript") or "")[:200],
                        "has_summary": bool(rec.get("summary")),
                        "segment_count": rec.get("seg_count", 0),
                        "created_at": str(rec.get("created_at") or ""),
                    })
            driver.close()
            return {"meetings": meetings}
        except Exception as exc:
            return {"meetings": [], "error": f"{type(exc).__name__}: {exc}"}

    @app.get("/meeting/history/{meeting_id}")
    async def meeting_detail(meeting_id: str) -> Dict[str, Any]:
        if not config.neo4j.password:
            return {"error": "Neo4j not configured"}
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(config.neo4j.uri, auth=(config.neo4j.user, config.neo4j.password))
            with driver.session(database=config.neo4j.database) as sess:
                result = sess.run(
                    "MATCH (m:Meeting {id: $id}) "
                    "OPTIONAL MATCH (m)-[:HAS_SEGMENT]->(seg:MeetingSegment) "
                    "WITH m, seg ORDER BY seg.segment_idx "
                    "RETURN m.id AS id, m.duration_s AS duration_s, m.num_speakers AS num_speakers, "
                    "m.transcript AS transcript, m.summary AS summary, m.created_at AS created_at, "
                    "collect({idx: seg.segment_idx, start: seg.start_s, end: seg.end_s, "
                    "speaker: seg.speaker, transcript: seg.transcript, confidence: seg.confidence}) AS segments",
                    id=meeting_id,
                )
                rec = result.single()
                if not rec:
                    return {"error": "Meeting not found"}
                driver.close()
                return {
                    "id": rec["id"],
                    "duration_s": rec.get("duration_s"),
                    "num_speakers": rec.get("num_speakers"),
                    "transcript": rec.get("transcript") or "",
                    "summary": rec.get("summary"),
                    "created_at": str(rec.get("created_at") or ""),
                    "segments": [s for s in (rec.get("segments") or []) if s.get("idx") is not None],
                }
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    @app.get("/voiceprints/status")
    async def voiceprints_status() -> Dict[str, Any]:
        from ..util.db import Database
        try:
            db = Database(Path(config.paths.artifacts_dir) / "results.sqlite")
            cur = db.conn.cursor()
            cur.execute("SELECT user_id, samples_json, threshold FROM voiceprints")
            users = []
            for row in cur.fetchall():
                samples = json.loads(row[1])
                uid = row[0]
                dev_cur = db.conn.cursor()
                dev_cur.execute("SELECT device_id FROM voiceprint_devices WHERE user_id=?", (uid,))
                devices = [d[0] for d in dev_cur.fetchall()]
                users.append({
                    "user_id": uid,
                    "sample_count": samples.get("count", 0),
                    "threshold": row[2],
                    "devices": devices,
                })
            return {"users": users, "count": len(users)}
        except Exception as exc:
            return {"users": [], "error": f"{type(exc).__name__}: {exc}"}

    @app.delete("/voiceprints/device/{user_id}/{device_id}")
    async def voiceprints_delete_device(user_id: str, device_id: str) -> Dict[str, Any]:
        from ..util.db import Database
        try:
            db = Database(Path(config.paths.artifacts_dir) / "results.sqlite")
            cur = db.conn.cursor()
            cur.execute("DELETE FROM voiceprint_devices WHERE user_id=? AND device_id=?", (user_id, device_id))
            db.conn.commit()
            return {"ok": True, "user_id": user_id, "device_id": device_id}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")

    @app.delete("/meeting/history/{meeting_id}")
    async def meeting_delete(meeting_id: str) -> Dict[str, Any]:
        if not config.neo4j.password:
            raise HTTPException(status_code=400, detail="Neo4j not configured")
        from neo4j import GraphDatabase
        try:
            driver = GraphDatabase.driver(config.neo4j.uri, auth=(config.neo4j.user, config.neo4j.password))
            with driver.session(database=config.neo4j.database) as sess:
                sess.run("MATCH (m:Meeting {id: $id})-[:HAS_SEGMENT]->(seg:MeetingSegment) DETACH DELETE seg, m", id=meeting_id)
            driver.close()
            return {"ok": True, "meeting_id": meeting_id}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")

    dispatch_history: list = []

    @app.get("/dispatch/status")
    async def dispatch_status() -> Dict[str, Any]:
        import httpx
        base = "http://host.docker.internal:8000"
        assistx_ok = False
        try:
            r = httpx.get(f"{base}/health", timeout=3)
            assistx_ok = r.status_code == 200
        except Exception:
            pass
        return {
            "assistx_reachable": assistx_ok,
            "assistx_url": base,
            "dispatched_count": len(dispatch_history),
            "last_dispatch": dispatch_history[-1] if dispatch_history else None,
        }

    @app.post("/dispatch/to-assistx")
    async def dispatch_to_assistx(req: DispatchRequest) -> Dict[str, Any]:
        import httpx, time, hmac, hashlib
        base = req.target_url
        event_id = uuid.uuid4().hex
        metadata = req.metadata if req.metadata and any(k for k in req.metadata if req.metadata[k]) else None
        ts = str(time.time())
        payload = OrderedDict()
        payload["event_id"] = event_id
        payload["event_type"] = req.event_type
        if req.text:
            payload["text"] = req.text
        payload["source"] = "sophia_voice"
        if req.session_id:
            payload["session_id"] = req.session_id
        payload["client_ts"] = ts
        if metadata is not None:
            payload["metadata"] = metadata
        payload["auto_dispatch"] = req.auto_dispatch
        body_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        sig = hmac.new(req.target_token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Voice-Signature"] = f"sha256={sig}"
        result = {"event_id": event_id, "sent": False, "error": None, "response": None}
        try:
            r = httpx.post(f"{base}/api/voice/events", content=body_bytes, headers=headers, timeout=10)
            result["sent"] = r.status_code == 200
            if r.status_code == 200:
                result["response"] = r.json()
            else:
                result["error"] = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        dispatch_history.append({**result, "event_type": req.event_type, "ts_ms": now_ms()})
        if len(dispatch_history) > 100:
            dispatch_history[:] = dispatch_history[-100:]
        return result

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
