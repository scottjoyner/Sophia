from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.preprocessing import normalize

from .speaker_embedder import SpeakerEmbedder


_SILERO_VAD = None


def _get_vad():
    global _SILERO_VAD
    if _SILERO_VAD is not None:
        return _SILERO_VAD
    import torch
    model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        force_reload=False,
        onnx=False,
    )
    (get_speech_timestamps, _, _, _, _) = utils
    _SILERO_VAD = (model, get_speech_timestamps)
    return _SILERO_VAD


def diarize(
    audio: np.ndarray,
    sample_rate: int,
    window_s: float = 1.5,
    max_speakers: Optional[int] = None,
    min_speakers: int = 1,
    min_segment_s: float = 0.5,
    speech_pad_s: float = 0.3,
) -> List[Dict]:
    vad_segments = _vad_segments(audio, sample_rate, speech_pad_s, min_segment_s)
    if not vad_segments:
        return [{"start": 0.0, "end": len(audio) / sample_rate, "speaker": 0, "cluster_confidence": 1.0}]
    chunks = _split_into_chunks(audio, sample_rate, vad_segments, window_s)
    if len(chunks) < 2:
        merged = _merge_segments([{"start": c["start"], "end": c["end"]} for c in chunks], min_segment_s)
        if not merged:
            return [{"start": 0.0, "end": len(audio) / sample_rate, "speaker": 0, "cluster_confidence": 1.0}]
        return [{"start": merged[0]["start"], "end": merged[-1]["end"], "speaker": 0, "cluster_confidence": 1.0}]

    embedder = SpeakerEmbedder()
    embeddings = []
    for c in chunks:
        chunk = audio[int(c["start"] * sample_rate):int(c["end"] * sample_rate)]
        if len(chunk) < int(sample_rate * 0.3):
            emb = np.zeros(192)
        else:
            emb = np.array(embedder.embed(chunk, sample_rate))
        embeddings.append(emb)

    emb_matrix = np.array(embeddings)
    energy = np.linalg.norm(emb_matrix, axis=1)
    valid = energy > 1e-6
    if valid.sum() < 1:
        merged = _merge_segments([{"start": c["start"], "end": c["end"]} for c in chunks], min_segment_s)
        return [{"start": merged[0]["start"], "end": merged[-1]["end"], "speaker": 0, "cluster_confidence": 1.0}]

    emb_valid = emb_matrix[valid]
    emb_normed = normalize(emb_valid, norm="l2")
    n = len(emb_normed)
    n_clusters = _pick_k(emb_normed, max_speakers, min_speakers, n)
    if n_clusters < 2:
        labels = np.zeros(len(chunks), dtype=int)
        confidences = np.ones(len(chunks))
    else:
        model = AgglomerativeClustering(
            n_clusters=n_clusters,
            metric="cosine",
            linkage="average",
        )
        cluster_labels = model.fit_predict(emb_normed)
        centroids = np.array([emb_normed[cluster_labels == k].mean(axis=0) for k in range(n_clusters)])
        centroids = normalize(centroids, norm="l2")
        sims = emb_normed @ centroids.T
        conf_vector = sims[np.arange(n), cluster_labels]
        labels = np.full(len(chunks), -1, dtype=int)
        labels[valid] = cluster_labels
        confidences = np.zeros(len(chunks))
        confidences[valid] = conf_vector

    timeline = _build_timeline(chunks, labels, confidences, min_segment_s)
    return timeline


def _vad_segments(
    audio: np.ndarray, sr: int, pad_s: float, min_s: float
) -> List[Dict]:
    try:
        model, get_speech_timestamps = _get_vad()
        import torch
        audio_t = torch.from_numpy(audio).float()
        raw = get_speech_timestamps(
            audio_t, model,
            threshold=0.5,
            min_speech_duration_ms=int(min_s * 1000),
            min_silence_duration_ms=250,
            return_seconds=True,
        )
        segs = [{"start": max(0, s["start"] - pad_s), "end": min(len(audio) / sr, s["end"] + pad_s)} for s in raw]
        if not segs:
            return []
        merged = [dict(segs[0])]
        for s in segs[1:]:
            if s["start"] - merged[-1]["end"] < min_s:
                merged[-1]["end"] = max(merged[-1]["end"], s["end"])
            else:
                merged.append(dict(s))
        return merged
    except Exception:
        return _segment_audio_fallback(audio, sr)


def _segment_audio_fallback(audio: np.ndarray, sr: int) -> List[Dict]:
    n = len(audio)
    window_len = int(sr * 0.5)
    stride_len = int(sr * 0.25)
    segs = []
    for start in range(0, n - window_len + 1, stride_len):
        end = start + window_len
        rms = np.sqrt(np.mean(audio[start:end] ** 2))
        if rms > 0.008:
            segs.append({"start": start / sr, "end": end / sr})
    merged = _merge_segments(segs, 0.3)
    return merged


def _split_into_chunks(
    audio: np.ndarray, sr: int, vad_segments: List[Dict], window_s: float
) -> List[Dict]:
    chunks = []
    for seg in vad_segments:
        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_dur = seg_end - seg_start
        if seg_dur <= window_s:
            chunks.append({"start": seg_start, "end": seg_end})
        else:
            n_windows = max(1, int(seg_dur / window_s))
            step = seg_dur / n_windows
            for i in range(n_windows):
                cs = seg_start + i * step
                ce = min(cs + window_s, seg_end)
                if ce - cs >= 0.3:
                    chunks.append({"start": cs, "end": ce})
    return chunks


def _pick_k(embeddings: np.ndarray, max_speakers: Optional[int], min_speakers: int, n: int) -> int:
    if n <= 2:
        return 1
    hi = n
    if max_speakers is not None:
        hi = min(max_speakers + 1, n)
    hi = min(hi, min(8, n))
    lo = max(2, min_speakers)
    if lo >= hi:
        return lo
    from sklearn.metrics import silhouette_score
    best_k = 1
    best_score = -1
    for k in range(lo, hi):
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
    chunks: List[Dict], labels: np.ndarray, confidences: np.ndarray, min_segment_s: float
) -> List[Dict]:
    if not chunks:
        return []
    timeline = []
    current_spk = labels[0]
    current_start = chunks[0]["start"]
    current_conf = confidences[0]
    for i, c in enumerate(chunks):
        spk = labels[i]
        if spk != current_spk:
            dur = c["start"] - current_start
            if dur >= min_segment_s:
                timeline.append({
                    "start": round(current_start, 2),
                    "end": round(c["start"], 2),
                    "speaker": int(current_spk) if current_spk >= 0 else -1,
                    "cluster_confidence": round(float(current_conf), 4),
                })
            current_spk = spk
            current_start = c["start"]
            current_conf = confidences[i]
        else:
            current_conf = max(current_conf, confidences[i])
    dur = chunks[-1]["end"] - current_start
    if dur >= min_segment_s:
        timeline.append({
            "start": round(current_start, 2),
            "end": round(chunks[-1]["end"], 2),
            "speaker": int(current_spk) if current_spk >= 0 else -1,
            "cluster_confidence": round(float(current_conf), 4),
        })
    if not timeline:
        timeline.append({
            "start": round(chunks[0]["start"], 2),
            "end": round(chunks[-1]["end"], 2),
            "speaker": 0,
            "cluster_confidence": 1.0,
        })
    return timeline


def _merge_segments(segs: List[Dict], min_gap: float) -> List[Dict]:
    if not segs:
        return segs
    merged = [dict(segs[0])]
    for s in segs[1:]:
        gap = s["start"] - merged[-1]["end"]
        if gap < min_gap:
            merged[-1]["end"] = max(merged[-1]["end"], s["end"])
        else:
            merged.append(dict(s))
    return merged


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
            stored = np.array(record["embedding"], dtype=np.float64).ravel()
            if stored.shape != emb.shape:
                continue
            score = float(emb @ stored / (np.linalg.norm(emb) * np.linalg.norm(stored)))
            if score > best_score and score >= threshold:
                best_score = score
                best_name = user_id
        seg["name"] = best_name or f"Speaker {spk + 1}"
        seg["confidence"] = round(best_score, 4)
    return segments
