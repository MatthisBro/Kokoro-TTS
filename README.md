# Kokoro TTS: PDF to Audiobook

A pipeline for converting my self written novellas into audiobooks using the **Kokoro-82M** TTS model.

## Key Features

- **Voices:** Utilizes the lightweight, 82 million parameter Kokoro model for speech synthesis.
- **Smart OCR Fallback:** Automatically detects scanned PDFs (images without selectable text) and uses Tesseract OCR to extract content.
- **Precise Timestamps:** Generates accurate word-level synchronization JSON files alongside the `.m4a` audio output.
- **Batch Processing:** Queue multiple PDFs or text files to process sequentially.
- **Fully Offline Capable:** Can be run entirely without an internet connection (see Offline Setup).

---

## Installation

Ensure Tesseract OCR is installed on your system to handle scanned documents.

**macOS:**
```bash
brew install tesseract
```

**Clone and set up the Python environment:**
```bash
git clone https://github.com/MatthisBro/Kokoro-TTS.git
cd Kokoro-TTS

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

---

## Usage

### Quick Start

Place your PDF files in the `PDFs` folder and run:
```bash
./run.sh
```

The script will automatically find all PDFs in the folder, transcribe them sequentially, and delete them after processing.

### Customize Voice and Speed

Optionally pass voice and speed arguments:
```bash
./run.sh af_sky 1.2
```
* `af_sky` - voice selection (default: af_heart)
* `1.2` - playback speed (default: 1.0)

**Output:**
The result will be saved at `output/[pdf_name].m4a` along with a precise word-level synchronization JSON file (`output/[pdf_name].json`). Each PDF creates its own output folder named after the PDF.

### Output Structure
```text
output/
├── my_book/
│   ├── my_book.m4a
│   └── my_book.json
└── another_book/
    ├── another_book.m4a
    └── another_book.json
```

---

## Audio-Text Synchronization JSON Format

The generated `.json` file is designed to facilitate precise, word-level synchronization between the output audio file and its corresponding text. It is highly optimized for building features like **karaoke-style word highlighting**, **tap-to-play functionality**, and **chapter/paragraph navigation**.

### Top-Level Structure

The JSON file consists of six main root properties:

```json
{
  "metadata": { ... },
  "audio_file": "audiobook.m4a",
  "text": "...",
  "chapters": [ ... ],
  "paragraphs": [ ... ],
  "words": [ ... ]
}
```

| Property | Type | Description |
| :--- | :--- | :--- |
| `metadata` | Object | Contains descriptive information about the media (title, duration, etc.). |
| `audio_file` | String | The filename or URL of the associated audio file. |
| `text` | String | The complete, unbroken raw text of the audio track. All character indices in the file reference this string. |
| `chapters` | Array | Defines the structural chapters of the text, mapped to word indices. |
| `paragraphs` | Array | Defines the structural paragraphs of the text, mapped to both word and character indices. |
| `words` | Array | The core synchronization data. Contains granular timing and character index data for *every single word*. |

### Detailed Breakdown

#### 1. Metadata (`metadata`)
Provides general information about the book or track.

```json
"metadata": {
  "b_id": "book_1774687865",
  "title": "title",
  "emoji": "🎧",
  "duration_seconds": 159.78,
  "total_pages": 1,
  "default_playback_speed": 1.0
}
```
*   `b_id`: A unique identifier for the book/audio.
*   `title`: The title of the content.
*   `emoji`: A graphical representation or icon.
*   `duration_seconds`: Total length of the audio file in seconds (Float).
*   `total_pages`: Total number of pages in the text.
*   `default_playback_speed`: The recommended or default playback speed (e.g., `1.0` for 1x speed).

#### 2. Chapters (`chapters`)
Defines the larger sections of the text. Instead of storing the text again, it uses word pointers.

```json
"chapters":[
  {
    "c_id": "ch_01",
    "title": "Chapter One",
    "w_start": 0,
    "w_end": 474
  }
]
```
*   `c_id`: Unique identifier for the chapter.
*   `title`: Display title of the chapter.
*   `w_start`: The integer index of the **first word** of this chapter in the `words` array.
*   `w_end`: The integer index of the **last word** of this chapter in the `words` array.

#### 3. Paragraphs (`paragraphs`)
Defines paragraph breaks, allowing the UI to render the text properly and enabling paragraph-level skipping.

```json
"paragraphs":[
  {
    "p_id": 0,
    "w_start": 0,
    "w_end": 474,
    "c_start": 0,
    "c_end": 2633
  }
]
```
*   `p_id`: Paragraph sequence ID or number.
*   `w_start` / `w_end`: The indices of the first and last words in the `words` array that belong to this paragraph.
*   `c_start` / `c_end`: The start and end **character indices** in the root `text` string. This allows you to quickly extract a paragraph's exact string using `text.substring(c_start, c_end)`.

#### 4. Words (`words`)
This is the engine of the synchronization. Every spoken word in the audio has a corresponding object in this array.

```json
"words":[
  {"i": 0, "s": 250, "e": 387, "c":[0, 2]},
  {"i": 1, "s": 387, "e": 675, "c": [3, 10]}
]
```
To keep the file size minimal, keys are abbreviated:
*   `i` (Index): The sequential ID of the word.
*   `s` (Start Time): The exact time the word *starts* playing in the audio, expressed in **milliseconds**.
*   `e` (End Time): The exact time the word *finishes* playing in the audio, expressed in **milliseconds**.
*   `c` (Character Range): An array of two integers `[start_index, end_index]` pointing to the exact location of this word in the root `text` string. 

### How the Synchronization Works (Example)

Let's look at the very first word in the JSON:
`{"i": 0, "s": 250, "e": 387, "c":[0, 2]}`

1. **Text Extraction**: By looking at `c:[0, 2]`, the application can call `text.substring(0, 2)`. In the master text ("be written down at all..."), characters 0 to 2 represent the word **"be"**.
2. **Audio Sync**: The application knows that the word **"be"** is spoken starting at `0.250` seconds (`s: 250`) and ends at `0.387` seconds (`e: 387`).
3. **UI Highlighting**: An audio player can listen to the current playback time. When the playback timer hits 250ms, the UI highlights the word "be". When it hits 387ms, it removes the highlight and moves to word `i: 1` ("written").

---

## Supported Voices

- `af_heart` (Default Female - Soft/Natural)
- `af_sky` (Female)
- `af_bella` (Female)
- `am_adam` (Male)
- `am_michael` (Male)

---

## Offline Setup

To operate without an internet connection, you must download the necessary model files manually to bypass the Hugging Face runtime download.

1. **Create the model directory:**
   ```bash
   mkdir -p model/voices
   ```
2. **Download the base model:**
   Download the following files from the[hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M/tree/main) repository and place them in the `model/` folder:
   - `config.json`
   - `kokoro-v1_0.pth`
3. **Download voices:**
   Download desired voices from the [voices/](https://huggingface.co/hexgrad/Kokoro-82M/tree/main/voices) directory and place them in `model/voices/`.
4. **Verify Structure:**
   ```text
   .
   ├── model/
   │   ├── config.json
   │   ├── kokoro-v1_0.pth
   │   └── voices/
   │       ├── af_heart.pt
   │       └── af_sky.pt
   ├── PDFs/
   │   └── your_pdf_file.pdf
   ├── run.sh
   └── ...
   ```
The application will automatically detect local files and run in offline mode.

---

## Architecture

1. **Extraction & OCR**: Extracts text from PDFs using `pdfplumber`. Pages identified as images fall back to Tesseract OCR.
2. **Cleaning**: Normalizes whitespace, corrects line-break hyphenation, and removes page number artifacts via `spacy`.
3. **Chunking**: Applies recursive chunking (maximum 350 characters) to remain within the Kokoro 512-token limit, preventing audio cutoffs.
4. **Synthesis**: Generates 24kHz audio using the Kokoro-82M model.
5. **Alignment**: Maps model tokens to the original text to generate word-level timestamps.

---

## Dependencies

**Models:**
- **Kokoro-82M v0.19**: Core TTS engine.
- **Tesseract OCR**: Image text extraction.

**Core Libraries:**
- `kokoro (>=0.7.16)`: Pipeline implementation.
- `torch`: PyTorch inference framework.
- `transformers`: Model weight management.
- `spacy`: NLP text cleaning.
- `pdfplumber` & `Pillow`: Document parsing and image formatting.
- `pytesseract`: OCR integration.
- `pydub` & `soundfile`: Audio generation and manipulation.

## Acknowledgments

- The developers of [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) for the core TTS model.
- The maintainers of Tesseract, PyTorch, and Hugging Face.
- This project was developed approximately 85% through natural language programming and AI collaboration ("vibe coding").