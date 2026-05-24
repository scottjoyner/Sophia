#!/usr/bin/env bash
# Setup voice cloning models inside the container.
# Run: docker exec voice-agent-sophia-voice-1 bash /app/scripts/setup_voice_clone.sh openvoice
# or:  docker exec voice-agent-sophia-voice-1 bash /app/scripts/setup_voice_clone.sh coqui
# or:  docker exec voice-agent-sophia-voice-1 bash /app/scripts/setup_voice_clone.sh all

set -euo pipefail

MODELS_DIR="${MODELS_DIR:-/models}"
OPENVOICE_DIR="${MODELS_DIR}/openvoice"

install_openvoice() {
    echo "=== Installing OpenVoice v2 ==="
    pip install --no-cache-dir openvoice melo
    mkdir -p "${OPENVOICE_DIR}/converter"
    mkdir -p "${OPENVOICE_DIR}/base_speakers/ses"

    echo "Downloading OpenVoice converter checkpoint..."
    curl -fsSL -o "${OPENVOICE_DIR}/converter/checkpoint.pth" \
        "https://myshell-public.s3.us-west-2.amazonaws.com/openvoice/checkpoints_v2/converter/checkpoint.pth"
    curl -fsSL -o "${OPENVOICE_DIR}/converter/config.json" \
        "https://myshell-public.s3.us-west-2.amazonaws.com/openvoice/checkpoints_v2/converter/config.json"

    echo "Downloading base speaker embedding..."
    curl -fsSL -o "${OPENVOICE_DIR}/base_speakers/ses/en-us-se.pth" \
        "https://myshell-public.s3.us-west-2.amazonaws.com/openvoice/checkpoints_v2/base_speakers/ses/en-us-se.pth"

    echo "OpenVoice v2 setup complete."
}

install_coqui() {
    echo "=== Installing Coqui XTTS ==="
    pip install --no-cache-dir "TTS>=0.22"

    echo "Coqui XTTS will auto-download the model on first use."
    echo "To pre-download: python -c 'from TTS.api import TTS; TTS(\"tts_models/multilingual/multi-dataset/xtts_v2\")'"
    echo "Coqui setup complete."
}

case "${1:-all}" in
    openvoice) install_openvoice ;;
    coqui) install_coqui ;;
    all)
        install_openvoice
        install_coqui
        ;;
    *)
        echo "Usage: $0 {openvoice|coqui|all}"
        exit 1
        ;;
esac

echo "Done."
