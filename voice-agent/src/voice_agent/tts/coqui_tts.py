from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from ..config import AppConfig


class CoquiTTS:
    def __init__(self, config: AppConfig):
        self.sample_rate = 24000
        self.voice = config.tts.voice
        self.speaker_wav = config.tts.speaker_wav
        self.model_name = config.tts.coqui_model_name
        self.device = "cuda" if config.tts.use_gpu else "cpu"
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return

        if not self.speaker_wav or not Path(self.speaker_wav).exists():
            raise RuntimeError(f"speaker_wav not found: {self.speaker_wav}")

        try:
            from TTS.api import TTS
        except ImportError:
            raise RuntimeError(
                "TTS package is required. Install with: pip install TTS"
            )

        # Patch TTS utils to handle PyTorch 2.12+ weights_only default change
        import TTS.utils.io as tts_io
        import torch
        _orig_fsspec = tts_io.load_fsspec
        def _patched_fsspec(path, map_location=None, cache=True, **kw):
            kw.setdefault("weights_only", False)
            return _orig_fsspec(path, map_location, cache, **kw)
        tts_io.load_fsspec = _patched_fsspec

        # Patch Xtts.load_checkpoint to use non-strict loading
        # The checkpoint was saved with inference wrappers but model init without
        import TTS.tts.models.xtts as xtts_module
        _orig_load_ckpt = xtts_module.Xtts.load_checkpoint
        def _patched_load_ckpt(self, *a, **kw):
            kw.setdefault("strict", False)
            return _orig_load_ckpt(self, *a, **kw)
        xtts_module.Xtts.load_checkpoint = _patched_load_ckpt

        self._model = TTS(self.model_name).to(self.device)

    def synthesize(self, text: str) -> np.ndarray:
        self._load()
        if self._model is None:
            return np.zeros(int(self.sample_rate * 0.5), dtype=np.float32)

        import tempfile
        from pathlib import Path

        tmp_dir = Path(tempfile.mkdtemp(prefix="coqui_"))
        try:
            out_path = str(tmp_dir / "out.wav")

            self._model.tts_to_file(
                text=text,
                speaker_wav=self.speaker_wav,
                language="en",
                file_path=out_path,
            )

            from voice_agent.util.audio import read_wav
            audio, sr = read_wav(out_path)
            if sr != self.sample_rate:
                from scipy import signal
                ratio = self.sample_rate / sr
                new_len = int(len(audio) * ratio)
                audio = signal.resample(audio, new_len)
            return audio.astype(np.float32)
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
