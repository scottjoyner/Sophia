from __future__ import annotations

from pathlib import Path

import numpy as np

from ..config import AppConfig


class OpenVoiceTTS:
    def __init__(self, config: AppConfig):
        self.sample_rate = 16000
        self.voice = config.tts.voice
        self.speaker_wav = config.tts.speaker_wav
        self.model_path = config.tts.openvoice_model_path
        self.device = "cuda" if config.tts.use_gpu else "cpu"
        self._tts_model = None
        self._tone_converter = None
        self._src_se = None
        self._target_se = None
        self._base_speaker_id = None
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            import torch
        except ImportError:
            raise RuntimeError("torch is required for OpenVoice TTS")

        if not self.speaker_wav or not Path(self.speaker_wav).exists():
            raise RuntimeError(f"speaker_wav not found: {self.speaker_wav}")

        model_path = Path(self.model_path) if self.model_path else None

        try:
            from openvoice import se_extractor
            from openvoice.api import ToneColorConverter
        except ImportError:
            raise RuntimeError(
                "openvoice package is required. Install with: pip install openvoice"
            )

        converter_dir = str(model_path / "converter") if model_path else None
        if converter_dir and Path(converter_dir).exists():
            self._tone_converter = ToneColorConverter(
                f"{converter_dir}/config.json", device=self.device
            )
            self._tone_converter.load_ckpt(f"{converter_dir}/checkpoint.pth")
        else:
            raise RuntimeError(
                f"OpenVoice converter model not found at {converter_dir}. "
                "Download from https://github.com/myshell-ai/OpenVoice"
            )

        self._target_se, _ = se_extractor.get_se(
            self.speaker_wav, self._tone_converter, vad=True
        )

        try:
            from melo.api import TTS
        except ImportError:
            raise RuntimeError("melo package is required for the base TTS model")

        model_name = "EN_NEWEST"
        self._tts_model = TTS(language=model_name, device=self.device)
        spk2id = dict(self._tts_model.hps.data["spk2id"].items())
        self._base_speaker_id = spk2id.get("EN-Newest", next(iter(spk2id.values()), 0))

        try:
            base_path = str(model_path / "base_speakers" / "ses" / "en-us-se.pth") if model_path else None
            if base_path and Path(base_path).exists():
                self._src_se = torch.load(base_path, map_location=self.device)
            else:
                base_wav = str(model_path / "base_speakers" / "en-us.wav") if model_path else None
                if base_wav and Path(base_wav).exists():
                    self._src_se, _ = se_extractor.get_se(base_wav, self._tone_converter, vad=True)
                else:
                    raise RuntimeError("Base speaker embedding not found")
        except Exception:
            raise RuntimeError(
                "Base speaker embedding could not be loaded. "
                "Ensure OpenVoice model files are in the model path."
            )

        self._loaded = True

    def synthesize(self, text: str) -> np.ndarray:
        self._load()
        if not self._tts_model or not self._tone_converter:
            return np.zeros(int(self.sample_rate * 0.5), dtype=np.float32)

        import tempfile

        tmp_dir = Path(tempfile.mkdtemp(prefix="openvoice_"))
        try:
            src_path = str(tmp_dir / "src.wav")
            out_path = str(tmp_dir / "out.wav")

            self._tts_model.tts_to_file(
                text, self._base_speaker_id, src_path, speed=1.0
            )

            self._tone_converter.convert(
                audio_src_path=src_path,
                src_se=self._src_se,
                tgt_se=self._target_se,
                output_path=out_path,
            )

            from voice_agent.util.audio import read_wav
            audio, sr = read_wav(out_path)
            if sr != self.sample_rate:
                import scipy.signal
                ratio = self.sample_rate / sr
                new_len = int(len(audio) * ratio)
                audio = scipy.signal.resample(audio, new_len)
            return audio.astype(np.float32)
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
