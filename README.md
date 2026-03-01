# Kokoro TTS: PDF to Audiobook

A high-performance tool for converting PDF books into high-quality audiobooks using the Kokoro TTS model, optimized for Apple Silicon (M4).

## 🚀 Quick Start

1.  **Open Terminal** and navigate to this folder:
    ```bash
    cd "/Users/matthisbrodbeck/Developer/Python/Kokoro TTS"
    ```

2.  **Run the conversion**:
    *   **Option A: Using the default file** (Rename your PDF to `script.pdf` and place it in this folder):
        ```bash
        ./run.sh
        ```
    *   **Option B: Specify any PDF file**:
        ```bash
        ./run.sh path/to/your_book.pdf
        ```

3.  **Find your audio**:
    The result will be saved at `output/audiobook.wav`.

## 🛠 Advanced Usage

You can customize the voice and speed by passing extra arguments:
```bash
./run.sh [input_file] [voice_name] [speed]
```

**Example**:
```bash
./run.sh my_book.pdf af_sky 1.2
```

### Supported Voices
- `af_heart` (Default, Female)
- `af_sky` (Female)
- `af_bella` (Female)
- `am_adam` (Male)
- `am_michael` (Male)

## 📊 Performance on M4 Pro
- **Synthesis Speed**: ~15x real-time.
- **Large Books**: A 400-page book (~10 hours of audio) takes about **40-50 minutes** to process.
- **Memory/GPU**: Uses PyTorch MPS for hardware acceleration on Mac.

## 📝 How it Works
1.  **Extraction**: Extracts text from PDF using `pdfplumber`.
2.  **Cleaning**: Removes hyphenation at line breaks and page numbers.
3.  **Chunking**: Uses a smart recursive chunker (max 350 chars) to prevent "gibberish" issues common with long TTS inputs.
4.  **Synthesis**: Processes chunks in parallel (where possible) and merges them into a single WAV file.
