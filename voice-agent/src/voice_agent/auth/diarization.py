from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from .speaker_embedder import SpeakerEmbedder


def diarize(
    audio: np.ndarray,
    sample_rate: int,
    window_s: float = 1.5,
    stride_s: float = 0.5,
    min_segment_s: float = 0.5,
    distance_threshold: float = 0.5,
) -> List[Dict]:
    segments = _segment_audio(audio, sample_rate, window_s, stride_s, min_segment_s)
    if len(segments) < 2:
        return [{"start": 0.0, "end": len(audio) / sample_rate, "speaker": 0}]

    embedder = SpeakerEmbedder()
    embeddings = []
    for seg in segments:
        start_samp = int(seg["start"] * sample_rate)
        end_samp = int(seg["end"] * sample_rate)
        chunk = audio[start_samp:end_samp]
        if len(chunk) < int(sample_rate * 0.3):
            emb = np.zeros(192)
        else:
            emb = np.array(embedder.embed(chunk, sample_rate))
        embeddings.append(emb)

    emb_matrix = np.array(embeddings)
    energy = np.linalg.norm(emb_matrix, axis=1)
    valid = energy > 1e-6
    if valid.sum() < 1:
        return [{"start": 0.0, "end": len(audio) / sample_rate, "speaker": 0}]

    emb_valid = emb_matrix[valid]
    n_clusters = max(1, min(len(emb_valid) - 1, _estimate_clusters(emb_valid)))
    if n_clusters < 2:
        labels = np.zeros(len(segments), dtype=int)
    else:
        model = AgglomerativeClustering(
            n_clusters=n_clusters,
            metric="cosine",
            linkage="average",
        )
        cluster_labels = model.fit_predict(emb_valid)
        labels = np.zeros(len(segments), dtype=int)
        labels[valid] = cluster_labels
        labels[~valid] = -1

    return _build_timeline(segments, labels, min_segment_s)


def identify_speakers(
    segments: List[Dict],
    registry: Dict,
    embedder: SpeakerEmbedder,
    sample_rate: int,
    audio: np.ndarray,
    threshold: float = 0.50,
) -> List[Dict]:
    for seg in segments:
        spk = seg["speaker"]
        if spk < 0:
            seg["name"] = "?"
            continue
        start_samp = int(seg["start"] * sample_rate)
        end_samp = int(seg["end"] * sample_rate)
        chunk = audio[start_samp:end_samp]
        if len(chunk) < int(sample_rate * 0.3):
            seg["name"] = f"Speaker {spk + 1}"
            continue
        emb = np.array(embedder.embed(chunk, sample_rate))
        best_name = None
        best_score = 0.0
        for user_id, record in registry.items():
            stored = np.array(record["embedding"])
            score = float(emb @ stored / (np.linalg.norm(emb) * np.linalg.norm(stored)))
            if score > best_score and score >= threshold:
                best_score = score
                best_name = user_id
        seg["name"] = best_name or f"Speaker {spk + 1}"
        seg["confidence"] = round(best_score, 4)
    return segments


def _segment_audio(
    audio: np.ndarray, sr: int, window_s: float, stride_s: float, min_s: float
) -> List[Dict]:
    n = len(audio)
    window_len = int(sr * window_s)
    stride_len = int(sr * stride_s)
    segs = []
    for start in range(0, n - window_len + 1, stride_len):
        end = start + window_len
        chunk = audio[start:end]
        rms = np.sqrt(np.mean(chunk ** 2))
        if rms > 0.01:
            segs.append({"start": start / sr, "end": end / sr})
    if not segs:
        return [{"start": 0.0, "end": n / sr}]
    merged = _merge_segments(segs, min_s)
    return merged


def _merge_segments(segs: List[Dict], min_s: float) -> List[Dict]:
    if not segs:
        return segs
    merged = [dict(segs[0])]
    for s in segs[1:]:
        gap = s["start"] - merged[-1]["end"]
        if gap < min_s:
            merged[-1]["end"] = max(merged[-1]["end"], s["end"])
        else:
            merged.append(dict(s))
    return merged


def _estimate_clusters(embeddings: np.ndarray) -> int:
    n = len(embeddings)
    if n <= 3:
        return 1
    from sklearn.metrics import silhouette_score
    best_k = 1
    best_score = -1
    for k in range(2, min(n, 8)):
        model = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average")
        labels = model.fit_predict(embeddings)
        if len(set(labels)) < 2:
            continue
        s = silhouette_score(embeddings, labels, metric="cosine")
        if s > best_score:
            best_score = s
            best_k = k
    return best_k


def _build_timeline(
    segments: List[Dict], labels: np.ndarray, min_segment_s: float
) -> List[Dict]:
    if not segments:
        return []
    timeline = []
    current_spk = labels[0]
    current_start = segments[0]["start"]
    for i, seg in enumerate(segments):
        spk = labels[i]
        if spk != current_spk:
            timeline.append({
                "start": round(current_start, 2),
                "end": round(seg["start"], 2),
                "speaker": int(current_spk),
            })
            current_spk = spk
            current_start = seg["start"]
    timeline.append({
        "start": round(current_start, 2),
        "end": round(segments[-1]["end"], 2),
        "speaker": int(current_spk),
    })
    filtered = [t for t in timeline if t["end"] - t["start"] >= min_segment_s]
    if not filtered:
        filtered = timeline[-1:]
    return filtered
