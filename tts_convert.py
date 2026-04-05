#!/usr/bin/env python3
"""
Kokoro TTS: Convert text or PDF files to audiobook.
Outputs BOTH an audiobook.wav and a highly-precise word-level timestamp JSON.

Usage:
    python tts_convert.py --input script.txt --voice af_heart
    python tts_convert.py --input script.pdf --voice af_heart
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import soundfile as sf

# ---------------------------------------------------------------------------
# Data Structures for Synchronization JSON
# ---------------------------------------------------------------------------

@dataclass
class Word:
    i: int             # Global Word Index
    s: int             # Start time (ms)
    e: int             # End time (ms)
    c: list[int]       # Character index [start, end]
    text: str          # Raw text

@dataclass
class Paragraph:
    p_id: int
    w_start: int
    w_end: int
    c_start: int
    c_end: int

@dataclass
class Chapter:
    c_id: str
    title: str
    w_start: int
    w_end: int


# ---------------------------------------------------------------------------
# Text preprocessing & Indexing
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Clean raw text for TTS: fix hyphenation, normalize whitespace, remove artifacts."""
    # Fix hyphenation across line breaks (e.g. "embar-\nrassingly" -> "embarrassingly")
    text = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', text)
    
    # Remove page numbers (standalone numbers on their own line)
    text = re.sub(r'\n\s*\d+\s*\n', '\n', text)
    
    # Identify potential chapter markers (e.g., "Chapter 1", "CHAPTER ONE")
    # and ensure they are on their own line with spacing
    chapter_patterns = [
        r'(?i)^\s*(chapter\s+\d+|chapter\s+[ivxldcm]+|chapter\s+\w+)\s*$',
        r'^\s*([IVXLCDM]+\.?)\s*$', # Roman numerals alone on a line
    ]
    for pattern in chapter_patterns:
        text = re.sub(pattern, r'\n\n\1\n\n', text, flags=re.MULTILINE)

    # Collapse multiple blank lines into two (paragraph boundary)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Normalize whitespace within lines (but keep paragraph breaks)
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        # Keep empty lines as they signify paragraph breaks
        if not line.strip():
            cleaned_lines.append('')
            continue
        # Normalize internal whitespace
        line = re.sub(r'[ \t]+', ' ', line).strip()
        cleaned_lines.append(line)
    
    # Rejoin and do a final pass for extra whitespace
    text = '\n'.join(cleaned_lines)
    text = re.sub(r' +', ' ', text)
    return text.strip()

def extract_text_from_pdf(pdf_path: str) -> str:
    import pdfplumber
    import pytesseract
    from PIL import Image
    
    print(f"Extracting text from PDF: {pdf_path}")
    all_text = []
    
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            # Attempt standard text extraction
            page_text = page.extract_text()
            
            # If standard extraction fails or yields very little text, try OCR
            # Some PDFs have "transparent" text or just images. 
            # We look for pages with very few characters.
            if not page_text or len(page_text.strip()) < 10:
                print(f"  Page {i + 1}: No selectable text found. Attempting OCR...")
                try:
                    # Convert PDF page to a high-res image for OCR
                    # Improved resolution (300 DPI) for better OCR accuracy
                    image = page.to_image(resolution=300).original
                    ocr_text = pytesseract.image_to_string(image)
                    if ocr_text.strip():
                        page_text = ocr_text
                        print(f"  Page {i + 1}: OCR successful.")
                    else:
                        print(f"  Page {i + 1}: OCR yielded no text.")
                except Exception as e:
                    print(f"  Page {i + 1}: OCR failed: {e}")
            
            if page_text:
                all_text.append(page_text)
            
            if (i + 1) % 10 == 0 or (i + 1) == total_pages:
                print(f"  Processed page {i + 1}/{total_pages}")
                
    full_text = '\n\n'.join(all_text)
    print(f"  Total characters extracted: {len(full_text):,}")
    return full_text

def load_input_and_parse(input_path: str) -> tuple[str, list[Chapter], list[Paragraph], list[Word], int]:
    """Load text, clean it, and parse it into Words, Paragraphs, and Chapters."""
    path = Path(input_path)
    if not path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    total_pages = 0
    if path.suffix.lower() == '.pdf':
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            total_pages = len(pdf.pages)
        raw = extract_text_from_pdf(input_path)
    elif path.suffix.lower() == '.txt':
        raw = path.read_text(encoding='utf-8')
    else:
        print(f"Error: Unsupported file type: {path.suffix}")
        sys.exit(1)

    cleaned_text = clean_text(raw)
    
    # Parse into indexing structure
    paragraphs = []
    words = []
    chapters = []
    
    # We maintain pointers into the cleaned_text string
    char_idx = 0
    word_idx = 0
    
    # Split by actual paragraph groupings (double newline)
    raw_paras = cleaned_text.split('\n\n')
    
    # regex for chapter detection
    chapter_regex = re.compile(r'^(chapter\s+\d+|chapter\s+[ivxldcm]+|chapter\s+\w+|[ivxldcm]+\.?)$', re.IGNORECASE)

    for p_idx, para_text in enumerate(raw_paras):
        if not para_text.strip():
            # Adjust char_idx for empty space between paras
            # find the next non-whitespace char after char_idx
            match = re.search(r'\S', cleaned_text[char_idx:])
            if match:
                char_idx += match.start()
            continue
            
        # Find exact start of paragraph in cleaned_text
        para_start_char = cleaned_text.find(para_text, char_idx)
        if para_start_char == -1:
            para_start_char = char_idx
            
        p_word_start = word_idx
        
        # Check if this paragraph is actually a chapter heading
        is_chapter = chapter_regex.match(para_text.strip())
        if is_chapter:
            chapters.append(Chapter(
                c_id=f"ch_{len(chapters) + 1:02d}",
                title=para_text.strip(),
                w_start=word_idx,
                w_end=0 # Updated later
            ))

        # Split paragraph into words
        # Using a regex to find all non-whitespace clusters with their spans
        for m in re.finditer(r'\S+', para_text):
            w_text = m.group()
            w_start_char_in_para = m.start()
            w_end_char_in_para = m.end()
            
            w_start_global = para_start_char + w_start_char_in_para
            w_end_global = para_start_char + w_end_char_in_para
            
            w = Word(
                i=word_idx,
                s=0, e=0, # Will be filled during TTS
                c=[w_start_global, w_end_global],
                text=w_text
            )
            words.append(w)
            word_idx += 1
            
        p_word_end = word_idx - 1 if word_idx > p_word_start else p_word_start
        para_end_char = para_start_char + len(para_text)
        
        paragraphs.append(Paragraph(
            p_id=p_idx,
            w_start=p_word_start,
            w_end=p_word_end,
            c_start=para_start_char,
            c_end=para_end_char
        ))
        
        # Move char_idx forward past this paragraph
        char_idx = para_end_char
        
    # Finalize chapter ranges
    for i, ch in enumerate(chapters):
        if i < len(chapters) - 1:
            ch.w_end = chapters[i+1].w_start - 1
        else:
            ch.w_end = len(words) - 1 if words else 0

    # If no chapters found, create fallback
    if not chapters:
        chapters = [
            Chapter(
                c_id="ch_01",
                title="Chapter One",
                w_start=0,
                w_end=len(words) - 1 if words else 0
            )
        ]
    
    print(f"Loaded {len(words):,} words across {len(paragraphs)} paragraphs and {len(chapters)} chapters from {path.name}")
    return cleaned_text, chapters, paragraphs, words, total_pages


# ---------------------------------------------------------------------------
# Smart Chunking (anti-gibberish)
# ---------------------------------------------------------------------------
# Kokoro's token limit is 512 tokens. To stay safely under this limit,
# we recursively split text so every chunk is <= 350 characters:
#   1. Split by paragraph (double newline)
#   2. If paragraph > 350 chars -> split by sentence (.!?)
#   3. If sentence > 350 chars -> split by clause (,;:)
#   4. If clause still > 350 chars -> hard-split at word boundary
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


def split_into_chunks_with_words(text: str, words: list[Word]) -> list[tuple[str, list[Word]]]:
    """
    Split text into chunks <= MAX_CHUNK_CHARS, along with their corresponding Word objects.
    We iterate over the exact Word objects to build these chunks.
    """
    chunks = []
    current_chunk_words = []
    current_chunk_text = ""
    current_len = 0
    
    for w in words:
        word_len = len(w.text)
        # Attempt to add word
        if current_len + word_len + (1 if current_len > 0 else 0) <= MAX_CHUNK_CHARS:
            if current_chunk_text:
                current_chunk_text += " "
                current_len += 1
            current_chunk_text += w.text
            current_chunk_words.append(w)
            current_len += word_len
        elif current_len > 0:
            # Word doesn't fit, finalize current chunk
            chunks.append((current_chunk_text, current_chunk_words))
            current_chunk_text = w.text
            current_chunk_words = [w]
            current_len = word_len
        else:
            # A single word is somehow larger than MAX_CHUNK_CHARS!
            chunks.append((w.text, [w]))
            current_chunk_text = ""
            current_chunk_words = []
            current_len = 0
            
    if current_chunk_words:
        chunks.append((current_chunk_text, current_chunk_words))
        
    return chunks

def align_tokens_to_words(tokens: list, words: list[Word], chunk_start_ms: int):
    """
    Align Kokoro native tokens to our original Word objects using proportional character sequence mapping.
    Populates w.s and w.e with exact ms values.
    """
    if not tokens or not words:
        return

    # Extract clean text sequences
    # Kokoro token texts might contain whitespace, punctuation, etc.
    # We join everything into a continuous string with start/end time markers per character
    
    # 1. Expand tokens to character-level timings
    char_times = []
    for t in tokens:
        t_text = t.text.strip()
        if not t_text:
            continue
        
        # Calculate start and end in ms
        # Safety check for None timestamps which sometimes occur for punctuation
        if t.start_ts is None or t.end_ts is None:
            continue

        t_start_ms = int(t.start_ts * 1000)
        t_end_ms = int(t.end_ts * 1000)
        
        if len(t_text) == 0:
            continue
            
        dur_per_char = (t_end_ms - t_start_ms) / len(t_text)
        
        for i, char in enumerate(t_text):
            # Only track alphanumeric/visible characters to avoid punctuation skewing alignments heavily
            if char.isalnum():
                c_start = t_start_ms + i * dur_per_char
                c_end = c_start + dur_per_char
                char_times.append({
                    'char': char.lower(),
                    's': c_start,
                    'e': c_end
                })

    if not char_times:
        return

    # 2. Extract strictly alphanumeric characters from our target words
    target_chars = []
    word_char_map = [] # maps target_chars index back to Word object
    for w in words:
        for char in w.text:
            if char.isalnum():
                target_chars.append(char.lower())
                word_char_map.append(w)
                
    if not target_chars:
        return

    # 3. Align characters (Simple linear ratio if lengths mismatch slightly, Kokoro sometimes expands numbers "$1" -> "one dollar")
    # For a high-performance robust approach, we map proportionally over the alphanumeric sequence.
    # Since Kokoro tokens perfectly reflect the spoken audio, scaling index mathematically guarantees every word gets a span.
    
    num_source = len(char_times)
    num_target = len(target_chars)
    
    # Reset all word timings for this chunk
    for w in words:
        w.s = float('inf')
        w.e = 0
        
    for target_idx, w in enumerate(word_char_map):
        # Find corresponding proportional position in token char_times
        source_idx = int((target_idx / max(1, num_target - 1)) * max(0, num_source - 1))
        
        # Guard index
        source_idx = min(max(source_idx, 0), num_source - 1)
        
        c_time = char_times[source_idx]
        
        # Update word boundaries (convert chunk-relative ms to global ms)
        global_s = int(c_time['s'] + chunk_start_ms)
        global_e = int(c_time['e'] + chunk_start_ms)
        
        if global_s < w.s:
            w.s = global_s
        if global_e > w.e:
            w.e = global_e

    # Clean up unmapped words (e.g. punctuation-only words) or correct overlaps
    prev_e = chunk_start_ms
    for i, w in enumerate(words):
        if w.s == float('inf'):
            # Unmapped word, give it nominal duration from previous
            w.s = prev_e
            w.e = prev_e + 50
        
        # Ensure monotonicity
        if w.s < prev_e:
            w.s = prev_e
        if w.e < w.s:
            w.e = w.s + 10
            
        prev_e = w.e


# ---------------------------------------------------------------------------
# TTS Synthesis
# ---------------------------------------------------------------------------

def synthesize(text: str, chapters: list[Chapter], paragraphs: list[Paragraph], words: list[Word], total_pages: int, voice: str, output_dir: str, speed: float = 1.0, title: str = None, file_stem: str = None):
    """
    Convert text to audio using Kokoro TTS and generate precise synchronization JSON.
    """
    from kokoro import KModel, KPipeline
    import torch

    # Path to local model files for offline use
    script_dir = Path(__file__).parent
    model_dir = script_dir / "model"
    config_path = model_dir / "config.json"
    model_path = model_dir / "kokoro-v1_0.pth"
    voices_dir = model_dir / "voices"

    # Ensure MPS fallback is enabled for Apple Silicon
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if file_stem is None:
        file_stem = title if title else "audiobook"

    print(f"\nInitializing Kokoro TTS pipeline (voice: {voice})...")

    # Determine device
    device = 'cuda' if torch.cuda.is_available() else ('mps' if torch.backends.mps.is_available() else 'cpu')

    # Load model (offline-first)
    if config_path.exists() and model_path.exists():
        print(f"  Loading local model weights from {model_dir}...")
        try:
            kmodel = KModel(config=str(config_path), model=str(model_path)).to(device).eval()
            pipeline = KPipeline(lang_code='a', model=kmodel)
        except Exception as e:
            print(f"  Warning: Failed to load local model: {e}. Falling back to default initialization.")
            pipeline = KPipeline(lang_code='a', device=device)
    else:
        print("  Local model files not found. Using default initialization (may require internet).")
        pipeline = KPipeline(lang_code='a', device=device)

    # Resolve voice path (offline-first)
    if not voice.endswith('.pt'):
        local_voice_path = voices_dir / f"{voice}.pt"
        if local_voice_path.exists():
            print(f"  Using local voice file: {local_voice_path}")
            voice = str(local_voice_path)
        else:
            print(f"  Voice file '{voice}' not found in {voices_dir}. Attempting online download.")

    # Split text into safe-sized chunks while tracking words
    chunks = split_into_chunks_with_words(text, words)
    total_chunks = len(chunks)
    
    print(f"Text split into {total_chunks} chunks (max {MAX_CHUNK_CHARS} chars each)")

    sample_rate = 24000
    all_audio_files = []
    total_audio_seconds = 0.0
    start_time = time.time()
    
    global_time_ms = 0  # To track final start/end times in the concatenated file

    for chunk_idx, (chunk_text, chunk_words) in enumerate(chunks):
        chunk_start = time.time()
        if chunk_idx % 10 == 0 or chunk_idx == total_chunks - 1:
            print(f"\n--- Chunk {chunk_idx + 1}/{total_chunks} ({len(chunk_text)} chars) ---")
            print(f"  Text: {chunk_text[:100]}...")

        chunk_audio_parts = []
        chunk_tokens = []

        generator = pipeline(
            chunk_text,
            voice=voice,
            speed=speed,
        )

        for seg_idx, (gs, ps, audio_tensor) in enumerate(generator):
            if audio_tensor is not None and len(audio_tensor) > 0:
                chunk_audio_parts.append(audio_tensor)
                
            # Usually 'tokens' is on the result object if iterating KPipeline directly
            # For the generator return types `(gs, ps, audio)` we might need to access the Result object
            # Kokoro returns (gs, ps, audio) but the pipeline result yields KPipeline.Result objects
            # To get tokens we must iterate over the pipeline output properly
            # Let's inspect how to obtain tokens if we just got (gs, ps, audio) back
            # Wait, pipeline yields Result objects directly in `__call__` if we iterate it.
            pass
            
        # Due to how Kokoro pipeline yields, generator actually yields `Result` objects
        # We need to re-invoke properly to get tokens
        generator = pipeline(chunk_text, voice=voice, speed=speed)
        
        chunk_audio_parts = []
        chunk_tokens = []
        for result in generator:
            if result.audio is not None and len(result.audio) > 0:
                chunk_audio_parts.append(result.audio)
            
            if hasattr(result, 'tokens') and result.tokens:
                chunk_tokens.extend(result.tokens)

        if not chunk_audio_parts:
            print(f"  WARNING: No audio generated for chunk {chunk_idx + 1}")
            continue

        # Concatenate segments within this chunk
        chunk_audio = np.concatenate(chunk_audio_parts)

        # Map Kokoro tokens to our words and apply global time offset
        align_tokens_to_words(chunk_tokens, chunk_words, global_time_ms)

        # Add a very brief pause between chunks (150ms)
        pause_ms = 150
        pause_samples = int(sample_rate * (pause_ms / 1000.0))
        pause = np.zeros(pause_samples, dtype=chunk_audio.dtype)
        chunk_audio_with_pause = np.concatenate([chunk_audio, pause])

        # Save chunk WAV
        chunk_filename = f"chunk_{chunk_idx:04d}.wav"
        chunk_filepath = output_path / chunk_filename
        sf.write(str(chunk_filepath), chunk_audio_with_pause, sample_rate)
        all_audio_files.append(chunk_filepath)

        chunk_duration = len(chunk_audio_with_pause) / sample_rate
        total_audio_seconds += chunk_duration
        
        # Advance global time (including pause)
        global_time_ms += int(chunk_duration * 1000)
        
        chunk_elapsed = time.time() - chunk_start
        total_elapsed = time.time() - start_time

        progress = (chunk_idx + 1) / total_chunks * 100
        if chunk_idx > 0:
            avg_time_per_chunk = total_elapsed / (chunk_idx + 1)
            remaining_chunks = total_chunks - (chunk_idx + 1)
            eta_seconds = avg_time_per_chunk * remaining_chunks
            eta_min = eta_seconds / 60
            print(f"  Progress: {progress:.1f}% | Audio: {chunk_duration:.1f}s | ETA: {eta_min:.1f} min")
        else:
            print(f"  Progress: {progress:.1f}% | Audio: {chunk_duration:.1f}s")

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
    final_wav_path = output_path / f"{file_stem}.wav"
    sf.write(str(final_wav_path), final_audio, sample_rate)

    final_audio_path = output_path / f"{file_stem}.m4a"
    try:
        import subprocess
        print("Converting to m4a...")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(final_wav_path), "-c:a", "aac", "-b:a", "128k", str(final_audio_path)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        final_wav_path.unlink()  # Remove temporary wav
    except Exception as e:
        print(f"Warning: ffmpeg conversion to m4a failed ({e}). Keeping wav file.")
        final_audio_path = final_wav_path

    # Clean up intermediate chunk files
    for audio_file in all_audio_files:
        audio_file.unlink(missing_ok=True)

    # Prepare JSON Synchronisation Output
    print("Exporting alignment JSON...")
    
    # Optional metadata defaults
    b_id = "book_" + str(int(time.time()))
    title = title if title else "Generated Audiobook"
    emoji = "🎧"
    
    json_output = {
        "metadata": {
            "b_id": b_id,
            "title": title,
            "emoji": emoji,
            "duration_seconds": round(total_audio_seconds, 2),
            "total_pages": total_pages,
            "default_playback_speed": speed
        },
        "audio_file": final_audio_path.name,
        "text": text,
        "chapters": [asdict(c) for c in chapters],
        "paragraphs": [asdict(p) for p in paragraphs],
        "words": []
    }
    
    # We serialize words manually to match exactly the required tiny schema 
    for w in words:
        json_output["words"].append({
            "i": w.i,
            "s": w.s,
            "e": w.e,
            "c": w.c
        })
        
    final_json_path = final_audio_path.with_suffix('.json')
    with open(final_json_path, 'w', encoding='utf-8') as f:
        json.dump(json_output, f, ensure_ascii=False, separators=(',', ':'))

    total_elapsed = time.time() - start_time
    total_audio_min = total_audio_seconds / 60
    total_elapsed_min = total_elapsed / 60

    print(f"\n{'='*60}")
    print(f"DONE!")
    print(f"  Audio Output: {final_audio_path}")
    print(f"  JSON Output:  {final_json_path}")
    print(f"  Audio duration: {total_audio_min:.1f} minutes ({total_audio_min/60:.1f} hours)")
    print(f"  Processing time: {total_elapsed_min:.1f} minutes")
    print(f"  Speed ratio: {total_audio_seconds / total_elapsed:.1f}x real-time")
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
        action='append',
        help='Input file path (.txt or .pdf). Use multiple times for batch processing: -i file1.pdf -i file2.pdf'
    )
    parser.add_argument(
        '--batch', '-b',
        nargs='+',
        help='Batch mode: process multiple files at once. Usage: --batch file1.pdf file2.pdf file3.pdf'
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
        help='Output directory (default: output/). For batch mode, subdirectories are created per file.'
    )

    args = parser.parse_args()

    # Determine input files
    input_files = []
    if args.batch:
        input_files = args.batch
    elif args.input:
        input_files = args.input
    else:
        # Look for any PDF file in the PDFs folder
        pdf_files = list(Path('PDFs').glob('*.pdf'))
        if pdf_files:
            input_files = [str(p) for p in pdf_files]
            print(f"Auto-detected {len(input_files)} PDF file(s) in PDFs folder")
        else:
            parser.error("Either --input or --batch must be specified, and no PDF file found in PDFs folder")

    # Remove duplicates while preserving order
    input_files = list(dict.fromkeys(input_files))
    
    # Validate all files exist before starting
    for input_file in input_files:
        if not Path(input_file).exists():
            print(f"Error: File not found: {input_file}")
            sys.exit(1)

    # Process each file
    for idx, input_file in enumerate(input_files):
        print(f"\n{'='*70}")
        print(f"Processing file {idx + 1}/{len(input_files)}: {Path(input_file).name}")
        print(f"{'='*70}\n")
        
        # Create output directory with the input file's name (without extension)
        file_stem = Path(input_file).stem
        output_dir = str(Path(args.output) / file_stem)

        text, chapters, paragraphs, words, total_pages = load_input_and_parse(input_file)
        synthesize(text, chapters, paragraphs, words, total_pages, args.voice, output_dir, args.speed, title=file_stem, file_stem=file_stem)
    
    if len(input_files) > 1:
        print(f"\n{'='*70}")
        print(f"BATCH COMPLETE! Processed {len(input_files)} files.")
        print(f"Output directory: {args.output}")
        print(f"{'='*70}\n")


if __name__ == '__main__':
    main()
