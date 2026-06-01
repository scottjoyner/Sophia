from pathlib import Path
import shutil
import subprocess
import sys


def _copy_patch_script(repo: Path, work: Path, name: str) -> None:
    scripts = work / "voice-agent" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(repo / "voice-agent" / "scripts" / name, scripts / name)


def test_graph_capture_reconciliation_patch_after_capture_idempotency(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    src = repo / "voice-agent" / "src" / "voice_agent" / "server" / "app.py"

    work = tmp_path / "work"
    target = work / "voice-agent" / "src" / "voice_agent" / "server"
    target.mkdir(parents=True)
    shutil.copy2(src, target / "app.py")

    for name in [
        "patch_capture_idempotency.py",
        "patch_graph_capture_reconciliation.py",
    ]:
        _copy_patch_script(repo, work, name)

    subprocess.run([sys.executable, "voice-agent/scripts/patch_capture_idempotency.py"], cwd=work, check=True)
    subprocess.run([sys.executable, "voice-agent/scripts/patch_graph_capture_reconciliation.py"], cwd=work, check=True)

    patched = (target / "app.py").read_text(encoding="utf-8")
    assert "lookup_capture_by_client_capture_id" in patched
    assert '"source": "neo4j"' in patched
    assert '"source": "idempotency_cache"' in patched
    assert "capture_idempotency.put(client_capture_id_clean" in patched
    assert "graph_lookup.get(\"found\")" in patched

    second = subprocess.run(
        [sys.executable, "voice-agent/scripts/patch_graph_capture_reconciliation.py"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "already applied" in second.stdout
    assert (target / "app.py").read_text(encoding="utf-8") == patched
