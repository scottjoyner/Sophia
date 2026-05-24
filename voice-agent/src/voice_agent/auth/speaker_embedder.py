from __future__ import annotations

from typing import List

import importlib.util
import numpy as np


class SpeakerEmbedder:
    def __init__(self):
        self.model = None
        if importlib.util.find_spec("speechbrain") is not None:
            from speechbrain.inference import EncoderClassifier

            self.model = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb")

    def embed(self, samples: np.ndarray, sample_rate: int) -> List[float]:
        if self.model is None:
            mean = float(np.mean(samples))
            std = float(np.std(samples))
            energy = float(np.mean(np.abs(samples)))
            return [mean, std, energy]
        import torch

        target_sr = 16000
        if sample_rate != target_sr:
            from scipy import signal
            samples = signal.resample_poly(samples, target_sr, sample_rate)
            sample_rate = target_sr

        tensor = torch.tensor(samples).unsqueeze(0)
        embedding = self.model.encode_batch(tensor).squeeze().cpu().numpy()
        return embedding.tolist()
