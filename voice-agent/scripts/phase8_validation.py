#!/usr/bin/env python3
"""One entrypoint for the five PLAN.md Phase 8 real-world checks.

Every item is skip-guarded: it runs for real when its prerequisites exist, and
otherwise reports ``skipped`` with the exact reason and the command that would
unblock it. Nothing here substitutes a placeholder for a measurement, and
``phase8_evidence`` is only ever true when a real backend produced the number.

======  ==========================================================
 item    what it checks
======  ==========================================================
 1 ws-auth  real voice over ``/ws`` -> ``auth_decision`` accepted, and a
            different speaker rejected (server must run a real ECAPA
            embedder + real STT)
 2 meeting  ``POST /meeting/process`` on real multi-speaker audio ->
            ``/meeting/status`` reaches ``done`` with >= 2 diarized
            speakers and speaker-attributed transcript segments
 3 tts      real OpenVoice/Coqui/Piper backend synthesising known phrases,
            scored against a live voice reference (WER, ECAPA
            similarity, spectral distance) + a human MOS panel
 4 dispatch a signed voice event to the real AssistX endpoint, verified
            through ``/dispatch/status`` and the trace endpoint
 5 fallback held-out clips scored through the live Neo4j historical
            candidate search after the active head rejects them
======  ==========================================================

Examples::

    # everything (each item skips itself if it lacks prerequisites)
    python scripts/phase8_validation.py --out-dir reports/phase8

    # one item, with the real assets it needs
    python scripts/phase8_validation.py --item ws-auth \\
        --base-url http://127.0.0.1:8765 \\
        --passphrase "$SOPHIA_PASSPHRASE" \\
        --enroll-wav  /data/enroll/scott_01.wav \\
        --verify-wav  /data/holdout/scott_09.wav \\
        --impostor-wav /data/holdout/visitor_03.wav \\
        --allow-enroll-side-effects --confirm-ecapa-backend

    python scripts/phase8_validation.py --item meeting \\
        --meeting-audio /data/meetings/standup_2025-11-03.wav --expect-speakers 3

Exit codes: 0 no item failed (skips are reported, not failures), 1 at least one
item failed, 2 usage error. ``--strict`` also fails on skips.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_agent.server.protocols import encode_message  # noqa: E402
from voice_agent.util.audio import b64encode, float_to_pcm16_bytes, read_wav  # noqa: E402

REPORT_SCHEMA = "sophia.phase8_validation.v1"
DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_OUT_DIR = "reports/phase8"
STUB_TRANSCRIPT = "hello world"
DISPATCH_SCRIPT = ROOT / "scripts" / "dispatch_live_probe.py"
TTS_SCRIPT = ROOT / "scripts" / "tts_quality_eval.py"
FALLBACK_SCRIPT = ROOT / "scripts" / "eval_fallback_candidate_search.py"
ITEMS = ("ws-auth", "meeting", "tts", "dispatch", "fallback")


@dataclass
class ItemResult:
    item: str
    title: str
    status: str  # ok | failed | skipped
    requirement: str
    command: list[str] = field(default_factory=list)
    observed: str = ""
    evidence: bool = False
    evidence_note: str = ""
    fix: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "title": self.title,
            "status": self.status,
            "requirement": self.requirement,
            "command": self.command,
            "observed": self.observed,
            "phase8_evidence": self.evidence,
            "phase8_evidence_note": self.evidence_note,
            "fix": self.fix,
            "detail": self.detail,
            "notes": self.notes,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _http(
    url: str,
    *,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    data: bytes | None = None,
    content_type: str | None = None,
    cookie: str | None = None,
    timeout: float = 15.0,
) -> tuple[int, str]:
    headers: dict[str, str] = {}
    payload: bytes | None = data
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        return 0, f"{type(exc).__name__}: {exc.reason}"


def _json_http(*args: Any, **kwargs: Any) -> tuple[int, Any]:
    status, text = _http(*args, **kwargs)
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, text


def _multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----sophia-phase8{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
        )
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts.append(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
            f"filename=\"{file_path.name}\"\r\nContent-Type: {mime}\r\n\r\n"
        ).encode()
        + file_path.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _server_reachable(base_url: str, timeout: float = 5.0) -> tuple[bool, dict[str, Any] | str]:
    status, body = _json_http(f"{base_url}/readyz", timeout=timeout)
    if status == 0:
        return False, body
    return True, body


def _run_script(
    script: Path, script_args: Sequence[str], *, cwd: Path | None = None
) -> tuple[int, str, str]:
    command = [sys.executable, str(script), *script_args]
    proc = subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        text=True,
        timeout=1800,
        cwd=str(cwd) if cwd else None,
        env=os.environ.copy(),
    )
    return proc.returncode, proc.stdout, proc.stderr


def _script_result(code: int, stdout: str, stderr: str, script: Path) -> tuple[str, dict[str, Any]]:
    """Map a child script exit code onto an item status, plus whatever it printed."""
    status = {0: "ok", 3: "skipped", 1: "failed", 2: "failed"}.get(code, "failed")
    payload: dict[str, Any] = {"stdout_tail": stdout[-4000:], "stderr_tail": stderr[-2000:]}
    return status, payload


def _child_report(out_dir: Path, filename: str) -> dict[str, Any] | None:
    path = out_dir / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _merge_child(
    result: ItemResult, payload: dict[str, Any], report: dict[str, Any] | None
) -> None:
    if report:
        result.detail = {"harness": report, "command_output": payload}
        source = report
    else:
        result.detail = {"command_output": payload}
        source = payload
    result.observed = str(
        source.get("skip_reason") or source.get("reason") or source.get("status") or result.status
    )
    result.fix = list(source.get("fix") or [])
    result.evidence = bool(source.get("phase8_evidence"))
    result.evidence_note = str(source.get("phase8_evidence_note") or "")
    result.notes = list(source.get("warnings") or [])
    if not result.observed:
        result.observed = f"the harness exited {result.status} without a report: {payload.get('stderr_tail', '')[:200]}"


# --------------------------------------------------------------------------- #
# item 1: /ws live auth
# --------------------------------------------------------------------------- #
async def _stream_wav(
    ws_url: str,
    wav_path: Path,
    *,
    session_id: str,
    user_id: str,
    protocol: str,
    chunk_ms: int = 30,
    drain_s: float = 12.0,
) -> dict[str, Any]:
    samples, sample_rate = read_wav(str(wav_path))
    if sample_rate != 16000:
        from scipy import signal

        samples = signal.resample_poly(samples, 16000, sample_rate)
        sample_rate = 16000
    chunk_size = max(1, int(sample_rate * (chunk_ms / 1000)))
    collected: dict[str, Any] = {"auth_decisions": [], "transcripts": [], "tts": [], "events": []}
    import websockets

    async with websockets.connect(ws_url, ping_interval=None, open_timeout=15) as ws:
        await ws.send(
            json.dumps(
                encode_message(
                    protocol,
                    "start_session",
                    {
                        "session_id": session_id,
                        "sample_rate": sample_rate,
                        "channels": 1,
                        "encoding": "pcm_s16le",
                        "user_id": user_id,
                    },
                )
            )
        )
        await asyncio.wait_for(ws.recv(), timeout=15)  # ack
        seq = 0
        for offset in range(0, len(samples), chunk_size):
            chunk = samples[offset : offset + chunk_size]
            if chunk.size == 0:
                break
            await ws.send(
                json.dumps(
                    encode_message(
                        protocol,
                        "audio_chunk",
                        {
                            "session_id": session_id,
                            "seq": seq,
                            "chunk_ms": chunk_ms,
                            "pcm_bytes": b64encode(float_to_pcm16_bytes(chunk)),
                            "t_client_ms": int(time.time() * 1000),
                        },
                    )
                )
            )
            seq += 1
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.2)
                    _collect(raw, collected)
            except TimeoutError:
                pass
        await ws.send(json.dumps(encode_message(protocol, "end_session", {"session_id": session_id})))
        deadline = time.time() + drain_s
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                _collect(raw, collected)
            except TimeoutError:
                if collected["auth_decisions"]:
                    break
    return {"chunks_sent": seq, "duration_s": round(len(samples) / sample_rate, 2), **collected}


def _collect(raw: str, collected: dict[str, Any]) -> None:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return
    frame = message.get("frame") if isinstance(message.get("frame"), dict) else message
    etype = frame.get("type")
    payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
    # The server sends protocol.encode("event", event_to_dict(event)), i.e. the
    # event envelope nests twice (frame -> event -> the event's own type/payload).
    for _ in range(4):
        if etype != "event" or not isinstance(payload.get("payload"), dict):
            break
        etype = payload.get("type", "event")
        payload = payload["payload"]
    if etype == "auth_decision":
        collected["auth_decisions"].append(
            {
                "accepted": payload.get("accepted"),
                "score": payload.get("score"),
                "auth_state": payload.get("auth_state"),
                "match_source": payload.get("match_source"),
                "fallback_used": payload.get("fallback_used"),
                "reason": payload.get("reason"),
            }
        )
    elif etype in ("stt_final", "stt_partial", "stt_refine"):
        if payload.get("text"):
            collected["transcripts"].append(str(payload["text"]))
    elif etype == "tts_output":
        collected["tts"].append({"text": payload.get("text"), "path": payload.get("path")})
    elif etype not in ("ack", None):
        collected["events"].append(etype)


def item_ws_auth(args: argparse.Namespace) -> ItemResult:
    result = ItemResult(
        item="ws-auth",
        title="Live websocket voice auth on real speech",
        status="skipped",
        requirement=(
            "a running Sophia server with a real ECAPA-TDNN speaker embedder and real STT, plus one "
            "held-out clip of the enrolled speaker and (for the reject side) one clip of a different speaker"
        ),
    )
    reachable, ready = _server_reachable(args.base_url)
    if not reachable:
        result.fix = [
            f"python -m voice_agent.cli serve --config {args.config or '<config.yaml>'}",
            f"re-run with --base-url {args.base_url}",
        ]
        result.observed = f"no server answered {args.base_url}/readyz ({ready})"
        return result
    if isinstance(ready, dict):
        result.detail["readyz"] = {
            "status": ready.get("status"),
            "degraded": ready.get("degraded"),
            "blocking_failures": ready.get("blocking_failures"),
        }
    passphrase = args.passphrase or os.environ.get("SOPHIA_PASSPHRASE", "")
    if not passphrase:
        result.fix = [
            "export SOPHIA_PASSPHRASE=<the owner passphrase>, or pass --passphrase",
            "the /ws session is only authenticated against the enrolled owner voiceprint",
        ]
        result.observed = "no passphrase available to log in"
        return result
    missing = [name for name, value in (("--enroll-wav", args.enroll_wav),) if not value]
    if not args.verify_wav:
        missing.append("--verify-wav")
    if missing:
        result.fix = [
            f"pass {' and '.join(missing)}: a held-out clip of the enrolled speaker is the whole point of this check",
            "record live clips instead of reusing the enrolment clip; the same clip scores ~1.0 by construction",
        ]
        result.observed = f"missing required audio arguments: {', '.join(missing)}"
        return result
    for label, path in (("--enroll-wav", args.enroll_wav), ("--verify-wav", args.verify_wav), ("--impostor-wav", args.impostor_wav)):
        if path and not Path(path).exists():
            result.fix = [f"{path} does not exist; record the clip first"]
            result.observed = f"{label} path is missing"
            return result
    if not args.allow_enroll_side_effects:
        result.fix = [
            "enrolling writes a voiceprint version into the server's registry (and Neo4j lineage):",
            "  a) point --base-url at a scratch Sophia instance, or",
            "  b) re-run with --allow-enroll-side-effects against the real one",
        ]
        result.observed = "enrolment side effects were not confirmed"
        return result

    ws_url = args.base_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
    status, body = _json_http(
        f"{args.base_url}/auth/login", method="POST", json_body={"passphrase": passphrase}
    )
    if status != 200 or not isinstance(body, dict) or not body.get("authenticated"):
        result.status = "failed"
        result.observed = f"POST /auth/login returned {status}: {str(body)[:200]}"
        result.fix = ["check --passphrase against config.auth owner passphrase"]
        return result

    enroll_body, enroll_type = _multipart(
        {"user_id": args.user_id, "device_id": args.device_id}, "audio", Path(args.enroll_wav)
    )
    enroll_status, enroll_response = _json_http(
        f"{args.base_url}/voiceprints/enroll",
        method="POST",
        data=enroll_body,
        content_type=enroll_type,
    )
    result.detail["enroll"] = {
        "status": enroll_status,
        "response": enroll_response if isinstance(enroll_response, dict) else str(enroll_response)[:400],
        "wav": str(args.enroll_wav),
    }
    if enroll_status != 200:
        result.status = "failed"
        result.observed = f"POST /voiceprints/enroll returned {enroll_status}"
        result.fix = [
            "the clip must be long enough for enrolment (see auth policy) and readable",
            "an enroll endpoint needing auth.auth.enrollment_token also needs enrollment_token set",
        ]
        return result

    verify = asyncio.run(
        _stream_wav(
            ws_url,
            Path(args.verify_wav),
            session_id=f"phase8-verify-{uuid.uuid4().hex[:8]}",
            user_id=args.user_id,
            protocol=args.protocol,
        )
    )
    result.detail["verify"] = verify
    decisions = verify["auth_decisions"]
    if not decisions:
        result.status = "failed"
        result.observed = "no auth_decision event arrived on /ws"
        result.fix = [
            "check the server log: auth runs at end_session or on silence timeout",
            f"confirm the clip is longer than the verify window (sent {verify['duration_s']}s)",
        ]
        return result
    accepted = [item for item in decisions if item.get("accepted")]
    scores = [item["score"] for item in decisions if isinstance(item.get("score"), (int, float))]
    transcript = verify["transcripts"][-1] if verify["transcripts"] else ""
    if not accepted:
        result.status = "failed"
        result.observed = (
            f"held-out clip of the enrolled speaker was REJECTED (scores={scores}, "
            f"states={[item.get('auth_state') for item in decisions]})"
        )
        result.fix = [
            "re-enrol with more/longer clips of the same speaker, or lower auth.threshold deliberately",
            "if the embedder is the statistic fallback the score is not speaker similarity at all",
        ]
        return result

    if args.impostor_wav:
        impostor = asyncio.run(
            _stream_wav(
                ws_url,
                Path(args.impostor_wav),
                session_id=f"phase8-impostor-{uuid.uuid4().hex[:8]}",
                user_id=args.user_id,
                protocol=args.protocol,
            )
        )
        result.detail["impostor"] = impostor
        impostor_accepted = [item for item in impostor["auth_decisions"] if item.get("accepted")]
        if impostor_accepted:
            result.status = "failed"
            result.observed = (
                f"a different speaker was ACCEPTED (scores="
                f"{[item.get('score') for item in impostor['auth_decisions']]})"
            )
            result.fix = ["this is a false accept: raise auth.threshold or fix the enrolled voiceprint"]
            return result

    result.status = "ok"
    result.observed = (
        f"held-out speaker accepted (best score {max(scores):.4f}); "
        + (
            "different speaker rejected"
            if args.impostor_wav
            else "no impostor clip supplied, so the reject side was NOT tested"
        )
    )
    result.command = [
        sys.executable,
        str(Path(__file__).name),
        "--item",
        "ws-auth",
        f"--base-url {args.base_url}",
        f"--enroll-wav {args.enroll_wav}",
        f"--verify-wav {args.verify_wav}",
    ]
    notes: list[str] = []
    evidence = True
    if not args.impostor_wav:
        evidence = False
        notes.append("no --impostor-wav: only the accept side is proven")
    if not args.confirm_ecapa_backend:
        evidence = False
        notes.append(
            "the server does not expose its speaker-embedder backend, so real ECAPA-TDNN could not be "
            "confirmed from here; pass --confirm-ecapa-backend only if you verified it (pip show speechbrain "
            "+ the server log line)"
        )
    if transcript == STUB_TRANSCRIPT:
        evidence = False
        notes.append(
            f"STT returned the deterministic stub transcript {STUB_TRANSCRIPT!r} "
            "(faster_whisper_stream without a loaded model), so the audio was never really transcribed"
        )
    elif not transcript:
        evidence = False
        notes.append("no STT transcript arrived, so the live audio path was not exercised end to end")
    result.evidence = evidence
    result.notes = notes
    result.evidence_note = (
        "counts as Phase 8 evidence only with ECAPA confirmed, a real transcript, and the impostor reject side"
    )
    return result


# --------------------------------------------------------------------------- #
# item 2: meeting mode
# --------------------------------------------------------------------------- #
def item_meeting(args: argparse.Namespace) -> ItemResult:
    result = ItemResult(
        item="meeting",
        title="Meeting mode diarization + speaker-attributed transcript",
        status="skipped",
        requirement=(
            "a running Sophia server with ffmpeg plus a real speechbrain/ECAPA diarization backend, and "
            "real multi-speaker meeting audio with known speaker count"
        ),
    )
    reachable, ready = _server_reachable(args.base_url)
    if not reachable:
        result.fix = [f"start the server, then re-run against {args.base_url}"]
        result.observed = f"no server answered {args.base_url}/readyz ({ready})"
        return result
    if isinstance(ready, dict):
        result.detail["readyz"] = {
            "status": ready.get("status"),
            "degraded": ready.get("degraded"),
            "ffmpeg": (ready.get("checks") or {}).get("ffmpeg"),
        }
        ffmpeg_check = (ready.get("checks") or {}).get("ffmpeg") or {}
        if not ffmpeg_check.get("ok", True):
            result.observed = (
                f"readiness says ffmpeg is unusable: {ffmpeg_check.get('detail') or ffmpeg_check.get('reason')}"
            )
            result.fix = [
                "apt-get install -y ffmpeg   # /meeting/process converts the upload with it",
                "or mount a static ffmpeg and point ffmpeg.ffmpeg_path at it",
            ]
            result.status = "failed"
            return result
    if not args.meeting_audio:
        result.fix = [
            "pass --meeting-audio <real multi-speaker recording> (at least two distinct voices, "
            "several minutes is realistic)",
            "this cannot be faked with a synthetic tone: the point is the speaker labels",
        ]
        result.observed = "no meeting audio supplied"
        return result
    audio_path = Path(args.meeting_audio)
    if not audio_path.exists():
        result.fix = [f"{audio_path} does not exist"]
        result.observed = "meeting audio path is missing"
        return result

    form = {"summarize": "false"}
    if args.max_speakers:
        form["max_speakers"] = str(args.max_speakers)
    body, content_type = _multipart(form, "audio", audio_path)
    status, response = _json_http(
        f"{args.base_url}/meeting/process", method="POST", data=body, content_type=content_type
    )
    if status != 200 or not isinstance(response, dict) or not response.get("task_id"):
        result.status = "failed"
        result.observed = f"POST /meeting/process returned {status}: {str(response)[:300]}"
        result.fix = ["the upload policy may have rejected the file size/duration", "check the server log"]
        return result
    task_id = response["task_id"]
    result.detail["task_id"] = task_id
    result.detail["status_url"] = response.get("status_url")

    deadline = time.time() + args.meeting_timeout
    final: dict[str, Any] = {}
    steps: list[str] = []
    while time.time() < deadline:
        poll_status, poll = _json_http(f"{args.base_url}/meeting/status/{task_id}")
        if poll_status != 200 or not isinstance(poll, dict):
            result.status = "failed"
            result.observed = f"GET /meeting/status/{task_id} returned {poll_status}"
            return result
        step = str(poll.get("step") or "")
        if not steps or steps[-1] != step:
            steps.append(step)
        if poll.get("status") in ("done", "error", "failed", "cancelled"):
            final = poll
            break
        time.sleep(2.0)
    result.detail["steps"] = steps
    if not final:
        result.status = "failed"
        result.observed = f"meeting task {task_id} did not settle within {args.meeting_timeout}s (last step {steps[-1] if steps else '?'})"
        result.fix = ["raise --meeting-timeout, or check whether the diarization backend is still loading"]
        return result
    if final.get("status") != "done":
        result.status = "failed"
        result.observed = f"meeting task {task_id} finished as {final.get('status')}: {final.get('error')}"
        result.fix = [
            "ffmpeg is required for the conversion step",
            "a statistic-fallback embedder cannot diarize: install speechbrain + torch for real diarization",
        ]
        error = str(final.get("error") or "")
        if "ffmpeg" in error:
            result.notes.append(
                "ffmpeg was needed but not usable: with the default "
                "runtime.audio_conversion_enabled=true the ffmpeg readiness check is blocking, "
                "so a /readyz pass should have caught this — re-check /readyz and the "
                "SOPHIA_AUDIO_CONVERSION_ENABLED value"
            )
        return result

    payload = final.get("result") or {}
    speakers = payload.get("speakers") or []
    segments = payload.get("segments") or []
    speaker_keys = {
        str(segment.get("speaker") or segment.get("speaker_id") or "")
        for segment in segments
        if isinstance(segment, dict)
    }
    speaker_keys.discard("")
    result.detail["n_speakers_field"] = len(speakers) if isinstance(speakers, list) else None
    result.detail["n_segments"] = len(segments)
    result.detail["distinct_segment_speakers"] = sorted(speaker_keys)
    result.detail["segment_preview"] = [
        {
            "start_s": segment.get("start_s", segment.get("start")),
            "end_s": segment.get("end_s", segment.get("end")),
            "speaker": segment.get("speaker") or segment.get("speaker_id"),
            "text": str(segment.get("text") or "")[:80],
        }
        for segment in segments[:12]
        if isinstance(segment, dict)
    ]
    expected = args.expect_speakers
    if expected and len(speaker_keys) < expected:
        result.status = "failed"
        result.observed = (
            f"expected >= {expected} distinct speakers, the transcript has {len(speaker_keys)}: "
            f"{sorted(speaker_keys)}"
        )
        result.fix = [
            "a single-speaker result on multi-speaker audio means the diarization backend is not real",
            "check that speechbrain + torch are installed in the server environment",
        ]
        return result
    if len(speaker_keys) < 2:
        result.status = "failed"
        result.observed = (
            f"the transcript has {len(speaker_keys)} distinct speaker label(s); meeting mode without "
            "speaker attribution is not the feature"
        )
        result.fix = ["pass --expect-speakers 2 at minimum, with audio where two voices clearly alternate"]
        return result

    result.status = "ok"
    result.observed = (
        f"task {task_id} done: {len(speaker_keys)} distinct speakers, {len(segments)} segments"
    )
    result.evidence = bool(args.confirm_diarization_backend)
    result.notes = (
        []
        if args.confirm_diarization_backend
        else [
            "the server does not expose its diarization backend, so real ECAPA diarization could not be "
            "confirmed; pass --confirm-diarization-backend only if you verified it",
        ]
    )
    result.evidence_note = "counts as evidence only when the diarization backend was confirmed real"
    return result


# --------------------------------------------------------------------------- #
# items 3-5: delegated to their dedicated harnesses
# --------------------------------------------------------------------------- #
def item_tts(args: argparse.Namespace) -> ItemResult:
    result = ItemResult(
        item="tts",
        title="TTS voice quality against a live voice reference",
        status="skipped",
        requirement=(
            "a real TTS backend (OpenVoice / Coqui / Piper) with model assets, a live voice reference clip, "
            "and a human listening panel for MOS"
        ),
    )
    script_args = ["--out-dir", str(Path(args.out_dir) / "tts")]
    if args.config:
        script_args += ["--config", args.config]
    if args.reference_wav:
        script_args += ["--reference-wav", args.reference_wav]
    if args.phrases_file:
        script_args += ["--phrases", args.phrases_file]
    if args.max_phrases:
        script_args += ["--max-phrases", str(args.max_phrases)]
    code, stdout, stderr = _run_script(TTS_SCRIPT, script_args)
    result.status, payload = _script_result(code, stdout, stderr, TTS_SCRIPT)
    result.command = [sys.executable, str(TTS_SCRIPT), *script_args]
    _merge_child(result, payload, _child_report(Path(args.out_dir) / "tts", "tts_quality_report.json"))
    metrics = (result.detail.get("harness") or {}).get("metrics") or {}
    if result.status == "ok" and metrics.get("mos"):
        result.notes.append(
            "WER and similarity are machine metrics; PLAN.md's human MOS still has to be filled in by "
            "listening to wav/ in the report directory"
        )
    return result


def item_dispatch(args: argparse.Namespace) -> ItemResult:
    result = ItemResult(
        item="dispatch",
        title="Real AssistX voice-event dispatch",
        status="skipped",
        requirement=(
            "ASSISTX_VOICE_WEBHOOK_BASE_URL pointing at the real AssistX instance and a matching "
            "ASSISTX_VOICE_WEBHOOK_SECRET (plus a running Sophia server for the cross-check)"
        ),
    )
    script_args: list[str] = ["--out-dir", str(Path(args.out_dir) / "dispatch")]
    if args.base_url:
        script_args += ["--server-base-url", args.base_url]
    if args.confirm_dispatch_side_effects:
        script_args.append("--confirm-side-effects")
    code, stdout, stderr = _run_script(DISPATCH_SCRIPT, script_args)
    result.status, payload = _script_result(code, stdout, stderr, DISPATCH_SCRIPT)
    result.command = [sys.executable, str(DISPATCH_SCRIPT), *script_args]
    _merge_child(
        result, payload, _child_report(Path(args.out_dir) / "dispatch", "dispatch_live_probe.json")
    )
    if result.status == "ok" and not args.confirm_dispatch_side_effects:
        result.evidence = False
        result.notes.append(
            "only the non-actionable conversation-ended event was sent; pass --confirm-dispatch-side-effects "
            "to prove an actionable task_created event is accepted too (that creates a REAL AssistX task)"
        )
    return result


def item_fallback(args: argparse.Namespace) -> ItemResult:
    result = ItemResult(
        item="fallback",
        title="Neo4j historical candidate fallback on held-out clips",
        status="skipped",
        requirement=(
            "a labelled held-out manifest of real clips (enrol + eval splits, with impostors), a Neo4j "
            "instance with credentials, and ECAPA-TDNN embeddings"
        ),
    )
    if not args.fallback_manifest:
        result.fix = [
            "pass --fallback-manifest <manifest.jsonl> of held-out real clips (columns: clip_id, path, "
            "speaker, device_id, split)",
            "the 2025 dashcam corpus is a known DIFFERENT speaker and may only appear as an impostor",
        ]
        result.observed = "no held-out manifest supplied"
        return result
    script_args = [
        "--manifest",
        args.fallback_manifest,
        "--out-dir",
        str(Path(args.out_dir) / "fallback"),
    ]
    if args.config:
        script_args += ["--config", args.config]
    if args.embeddings_cache:
        script_args += ["--embeddings-cache", args.embeddings_cache]
    code, stdout, stderr = _run_script(FALLBACK_SCRIPT, script_args)
    result.status, payload = _script_result(code, stdout, stderr, FALLBACK_SCRIPT)
    result.command = [sys.executable, str(FALLBACK_SCRIPT), *script_args]
    _merge_child(
        result, payload, _child_report(Path(args.out_dir) / "fallback", "fallback_candidate_report.json")
    )
    return result


RUNNERS = {
    "ws-auth": item_ws_auth,
    "meeting": item_meeting,
    "tts": item_tts,
    "dispatch": item_dispatch,
    "fallback": item_fallback,
}


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the five PLAN.md Phase 8 real-world checks.")
    parser.add_argument("--item", action="append", choices=[*ITEMS, "all"], default=None)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Running Sophia server base URL.")
    parser.add_argument("--config", default=None, help="Sophia YAML config passed to the harnesses.")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Where the per-item reports land.")
    parser.add_argument("--strict", action="store_true", help="Treat skipped items as failures too.")
    parser.add_argument("--json", action="store_true", help="Print the JSON report instead of the table.")
    # item 1
    parser.add_argument("--passphrase", default=None, help="Owner passphrase (default: $SOPHIA_PASSPHRASE).")
    parser.add_argument("--user-id", default="scott", help="User id to enrol/verify against.")
    parser.add_argument("--device-id", default="phase8-eval", help="Device id recorded with the enrolment.")
    parser.add_argument("--protocol", default="hermes_overlay_v1", help="Websocket protocol id.")
    parser.add_argument("--enroll-wav", default=None, help="Real clip of the target speaker to enrol.")
    parser.add_argument("--verify-wav", default=None, help="Held-out clip of the same speaker.")
    parser.add_argument("--impostor-wav", default=None, help="Held-out clip of a different speaker.")
    parser.add_argument(
        "--allow-enroll-side-effects",
        action="store_true",
        help="Confirm writing an enrolment voiceprint into the server registry/Neo4j.",
    )
    parser.add_argument(
        "--confirm-ecapa-backend",
        action="store_true",
        help="Assert the server's speaker embedder is real ECAPA-TDNN (verified out of band).",
    )
    # item 2
    parser.add_argument("--meeting-audio", default=None, help="Real multi-speaker meeting recording.")
    parser.add_argument("--expect-speakers", type=int, default=None, help="Minimum distinct speakers expected.")
    parser.add_argument("--max-speakers", type=int, default=None, help="max_speakers form value.")
    parser.add_argument("--meeting-timeout", type=float, default=900.0, help="Seconds to wait for the task.")
    parser.add_argument(
        "--confirm-diarization-backend",
        action="store_true",
        help="Assert the server's diarization backend is real speechbrain/ECAPA.",
    )
    # item 3
    parser.add_argument("--reference-wav", default=None, help="Live voice reference clip for TTS identity.")
    parser.add_argument("--phrases-file", default=None, help="File with one evaluation phrase per line.")
    parser.add_argument("--max-phrases", type=int, default=None, help="Only use the first N phrases.")
    # item 4
    parser.add_argument(
        "--confirm-dispatch-side-effects",
        action="store_true",
        help="Allow sending an actionable task_created event (creates a REAL AssistX task).",
    )
    # item 5
    parser.add_argument("--fallback-manifest", default=None, help="Held-out labelled clips manifest.")
    parser.add_argument("--embeddings-cache", default=None, help="Embeddings cache for the fallback eval.")
    return parser


def _table(results: list[ItemResult]) -> str:
    header = f"{'item':<10} {'status':<8} {'evidence':<9} observed"
    lines = [header, "-" * len(header)]
    for result in results:
        observed = result.observed.replace("\n", " ")
        if len(observed) > 96:
            observed = observed[:93] + "..."
        lines.append(f"{result.item:<10} {result.status:<8} {str(result.evidence):<9} {observed}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    selected = list(ITEMS) if not args.item or "all" in args.item else [name for name in ITEMS if name in args.item]
    results = []
    for name in selected:
        print(f"[phase8] {name} ...", flush=True)
        result = RUNNERS[name](args)
        results.append(result)
        print(f"[phase8] {name}: {result.status} -- {result.observed}", flush=True)
        if result.fix:
            for command in result.fix:
                print(f"         -> {command}")
    failed = [result for result in results if result.status == "failed"]
    skipped = [result for result in results if result.status == "skipped"]
    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": _now(),
        "base_url": args.base_url,
        "config": args.config,
        "python": sys.executable,
        "items": [result.to_dict() for result in results],
        "summary": {
            "ok": [result.item for result in results if result.status == "ok"],
            "failed": [result.item for result in failed],
            "skipped": [result.item for result in skipped],
            "phase8_evidence_items": [result.item for result in results if result.evidence],
        },
        "conclusion": (
            "every item ran and passed"
            if results and not failed and not skipped
            else "NOT complete: "
            + ", ".join(
                [f"{result.item} FAILED" for result in failed]
                + [f"{result.item} skipped" for result in skipped]
            )
        ),
        "note": (
            "phase8_evidence items only; a skipped or degraded item is never counted, and PLAN.md Phase 8 "
            "checkboxes stay unticked until a human runs these against real assets"
        ),
    }
    report_path = out_dir / "phase8_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print()
    print(_table(results))
    print(f"\nevidence items : {report['summary']['phase8_evidence_items'] or 'none'}")
    print(f"conclusion     : {report['conclusion']}")
    print(f"report         : {report_path}")
    if args.json:
        print()
        print(json.dumps(report, indent=2, default=str))
    if failed or (args.strict and skipped):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
