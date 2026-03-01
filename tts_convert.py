#!/usr/bin/env python3
"""
Kokoro TTS: Convert text or PDF files to audiobook.
Usage:
    python tts_convert.py --input script.txt --voice af_heart
    python tts_convert.py --input script.pdf --voice af_heart
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf


# ---------------------------------------------------------------------------
# Text preprocessing
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Clean raw text for TTS: fix hyphenation, normalize whitespace, remove artifacts."""
    # Fix hyphenation across line breaks (e.g. "embar-\nrassingly" -> "embarrassingly")
    text = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', text)

    # Remove page numbers (standalone numbers on their own line)
    text = re.sub(r'\n\s*\d+\s*\n', '\n', text)

    # Collapse multiple blank lines into two (paragraph boundary)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Normalize whitespace within lines (but keep paragraph breaks)
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        # Collapse multiple spaces into one
        line = re.sub(r'[ \t]+', ' ', line).strip()
        cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines)

    # Remove any non-printable characters except newlines
    text = re.sub(r'[^\S\n]+', ' ', text)

    return text.strip()


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from a PDF file using pdfplumber."""
    import pdfplumber

    print(f"Extracting text from PDF: {pdf_path}")
    all_text = []

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            page_text = page.extract_text()
            if page_text:
                all_text.append(page_text)
            if (i + 1) % 50 == 0 or (i + 1) == total_pages:
                print(f"  Extracted page {i + 1}/{total_pages}")

    full_text = '\n\n'.join(all_text)
    print(f"  Total characters extracted: {len(full_text):,}")
    return full_text


def load_input(input_path: str) -> str:
    """Load text from a .txt or .pdf file."""
    path = Path(input_path)
    if not path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    if path.suffix.lower() == '.pdf':
        raw = extract_text_from_pdf(input_path)
    elif path.suffix.lower() == '.txt':
        raw = path.read_text(encoding='utf-8')
    else:
        print(f"Error: Unsupported file type: {path.suffix}")
        sys.exit(1)

    cleaned = clean_text(raw)
    word_count = len(cleaned.split())
    print(f"Loaded {word_count:,} words from {path.name}")
    return cleaned


# ---------------------------------------------------------------------------
# Smart Chunking (anti-gibberish)
# ---------------------------------------------------------------------------
# Kokoro's token limit is 512 tokens. To stay safely under this limit,
# we recursively split text so every chunk is <= 350 characters:
#   1. Split by paragraph (double newline)
#   2. If paragraph > 350 chars → split by sentence (.!?)
#   3. If sentence > 350 chars → split by clause (,;:)
#   4. If clause still > 350 chars → hard-split at word boundary
# ---------------------------------------------------------------------------

MAX_CHUNK_CHARS = 350


def _split_by_clauses(sentence: str) -> list[str]:
    """Split a long sentence by clause boundaries (comma, semicolon, colon)."""
    # Split keeping the delimiter at the end of the preceding part
    parts = re.split(r'(?<=[,;:])\s+', sentence)
    return [p.strip() for p in parts if p.strip()]


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Last resort: split at word boundaries to fit within max_chars."""
    words = text.split()
    chunks = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > max_chars:
            chunks.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    if current:
        chunks.append(current)
    return chunks


def _ensure_max_length(pieces: list[str], max_chars: int) -> list[str]:
    """Ensure every piece is within max_chars, applying deeper splitting as needed."""
    result = []
    for piece in pieces:
        if len(piece) <= max_chars:
            result.append(piece)
        else:
            # Try splitting by clause
            clauses = _split_by_clauses(piece)
            for clause in clauses:
                if len(clause) <= max_chars:
                    result.append(clause)
                else:
                    # Hard split at word boundary
                    result.extend(_hard_split(clause, max_chars))
    return result


def split_into_chunks(text: str) -> list[str]:
    """
    Smart recursive chunker for Kokoro TTS.
    
    Guarantees every returned chunk is <= MAX_CHUNK_CHARS (350) characters
    to stay safely within Kokoro's 512-token limit, which prevents gibberish.
    
    Splitting hierarchy:
        Paragraph → Sentence → Clause → Word boundary
    """
    # Step 1: Split by paragraphs
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    all_chunks = []

    for para in paragraphs:
        # Normalize internal newlines to spaces (paragraph is a single block)
        para = re.sub(r'\n', ' ', para).strip()

        if len(para) <= MAX_CHUNK_CHARS:
            all_chunks.append(para)
            continue

        # Step 2: Split by sentence
        sentences = re.split(r'(?<=[.!?])\s+', para)
        sentences = [s.strip() for s in sentences if s.strip()]

        # Step 3+4: Ensure each sentence fits, splitting by clause or word if needed
        safe_pieces = _ensure_max_length(sentences, MAX_CHUNK_CHARS)
        all_chunks.extend(safe_pieces)

    # Verify all chunks are within limit
    for i, chunk in enumerate(all_chunks):
        if len(chunk) > MAX_CHUNK_CHARS:
            # This shouldn't happen, but safety net
            print(f"  WARNING: Chunk {i} has {len(chunk)} chars (limit {MAX_CHUNK_CHARS}), force-splitting")
            idx = all_chunks.index(chunk)
            all_chunks[idx:idx+1] = _hard_split(chunk, MAX_CHUNK_CHARS)

    return all_chunks


# ---------------------------------------------------------------------------
# TTS Synthesis
# ---------------------------------------------------------------------------

def synthesize(text: str, voice: str, output_dir: str, speed: float = 1.0):
    """
    Convert text to audio using Kokoro TTS.
    
    Produces individual WAV files per chunk in output_dir,
    then concatenates them into a single output file.
    """
    from kokoro import KPipeline

    # Ensure MPS fallback is enabled for Apple Silicon
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"\nInitializing Kokoro TTS pipeline (voice: {voice})...")
    pipeline = KPipeline(lang_code='a')  # American English

    # Split text into safe-sized chunks
    chunks = split_into_chunks(text)
    total_chunks = len(chunks)
    print(f"Text split into {total_chunks} chunks (max {MAX_CHUNK_CHARS} chars each)")
    print(f"Chunk sizes: min={min(len(c) for c in chunks)}, "
          f"max={max(len(c) for c in chunks)}, "
          f"avg={sum(len(c) for c in chunks) // len(chunks)}")

    sample_rate = 24000
    all_audio_files = []
    total_audio_seconds = 0.0
    start_time = time.time()

    for chunk_idx, chunk in enumerate(chunks):
        chunk_start = time.time()
        if chunk_idx % 10 == 0 or chunk_idx == total_chunks - 1:
            print(f"\n--- Chunk {chunk_idx + 1}/{total_chunks} ({len(chunk)} chars) ---")
            print(f"  Text: {chunk[:100]}...")

        # Generate audio — each chunk is already small enough,
        # so we let Kokoro process it as a single unit
        chunk_audio_parts = []

        generator = pipeline(
            chunk,
            voice=voice,
            speed=speed,
        )

        for seg_idx, (gs, ps, audio) in enumerate(generator):
            if audio is not None and len(audio) > 0:
                chunk_audio_parts.append(audio)

        if not chunk_audio_parts:
            print(f"  WARNING: No audio generated for chunk {chunk_idx + 1}")
            continue

        # Concatenate segments within this chunk
        chunk_audio = np.concatenate(chunk_audio_parts)

        # Add a very brief pause between chunks (150ms) — just enough for
        # natural breathing, but not so long it sounds like a weird gap
        pause = np.zeros(int(sample_rate * 0.15), dtype=chunk_audio.dtype)
        chunk_audio = np.concatenate([chunk_audio, pause])

        # Save chunk WAV
        chunk_filename = f"chunk_{chunk_idx:04d}.wav"
        chunk_filepath = output_path / chunk_filename
        sf.write(str(chunk_filepath), chunk_audio, sample_rate)
        all_audio_files.append(chunk_filepath)

        chunk_duration = len(chunk_audio) / sample_rate
        total_audio_seconds += chunk_duration
        chunk_elapsed = time.time() - chunk_start
        total_elapsed = time.time() - start_time

        # Progress and ETA
        progress = (chunk_idx + 1) / total_chunks * 100
        if chunk_idx > 0:
            avg_time_per_chunk = total_elapsed / (chunk_idx + 1)
            remaining_chunks = total_chunks - (chunk_idx + 1)
            eta_seconds = avg_time_per_chunk * remaining_chunks
            eta_min = eta_seconds / 60
            print(f"  Progress: {progress:.1f}% | Audio: {chunk_duration:.1f}s "
                  f"| Chunk time: {chunk_elapsed:.1f}s | ETA: {eta_min:.1f} min")
        else:
            print(f"  Progress: {progress:.1f}% | Audio: {chunk_duration:.1f}s "
                  f"| Chunk time: {chunk_elapsed:.1f}s")

    # Concatenate all chunks into final audiobook
    print(f"\n{'='*60}")
    print("Concatenating all chunks into final audiobook...")

    all_audio = []
    for audio_file in all_audio_files:
        data, _ = sf.read(str(audio_file))
        all_audio.append(data)

    if not all_audio:
        print("ERROR: No audio was generated!")
        sys.exit(1)

    final_audio = np.concatenate(all_audio)
    final_path = output_path / "audiobook.wav"
    sf.write(str(final_path), final_audio, sample_rate)

    # Clean up intermediate chunk files — keep only the final audiobook
    for audio_file in all_audio_files:
        audio_file.unlink(missing_ok=True)

    total_elapsed = time.time() - start_time
    total_audio_min = total_audio_seconds / 60
    total_elapsed_min = total_elapsed / 60

    print(f"\n{'='*60}")
    print(f"DONE!")
    print(f"  Output: {final_path}")
    print(f"  Audio duration: {total_audio_min:.1f} minutes ({total_audio_min/60:.1f} hours)")
    print(f"  Processing time: {total_elapsed_min:.1f} minutes")
    print(f"  Speed ratio: {total_audio_seconds / total_elapsed:.1f}x real-time")
    print(f"  Chunks processed: {len(all_audio_files)}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert text/PDF to audiobook using Kokoro TTS"
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        help='Input file path (.txt or .pdf)'
    )
    parser.add_argument(
        '--voice', '-v',
        default='af_heart',
        help='Kokoro voice name (default: af_heart)'
    )
    parser.add_argument(
        '--speed', '-s',
        type=float,
        default=1.0,
        help='Speech speed multiplier (default: 1.0)'
    )
    parser.add_argument(
        '--output', '-o',
        default='output',
        help='Output directory (default: output/)'
    )

    args = parser.parse_args()

    text = load_input(args.input)
    synthesize(text, args.voice, args.output, args.speed)


if __name__ == '__main__':
    main()
