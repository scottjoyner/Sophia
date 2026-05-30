import json
import subprocess
import sys
from pathlib import Path

from voice_agent.server.graph_outbox import GraphOutbox


def test_replay_graph_outbox_script_reports_missing_neo4j_password(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "voice-agent" / "scripts" / "replay_graph_outbox.py"
    artifacts = tmp_path / "runs"
    outbox = GraphOutbox(artifacts / "graph_outbox.sqlite")
    outbox.enqueue(
        kind="capture",
        idempotency_key="capture:test",
        payload={
            "user_id": "scott",
            "capture_id": "test",
            "transcript": "hello",
            "audio_path": "/tmp/test.webm",
            "content_type": "audio/webm",
            "duration_ms": 1000,
            "metadata": {},
            "context": {},
        },
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--artifacts-dir",
            str(artifacts),
            "--json",
        ],
        cwd=repo / "voice-agent",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["before"] == {"pending": 1}
    assert payload["result"]["reason"] == "Neo4j password not configured"
    assert payload["after"] == {"pending": 1}
