# voice-agent

## Quickstart
```bash
pip install -e .
voice-agent serve --host 0.0.0.0 --port 8765 --config configs/dev.yaml
```

## CLI
```bash
voice-agent enroll --user scott --mic --phrases tests/fixtures/enroll_phrases.txt --n 5
voice-agent verify --user scott --mic --seconds 3
voice-agent mic --url ws://localhost:8765/ws --user scott
voice-agent replay --url ws://localhost:8765/ws --wav tests/fixtures/sample.wav --ref tests/fixtures/sample.txt --user scott
voice-agent bench --dataset dataset/manifest.jsonl --out runs/demo --config configs/bench.yaml
voice-agent report --run runs/demo/results.sqlite
```
