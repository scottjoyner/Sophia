#!/usr/bin/env python3
"""Build Scott's speaker fingerprint from extracted audio samples."""

import json
import numpy as np
from pathlib import Path
import torch
import speechbrain.inference.speaker as speaker_recognizer


def build_speaker_fingerprint(samples_dir, output_path, model_name='speechbrain/spkrec-ecapa-voxceleb'):
    """
    Build a speaker fingerprint by averaging embeddings from multiple samples.
    
    Args:
        samples_dir: Directory containing WAV files and manifest.jsonl
        output_path: Path to save the fingerprint
        model_name: Pre-trained model name from HuggingFace
    """
    print(f"Loading speaker recognition model: {model_name}")
    
    # Load the model (this downloads it on first run)
    try:
        model = speaker_recognizer.EncoderClassifier.from_hparams(
            source=model_name,
            savedir=f'/tmp/{model_name.replace("/", "_")}',
            run_opts={'device': 'cpu'}
        )
    except Exception as e:
        print(f"Error loading model: {e}")
        print("Trying alternative model...")
        model = speaker_recognizer.EncoderClassifier.from_hparams(
            source='speechbrain/spkrec-xvector-voxceleb',
            savedir=f'/tmp/spkrec_xvector_voxceleb',
            run_opts={'device': 'cpu'}
        )
    
    # Load manifest
    manifest_path = Path(samples_dir) / 'manifest.jsonl'
    samples = []
    with open(manifest_path, 'r') as f:
        for line in f:
            samples.append(json.loads(line))
    
    print(f"Loaded {len(samples)} samples from manifest")
    
    # Extract embeddings
    embeddings = []
    failed = []
    
    for i, sample in enumerate(samples):
        wav_path = Path(sample['path'])
        if not wav_path.exists():
            failed.append(sample)
            continue
        
        try:
            # Load audio
            import soundfile as sf
            audio, sr = sf.read(wav_path)
            
            # Convert to torch tensor (expects shape: [1, num_samples])
            audio_tensor = torch.FloatTensor(audio).unsqueeze(0)
            
            # Extract embedding
            with torch.no_grad():
                embedding = model.encode_batch(audio_tensor)
                # Flatten and normalize embedding
                embedding_flat = embedding.flatten()
                embedding_flat = embedding_flat / torch.norm(embedding_flat)
                embedding = embedding_flat.cpu().numpy()
            
            embeddings.append(embedding)
            
            if (i + 1) % 50 == 0:
                print(f"  Processed {i + 1}/{len(samples)} samples, {len(embeddings)} successful")
                
        except Exception as e:
            failed.append(sample)
            if len(failed) < 5:  # Print first few errors
                print(f"  Error processing {wav_path.name}: {e}")
    
    if not embeddings:
        print("ERROR: No successful embeddings extracted!")
        return None
    
    # Average embeddings to create fingerprint
    fingerprint = np.mean(embeddings, axis=0)
    fingerprint = fingerprint / np.linalg.norm(fingerprint)  # Normalize
    
    print(f"\nFingerprint stats:")
    print(f"  Dimension: {fingerprint.shape[0]}")
    print(f"  Mean: {np.mean(fingerprint):.6f}")
    print(f"  Std: {np.std(fingerprint):.6f}")
    print(f"  Min: {np.min(fingerprint):.6f}")
    print(f"  Max: {np.max(fingerprint):.6f}")
    
    # Save fingerprint
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save as numpy file
    np.save(output_path, fingerprint)
    print(f"\nFingerprint saved to: {output_path}")
    
    # Save metadata
    metadata = {
        'model': model_name,
        'num_samples': len(samples),
        'successful': len(embeddings),
        'failed': len(failed),
        'fingerprint_dim': fingerprint.shape[0],
        'fingerprint_mean': float(np.mean(fingerprint)),
        'fingerprint_std': float(np.std(fingerprint)),
    }
    
    metadata_path = output_path.with_suffix('.json')
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    print(f"Metadata saved to: {metadata_path}")
    
    return fingerprint


def test_fingerprint(fingerprint_path, samples_dir, top_k=5):
    """Test the fingerprint against samples to verify it works."""
    print(f"\nTesting fingerprint against samples...")
    
    # Load fingerprint
    fingerprint = np.load(fingerprint_path)
    
    # Load manifest
    manifest_path = Path(samples_dir) / 'manifest.jsonl'
    samples = []
    with open(manifest_path, 'r') as f:
        for line in f:
            samples.append(json.loads(line))
    
    # Extract embeddings and compute similarity
    scores = []
    for sample in samples:
        wav_path = Path(sample['path'])
        if not wav_path.exists():
            continue
        
        try:
            # Load audio
            import soundfile as sf
            import torch
            
            audio, sr = sf.read(wav_path)
            # Convert to torch tensor
            audio_tensor = torch.FloatTensor(audio).unsqueeze(0)
            
            # Extract embedding
            with torch.no_grad():
                embedding = model.encode_batch(audio_tensor)
                embedding = embedding / embedding.norm(dim=1, keepdim=True)
                embedding = embedding.squeeze().cpu().numpy()
            
            # Compute cosine similarity
            similarity = np.dot(fingerprint, embedding)
            scores.append((sample, similarity))
            
        except Exception as e:
            continue
    
    # Sort by similarity
    scores.sort(key=lambda x: x[1], reverse=True)
    
    print(f"\nTop {top_k} most similar samples:")
    for i, (sample, score) in enumerate(scores[:top_k]):
        print(f"  {i+1}. {score:.4f} - {sample['text'][:60]}...")
    
    # Show distribution
    all_scores = [s[1] for s in scores]
    print(f"\nScore distribution:")
    print(f"  Mean: {np.mean(all_scores):.4f}")
    print(f"  Std: {np.std(all_scores):.4f}")
    print(f"  Min: {np.min(all_scores):.4f}")
    print(f"  Max: {np.max(all_scores):.4f}")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Build speaker fingerprint')
    parser.add_argument('--samples-dir', default='/data/voice-insight/training/scott', help='Samples directory')
    parser.add_argument('--output', default='/data/voice-insight/fingerprints/scott.npy', help='Output path')
    parser.add_argument('--model', default='speechbrain/spkrec-ecapa-voxceleb', help='Model name')
    parser.add_argument('--test', action='store_true', help='Test fingerprint after building')
    args = parser.parse_args()
    
    fingerprint = build_speaker_fingerprint(args.samples_dir, args.output, args.model)
    
    if fingerprint is not None and args.test:
        test_fingerprint(args.output, args.samples_dir)
