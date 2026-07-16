from __future__ import annotations

import argparse
import asyncio
import hmac
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import uvicorn

from .auth.enroll import EnrollmentError, enroll_from_files
from .auth.verify import verify_audio_segment
from .bench.replay_client import replay_wav
from .bench.report import generate_report
from .bench.runner import run_bench
from .config import AppConfig, load_config
from .server.app import create_app
from .server.protocols import encode_message
from .util.audio import read_wav, write_wav


def _load_config(path: str | None) -> AppConfig:
    return load_config(path)


def _require_owner_override(config: AppConfig, user_id: str, token: str | None) -> None:
    if user_id != config.auth.owner_user_id:
        raise EnrollmentError(
            f"Owner override can only enroll the configured owner user_id={config.auth.owner_user_id!r}; got {user_id!r}"
        )
    if not config.auth.owner_override_enabled:
        raise EnrollmentError("Owner override enrollment is disabled. Set SOPHIA_OWNER_OVERRIDE_ENABLED=true.")
    expected = config.auth.owner_override_token
    if not expected:
        raise EnrollmentError(
            "Owner override token is not configured. Set SOPHIA_OWNER_OVERRIDE_TOKEN or SOPHIA_OWNER_OVERRIDE_TOKEN_FILE."
        )
    if not token or not hmac.compare_digest(str(token), str(expected)):
        raise EnrollmentError("Invalid owner override token")


def _files_from_args(args: argparse.Namespace) -> list[str]:
    files: list[str] = []
    if getattr(args, "audio_dir", None):
        root = Path(args.audio_dir)
        patterns = getattr(args, "glob", None) or ["*.wav"]
        for pattern in patterns:
            files.extend(str(p) for p in root.glob(pattern))
    if getattr(args, "files", None):
        files.extend(args.files)
    # Preserve order but drop duplicates.
    return list(dict.fromkeys(files))


def cmd_serve(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port)


def cmd_enroll(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    files: list[str] = []
    if args.mic:
        files = _record_phrases(config, args.user, args.phrases, args.n)
    else:
        files = _files_from_args(args)
    if not files:
        print("No audio files provided for enrollment", file=sys.stderr)
        sys.exit(1)
    try:
        result = enroll_from_files(config, args.user, files, append=args.append, source=args.source)
    except EnrollmentError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2))


def cmd_owner_override_enroll(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    try:
        _require_owner_override(config, args.user, args.override_token)
        files = _files_from_args(args)
        if args.mic:
            files.extend(_record_phrases(config, args.user, args.phrases, args.n))
        if not files:
            raise EnrollmentError("No audio files provided for owner override enrollment")
        result = enroll_from_files(
            config,
            args.user,
            files,
            append=True,
            source=args.source,
            min_seconds=config.auth.owner_append_min_seconds,
            max_seconds=config.auth.owner_append_max_seconds,
        )
    except EnrollmentError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"ok": True, "override": "owner_token", **result}, indent=2))


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
    asyncio.run(
        replay_wav(args.url, args.wav, args.user, session_id=args.session_id, protocol=args.protocol)
    )


def cmd_mic(args: argparse.Namespace) -> None:
    if importlib.util.find_spec("sounddevice") is None:
        print("sounddevice not available", file=sys.stderr)
        sys.exit(1)
    import sounddevice as sd
    import websockets

    from .util.audio import b64encode, float_to_pcm16_bytes
    from .util.time import now_ms

    async def stream() -> None:
        sample_rate = 16000
        chunk_ms = 30
        chunk_size = int(sample_rate * chunk_ms / 1000)
        async with websockets.connect(args.url) as ws:
            await ws.send(
                json.dumps(
                    encode_message(
                        args.protocol,
                        "start_session",
                        {
                            "session_id": args.session_id,
                            "sample_rate": sample_rate,
                            "channels": 1,
                            "encoding": "pcm_s16le",
                            "user_id": args.user,
                        },
                    )
                )
            )
            await ws.recv()
            for _ in range(args.seconds * 1000 // chunk_ms):
                audio = sd.rec(chunk_size, samplerate=sample_rate, channels=1, dtype="float32")
                sd.wait()
                pcm = float_to_pcm16_bytes(audio.flatten())
                await ws.send(
                    json.dumps(
                        encode_message(
                            args.protocol,
                            "audio_chunk",
                            {
                                "session_id": args.session_id,
                                "seq": 0,
                                "chunk_ms": chunk_ms,
                                "pcm_bytes": b64encode(pcm),
                                "t_client_ms": now_ms(),
                            },
                        )
                    )
                )
                await ws.recv()
            await ws.send(json.dumps(encode_message(args.protocol, "end_session", {"session_id": args.session_id})))
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
    result = enroll_from_files(config, args.user, files, append=args.append, source="ingest-dir")
    print(json.dumps(result, indent=2))


def cmd_ingest_auto_ingest(args: argparse.Namespace) -> None:
    from .auth.neo4j_ingest import (
        collect_audio_paths_from_neo4j,
        mark_audio_files_enrolled,
    )

    config = _load_config(args.config)
    try:
        files = collect_audio_paths_from_neo4j(
            args.neo4j_uri,
            args.neo4j_user,
            args.neo4j_pass,
            speaker_node_id=args.speaker_node_id,
            speaker_name=args.speaker_name,
            limit=args.limit,
            database=args.neo4j_database,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    result = enroll_from_files(config, args.user, files, append=args.append, source="neo4j")
    # Close the ingest loop: advance the staged AudioFile nodes from pending to
    # enrolled so they are not re-pulled on the next run.
    version_id = result.get("version_id")
    if version_id and not result.get("errors"):
        try:
            advanced = mark_audio_files_enrolled(
                args.neo4j_uri,
                args.neo4j_user,
                args.neo4j_pass,
                paths=files,
                enrolled_user_id=args.user,
                version_id=version_id,
                database=args.neo4j_database,
            )
            result["audio_files_advanced"] = advanced
        except RuntimeError as exc:
            print(f"warning: could not mark audio files enrolled: {exc}", file=sys.stderr)
    print(json.dumps(result, indent=2))


def _add_file_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--files", nargs="*", help="Explicit audio files to enroll")
    parser.add_argument("--audio-dir", help="Directory of audio clips to enroll")
    parser.add_argument("--glob", nargs="*", default=["*.wav"], help="Glob patterns under --audio-dir; default: *.wav")


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
    _add_file_args(enroll)
    enroll.add_argument("--mic", action="store_true")
    enroll.add_argument("--phrases", default="tests/fixtures/enroll_phrases.txt")
    enroll.add_argument("--n", type=int, default=3)
    enroll.add_argument("--append", action="store_true", help="Append to the existing voiceprint instead of replacing it")
    enroll.add_argument("--source", default="manual")
    enroll.add_argument("--config", default=None)
    enroll.set_defaults(func=cmd_enroll)

    owner_enroll = sub.add_parser("owner-override-enroll")
    owner_enroll.add_argument("--user", default="scott")
    _add_file_args(owner_enroll)
    owner_enroll.add_argument("--mic", action="store_true")
    owner_enroll.add_argument("--phrases", default="tests/fixtures/enroll_phrases.txt")
    owner_enroll.add_argument("--n", type=int, default=5)
    owner_enroll.add_argument("--override-token", required=True)
    owner_enroll.add_argument("--source", default="owner_override")
    owner_enroll.add_argument("--config", default=None)
    owner_enroll.set_defaults(func=cmd_owner_override_enroll)

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
    replay.add_argument("--protocol", choices=["native_ws", "hermes_overlay_v1"], default="native_ws")
    replay.add_argument("--ref", help="Reference transcript for compatibility with older docs", default=None)
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
    ingest_dir.add_argument("--append", action="store_true")
    ingest_dir.add_argument("--config", default=None)
    ingest_dir.set_defaults(func=cmd_ingest_dir)

    ingest_auto = sub.add_parser("ingest-auto-ingest")
    ingest_auto.add_argument("--neo4j-uri", required=True)
    ingest_auto.add_argument("--neo4j-user", required=True)
    ingest_auto.add_argument("--neo4j-pass", required=True)
    ingest_auto.add_argument("--neo4j-database", default="neo4j")
    ingest_auto.add_argument("--user", required=True)
    ingest_auto.add_argument("--speaker-node-id")
    ingest_auto.add_argument("--speaker-name")
    ingest_auto.add_argument("--limit", type=int, default=200)
    ingest_auto.add_argument("--append", action="store_true")
    ingest_auto.add_argument("--config", default=None)
    ingest_auto.set_defaults(func=cmd_ingest_auto_ingest)

    mic = sub.add_parser("mic")
    mic.add_argument("--url", required=True)
    mic.add_argument("--user", default="default")
    mic.add_argument("--session-id", default="mic-session")
    mic.add_argument("--seconds", type=int, default=10)
    mic.add_argument("--protocol", choices=["native_ws", "hermes_overlay_v1"], default="native_ws")
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


def _record_phrases(config: AppConfig, user_id: str, phrases_path: str, n: int) -> list[str]:
    phrases = Path(phrases_path).read_text(encoding="utf-8").splitlines()
    paths: list[str] = []
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
