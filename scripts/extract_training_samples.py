#!/usr/bin/env python3
"""Extract training samples from NAS audio files using RTTM speaker labels and transcriptions."""

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def parse_rttm(rttm_path):
    """Parse RTTM file and return list of speaker segments."""
    segments = []
    with open(rttm_path, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 8:
                segments.append({
                    'start': float(parts[2]),
                    'duration': float(parts[3]),
                    'speaker': parts[7],
                })
    return segments


def parse_transcription_csv(csv_path):
    """Parse transcription CSV and return list of segments."""
    segments = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            segments.append({
                'start': float(row['StartTime']),
                'end': float(row['EndTime']),
                'text': row['Text'].strip(),
            })
    return segments


def identify_speaker_label(rttm_segments, trans_segments, target_speaker=None):
    """
    Identify which speaker label corresponds to Scott.
    Strategy: look for first-person speech patterns (I, me, my, mine) in the transcription
    and match to the RTTM speaker label.
    """
    first_person_patterns = [' i ', ' i,', ' i.', ' i!', ' i?', ' i ', ' me ', ' my ', ' mine ', 
                             ' i\'m ', ' i\'ve ', ' i\'ll ', ' i\'d ', 'myself', 'myself']
    
    speaker_first_person = {}
    speaker_text = {}
    
    for t_seg in trans_segments:
        text = t_seg['text'].lower()
        is_first_person = any(p in text for p in first_person_patterns)
        
        if is_first_person:
            # Find matching RTTM segment by time
            for r_seg in rttm_segments:
                if r_seg['start'] <= t_seg['start'] <= r_seg['start'] + r_seg['duration']:
                    spk = r_seg['speaker']
                    if spk not in speaker_first_person:
                        speaker_first_person[spk] = 0
                        speaker_text[spk] = []
                    speaker_first_person[spk] += 1
                    speaker_text[spk].append(t_seg['text'])
                    break
    
    if target_speaker:
        return target_speaker
    
    # Return the speaker with most first-person speech
    if speaker_first_person:
        return max(speaker_first_person, key=speaker_first_person.get)
    
    # Fallback: return the most frequent speaker
    speaker_counts = {}
    for seg in rttm_segments:
        spk = seg['speaker']
        speaker_counts[spk] = speaker_counts.get(spk, 0) + 1
    return max(speaker_counts, key=speaker_counts.get) if speaker_counts else None


def extract_audio_segment(mp3_path, start, duration, output_path, sr=16000):
    """Extract audio segment from MP3 file using ffmpeg."""
    cmd = [
        'ffmpeg', '-y', '-i', str(mp3_path),
        '-ss', str(start),
        '-t', str(duration),
        '-ar', str(sr),
        '-ac', '1',
        '-format', 'f32le',
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR extracting segment: {result.stderr}")
        return False
    return True


def extract_with_librosa(mp3_path, start, duration, output_path, sr=16000):
    """Extract audio segment using librosa and write proper WAV."""
    import librosa
    audio, _ = librosa.load(mp3_path, sr=sr, offset=start, duration=duration)
    # Write as proper WAV (not raw)
    sf.write(output_path, audio, sr, format='WAV', subtype='FLOAT')
    return True


def process_file(mp3_path, rttm_path, trans_csv_path, speaker_label, output_dir, min_duration=2.0, max_duration=15.0):
    """Process a single audio file and extract training samples."""
    samples = []
    
    # Parse RTTM and transcription
    rttm_segments = parse_rttm(rttm_path)
    trans_segments = parse_transcription_csv(trans_csv_path)
    
    # Filter RTTM segments for target speaker
    target_segments = [s for s in rttm_segments if s['speaker'] == speaker_label]
    
    # Match with transcription and filter by duration/text quality
    for t_seg in trans_segments:
        duration = t_seg['end'] - t_seg['start']
        
        # Check duration constraints
        if duration < min_duration or duration > max_duration:
            continue
        
        # Check text quality (skip very short or noisy transcripts)
        text = t_seg['text'].strip()
        if len(text) < 16:
            continue
        if text.startswith('I-104'):
            continue
        if 'music' in text.lower() or 'silence' in text.lower() or 'noise' in text.lower():
            continue
        
        # Find matching RTTM segment
        for r_seg in target_segments:
            if r_seg['start'] <= t_seg['start'] <= r_seg['start'] + r_seg['duration']:
                # Extract segment
                sample_id = Path(mp3_path).stem + f"_{r_seg['start']:.0f}"
                output_path = output_dir / f"{sample_id}.wav"
                
                if extract_with_librosa(mp3_path, r_seg['start'], r_seg['duration'], output_path):
                    samples.append({
                        'path': str(output_path),
                        'text': text,
                        'start': r_seg['start'],
                        'duration': r_seg['duration'],
                        'speaker': speaker_label,
                        'source_file': str(mp3_path),
                    })
                break
    
    return samples


def main():
    parser = argparse.ArgumentParser(description='Extract training samples from NAS audio files')
    parser.add_argument('--years', nargs='+', default=['2024', '2025', '2026'], help='Years to process')
    parser.add_argument('--months', nargs='+', type=int, help='Months to process (1-12)')
    parser.add_argument('--days', nargs='+', type=int, help='Days to process')
    parser.add_argument('--output', default='/mnt/S/sophia-ingest/voice-insight/training/scott', help='Output directory')
    parser.add_argument('--min-duration', type=float, default=2.0, help='Minimum segment duration')
    parser.add_argument('--max-duration', type=float, default=15.0, help='Maximum segment duration')
    parser.add_argument('--target-speaker', help='Target speaker label (auto-detect if not specified)')
    parser.add_argument('--max-files', type=int, default=50, help='Maximum files to process')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without executing')
    args = parser.parse_args()
    
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    nas_base = Path('/nas-fileserver/audio')
    
    # Collect files to process
    files_to_process = []
    for year in args.years:
        year_dir = nas_base / year
        if not year_dir.exists():
            print(f"Year {year} not found, skipping")
            continue
        
        for month in range(1, 13):
            if args.months and month not in args.months:
                continue
            
            month_dir = year_dir / f"{month:02d}"
            if not month_dir.exists():
                continue
            
            for day_dir in sorted(month_dir.iterdir()):
                if not day_dir.is_dir():
                    continue
                
                if args.days and int(day_dir.name) not in args.days:
                    continue
                
                # Find MP3 files with RTTM
                for mp3_file in sorted(day_dir.glob('*.mp3')):
                    rttm_file = day_dir / f"{mp3_file.stem}_speakers.rttm"
                    trans_csv = day_dir / f"{mp3_file.stem}_transcription.csv"
                    
                    if rttm_file.exists() and trans_csv.exists():
                        files_to_process.append((mp3_file, rttm_file, trans_csv))
                        
                        if len(files_to_process) >= args.max_files:
                            break
                
                if len(files_to_process) >= args.max_files:
                    break
            
            if len(files_to_process) >= args.max_files:
                break
        
        if len(files_to_process) >= args.max_files:
            break
    
    print(f"Found {len(files_to_process)} files to process")
    
    # Process files
    all_samples = []
    for i, (mp3_path, rttm_path, trans_csv) in enumerate(files_to_process):
        print(f"\nProcessing file {i+1}/{len(files_to_process)}: {mp3_path.name}")
        
        # Parse RTTM to identify speakers
        rttm_segments = parse_rttm(rttm_path)
        trans_segments = parse_transcription_csv(trans_csv)
        
        # Identify Scott's speaker label
        if args.target_speaker:
            speaker_label = args.target_speaker
        else:
            speaker_label = identify_speaker_label(rttm_segments, trans_segments)
        
        print(f"  Target speaker: {speaker_label}")
        
        # Process the file
        samples = process_file(
            mp3_path, rttm_path, trans_csv,
            speaker_label, output_dir,
            min_duration=args.min_duration,
            max_duration=args.max_duration
        )
        
        all_samples.extend(samples)
        print(f"  Extracted {len(samples)} samples")
    
    # Save manifest
    manifest_path = output_dir / 'manifest.jsonl'
    with open(manifest_path, 'w') as f:
        for sample in all_samples:
            f.write(json.dumps(sample) + '\n')
    
    print(f"\nTotal samples extracted: {len(all_samples)}")
    print(f"Manifest saved to: {manifest_path}")
    
    # Print sample stats
    if all_samples:
        durations = [s['duration'] for s in all_samples]
        texts = [s['text'] for s in all_samples]
        print(f"\nStats:")
        print(f"  Min duration: {min(durations):.2f}s")
        print(f"  Max duration: {max(durations):.2f}s")
        print(f"  Avg duration: {sum(durations)/len(durations):.2f}s")
        print(f"  Sample texts:")
        for t in texts[:5]:
            print(f"    - {t[:80]}...")


if __name__ == '__main__':
    main()
