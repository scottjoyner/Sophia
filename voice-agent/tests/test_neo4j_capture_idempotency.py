from pathlib import Path


def test_neo4j_capture_write_uses_merge_for_capture_and_transcript():
    repo = Path(__file__).resolve().parents[2]
    content = (repo / "voice-agent" / "src" / "voice_agent" / "auth" / "neo4j_ingest.py").read_text()

    assert "capture_dedupe_key" in content
    assert "client_capture_id" in content
    assert "MERGE (capture:SophiaCapture {dedupe_key: $capture_dedupe_key})" in content
    assert "MERGE (transcript:Transcript {id: $transcript_id})" in content
    assert "CREATE (capture:SophiaCapture" not in content
    assert "CREATE (transcript:Transcript" not in content
    assert "MERGE (audio)-[:CAPTURED_AS]->(capture)" in content
    assert "MERGE (transcript)-[:CAPTURED_IN]->(capture)" in content
