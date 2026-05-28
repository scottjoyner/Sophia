from __future__ import annotations

import logging
from typing import List

import numpy as np

from ..config import AppConfig

logger = logging.getLogger(__name__)


class SpeakerEmbedder:
    def __init__(self, config: AppConfig | None = None):
        self.config = config
        self.target_sample_rate = 16000
        self.model = None
        try:
            from speechbrain.inference.speaker import EncoderClassifier
            self.model = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb")
        except Exception as exc:
            logger.warning(f"Could not load speechbrain model: {exc}. Using fallback embeddings.")

    def _prepare_audio(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if audio.size == 0:
            return audio

        audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 1.0:
            audio = audio / peak

        if sample_rate != self.target_sample_rate:
            audio = self._resample(audio, sample_rate, self.target_sample_rate)

        # Trim obvious leading/trailing silence but keep internal pauses. This helps ECAPA
        # compare speech content instead of device noise floor or long dead air.
        abs_audio = np.abs(audio)
        if abs_audio.size:
            threshold = max(0.005, float(np.percentile(abs_audio, 70)) * 0.25)
            voiced = np.where(abs_audio >= threshold)[0]
            if voiced.size:
                pad = int(0.15 * self.target_sample_rate)
                start = max(0, int(voiced[0]) - pad)
                end = min(audio.size, int(voiced[-1]) + pad)
                audio = audio[start:end]

        return np.asarray(audio, dtype=np.float32)

    @staticmethod
    def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
        if source_rate <= 0 or source_rate == target_rate or samples.size == 0:
            return samples.astype(np.float32)
        try:
            from scipy import signal
            # resample_poly is higher quality for audio
            duration = samples.size / float(source_rate)
            target_len = max(1, int(round(duration * target_rate)))
            return signal.resample(samples, target_len).astype(np.float32)
        except ImportError:
            duration = samples.size / float(source_rate)
            target_len = max(1, int(round(duration * target_rate)))
            source_x = np.linspace(0.0, duration, num=samples.size, endpoint=False)
            target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
            return np.interp(target_x, source_x, samples).astype(np.float32)

    def embed(self, samples: np.ndarray, sample_rate: int) -> List[float]:
        audio = self._prepare_audio(samples, sample_rate)
        if self.model is None:
            mean = float(np.mean(audio)) if audio.size else 0.0
            std = float(np.std(audio)) if audio.size else 0.0
            energy = float(np.mean(np.abs(audio))) if audio.size else 0.0
            # Return a simple 192-dim vector for compatibility with speechbrain-trained registries
            vec = np.zeros(192)
            vec[0] = mean
            vec[1] = std
            vec[2] = energy
            return vec.tolist()
            
        import torch
        tensor = torch.tensor(audio, dtype=torch.float32).unsqueeze(0)
        embedding = self.model.encode_batch(tensor).squeeze().cpu().numpy()
        return embedding.tolist()
