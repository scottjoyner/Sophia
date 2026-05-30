from pathlib import Path
import shutil
import subprocess
import sys


def test_app_reliability_patch_is_idempotent(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"
    script = repo / "voice-agent" / "scripts" / "patch_app_reliability.py"

    work = tmp_path / "work"
    target = work / "voice-agent" / "src" / "voice_agent" / "server"
    target.mkdir(parents=True)
    shutil.copy2(src, target / "app.py")

    scripts = work / "voice-agent" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(script, scripts / "patch_app_reliability.py")

    subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_app_reliability.py"],
        cwd=work,
        check=True,
    )
    patched = (target / "app.py").read_text(encoding="utf-8")

    assert "from .graph_outbox import GraphOutbox, replay_graph_outbox_items" in patched
    assert "from .readiness import build_readiness_report" in patched
    assert "from .trusted_sessions import TrustedSessionStore" in patched
    assert "from .upload_limits import" in patched
    assert "trusted_sessions = TrustedSessionStore" in patched
    assert "graph_outbox = GraphOutbox" in patched
    assert "app.state.trusted_sessions = trusted_sessions" in patched
    assert "app.state.graph_outbox = graph_outbox" in patched
    assert "return build_readiness_report(config)" in patched
    assert '@app.get("/graph/outbox/status")' in patched
    assert "graph_outbox.summary()" in patched
    assert 'return {"ok": True, **graph_outbox.summary()}' in patched
    assert '@app.post("/graph/outbox/replay")' in patched
    assert '@app.get("/session/status")' in patched
    assert '@app.post("/session/clear")' in patched
    assert "trusted_sessions.upsert" in patched
    assert "trusted_session" in patched
    assert "VERIFY_AUDIO_POLICY" in patched
    assert "CAPTURE_AUDIO_POLICY" in patched
    assert "VOICEPRINT_AUDIO_POLICY" in patched
    assert "MEETING_AUDIO_POLICY" in patched
    assert "read_upload_with_limits(audio, MEETING_AUDIO_POLICY)" in patched
    assert "graph_pending" in patched
    assert "graph_outbox_id" in patched
    assert "graph_outbox.enqueue" in patched
    assert "idempotency_key=f\"capture:{capture_id}\"" in patched

    second = subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_app_reliability.py"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "already applied" in second.stdout
    assert (target / "app.py").read_text(encoding="utf-8") == patched
