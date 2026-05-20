from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import List

import numpy as np
import uvicorn

from .auth.enroll import enroll_from_files
from .auth.verify import verify_audio_segment
from .bench.replay_client import replay_wav
from .bench.report import generate_report
from .bench.runner import run_bench
from .config import AppConfig, load_config
from .server.app import create_app
from .util.audio import read_wav, write_wav


def _load_config(path: str | None) -> AppConfig:
    return load_config(path)


def cmd_serve(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port)


def cmd_enroll(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    files: List[str] = []
    if args.mic:
        files = _record_phrases(config, args.user, args.phrases, args.n)
    elif args.audio_dir:
        files = [str(p) for p in Path(args.audio_dir).glob("*.wav")]
    elif args.files:
        files = args.files
    else:
        print("No audio files provided for enrollment", file=sys.stderr)
        sys.exit(1)
    enroll_from_files(config, args.user, files)
    print(f"Enrolled {args.user} with {len(files)} samples")


def cmd_verify(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    if args.mic:
        audio, sr = _record_audio(args.seconds)
        result = verify_audio_segment(config, "verify", args.user, audio, sr)
        print(result)
    elif args.wav:
        audio, sr = read_wav(args.wav)
        result = verify_audio_segment(config, "verify", args.user, audio, sr)
        print(result)
    else:
        print("Provide --wav for verification", file=sys.stderr)
        sys.exit(1)


def cmd_replay(args: argparse.Namespace) -> None:
    asyncio.run(replay_wav(args.url, args.wav, args.user, session_id=args.session_id))


def cmd_mic(args: argparse.Namespace) -> None:
    if importlib.util.find_spec("sounddevice") is None:
        print("sounddevice not available", file=sys.stderr)
        sys.exit(1)
    import sounddevice as sd
    import websockets
    import json
    from .util.audio import b64encode, float_to_pcm16_bytes
    from .util.time import now_ms

    async def stream() -> None:
        sample_rate = 16000
        chunk_ms = 30
        chunk_size = int(sample_rate * chunk_ms / 1000)
        async with websockets.connect(args.url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "start_session",
                        "payload": {
                            "session_id": args.session_id,
                            "sample_rate": sample_rate,
                            "channels": 1,
                            "encoding": "pcm_s16le",
                            "user_id": args.user,
                        },
                    }
                )
            )
            await ws.recv()
            for _ in range(args.seconds * 1000 // chunk_ms):
                audio = sd.rec(chunk_size, samplerate=sample_rate, channels=1, dtype="float32")
                sd.wait()
                pcm = float_to_pcm16_bytes(audio.flatten())
                await ws.send(
                    json.dumps(
                        {
                            "type": "audio_chunk",
                            "payload": {
                                "session_id": args.session_id,
                                "seq": 0,
                                "chunk_ms": chunk_ms,
                                "pcm_bytes": b64encode(pcm),
                                "t_client_ms": now_ms(),
                            },
                        }
                    )
                )
                await ws.recv()
            await ws.send(json.dumps({"type": "end_session", "payload": {"session_id": args.session_id}}))
            await ws.recv()

    asyncio.run(stream())


def cmd_bench(args: argparse.Namespace) -> None:
    run_bench(args.dataset, args.out, args.url)


def cmd_report(args: argparse.Namespace) -> None:
    out_path = Path(args.run).parent / "report.md"
    generate_report(args.run, str(out_path))
    print(f"Report written to {out_path}")


def cmd_ingest_dir(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    files = [str(p) for p in Path(args.audio_dir).glob("*.wav")]
    enroll_from_files(config, args.user, files)
    print(f"Ingested {len(files)} files for {args.user}")


def cmd_ingest_auto_ingest(args: argparse.Namespace) -> None:
    if importlib.util.find_spec("neo4j") is None:
        print("neo4j driver not installed", file=sys.stderr)
        sys.exit(1)
    from neo4j import GraphDatabase
    config = _load_config(args.config)
    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_pass))
    files: List[str] = []
    with driver.session() as session:
        if args.speaker_node_id:
            query = "MATCH (s:Speaker) WHERE id(s)=$id MATCH (s)-[:SPOKE_IN]->(a:Audio) RETURN a.path as path"
            result = session.run(query, id=int(args.speaker_node_id))
        else:
            query = "MATCH (a:Audio) RETURN a.path as path"
            result = session.run(query)
        for record in result:
            files.append(record["path"])
    enroll_from_files(config, args.user, files)
    print(f"Ingested {len(files)} files from Neo4j for {args.user}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--config", default=None)
    serve.set_defaults(func=cmd_serve)

    enroll = sub.add_parser("enroll")
    enroll.add_argument("--user", required=True)
    enroll.add_argument("--files", nargs="*")
    enroll.add_argument("--audio-dir")
    enroll.add_argument("--mic", action="store_true")
    enroll.add_argument("--phrases", default="tests/fixtures/enroll_phrases.txt")
    enroll.add_argument("--n", type=int, default=3)
    enroll.add_argument("--config", default=None)
    enroll.set_defaults(func=cmd_enroll)

    verify = sub.add_parser("verify")
    verify.add_argument("--user", required=True)
    verify.add_argument("--wav")
    verify.add_argument("--mic", action="store_true")
    verify.add_argument("--seconds", type=int, default=3)
    verify.add_argument("--config", default=None)
    verify.set_defaults(func=cmd_verify)

    replay = sub.add_parser("replay")
    replay.add_argument("--url", required=True)
    replay.add_argument("--wav", required=True)
    replay.add_argument("--user", default="default")
    replay.add_argument("--session-id", default="session")
    replay.set_defaults(func=cmd_replay)

    bench = sub.add_parser("bench")
    bench.add_argument("--dataset", required=True)
    bench.add_argument("--out", required=True)
    bench.add_argument("--url", default="ws://localhost:8765/ws")
    bench.set_defaults(func=cmd_bench)

    report = sub.add_parser("report")
    report.add_argument("--run", required=True)
    report.set_defaults(func=cmd_report)

    ingest_dir = sub.add_parser("ingest-dir")
    ingest_dir.add_argument("--user", required=True)
    ingest_dir.add_argument("--audio-dir", required=True)
    ingest_dir.add_argument("--config", default=None)
    ingest_dir.set_defaults(func=cmd_ingest_dir)

    ingest_auto = sub.add_parser("ingest-auto-ingest")
    ingest_auto.add_argument("--neo4j-uri", required=True)
    ingest_auto.add_argument("--neo4j-user", required=True)
    ingest_auto.add_argument("--neo4j-pass", required=True)
    ingest_auto.add_argument("--user", required=True)
    ingest_auto.add_argument("--speaker-node-id")
    ingest_auto.add_argument("--config", default=None)
    ingest_auto.set_defaults(func=cmd_ingest_auto_ingest)

    mic = sub.add_parser("mic")
    mic.add_argument("--url", required=True)
    mic.add_argument("--user", default="default")
    mic.add_argument("--session-id", default="mic-session")
    mic.add_argument("--seconds", type=int, default=10)
    mic.set_defaults(func=cmd_mic)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


def _record_audio(seconds: int) -> tuple[np.ndarray, int]:
    if importlib.util.find_spec("sounddevice") is None:
        print("sounddevice not available", file=sys.stderr)
        sys.exit(1)
    import sounddevice as sd

    sample_rate = 16000
    audio = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="float32")
    sd.wait()
    return audio.flatten(), sample_rate


def _record_phrases(config: AppConfig, user_id: str, phrases_path: str, n: int) -> List[str]:
    phrases = Path(phrases_path).read_text(encoding="utf-8").splitlines()
    paths: List[str] = []
    for idx in range(n):
        phrase = phrases[idx % len(phrases)] if phrases else "Please say the enrollment phrase."
        print(f"Say: {phrase}")
        audio, sr = _record_audio(3)
        out_path = Path(config.paths.artifacts_dir) / "enroll" / f"{user_id}_{idx}.wav"
        write_wav(out_path, audio, sr)
        paths.append(str(out_path))
    return paths


if __name__ == "__main__":
    main()
