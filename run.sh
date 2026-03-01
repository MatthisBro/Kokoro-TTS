# Kokoro TTS: PDF to Audiobook
# ============================================================
# Usage:
#   ./run.sh [input_file.pdf] [voice] [speed]
#
# Examples:
#   ./run.sh                  (Uses script.pdf, af_heart, 1.0)
#   ./run.sh book.pdf         (Uses book.pdf)
#   ./run.sh book.pdf af_sky 1.2
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
OUTPUT_DIR="$SCRIPT_DIR/output"

# Default values
INPUT_FILE="${1:-$SCRIPT_DIR/script.pdf}"
VOICE="${2:-af_heart}"
SPEED="${3:-1.0}"

# Resolve absolute path for input if it's relative
if [[ ! "$INPUT_FILE" == /* ]]; then
    INPUT_FILE="$PWD/$INPUT_FILE"
fi

# Check virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "Error: Virtual environment not found at $VENV_DIR"
    echo "Run: python3 -m venv venv && pip install -r requirements.txt"
    exit 1
fi

# Check input file
if [ ! -f "$INPUT_FILE" ]; then
    echo "Error: File not found: $INPUT_FILE"
    echo "Usage: ./run.sh [path/to/script.pdf]"
    exit 1
fi

echo "============================================================"
echo "  Kokoro TTS: PDF to Audiobook"
echo "============================================================"
echo "  Input:  $(basename "$INPUT_FILE")"
echo "  Output: $OUTPUT_DIR/"
echo "  Voice:  $VOICE"
echo "  Speed:  $SPEED"
echo "============================================================"
echo ""

START_TIME=$(date +%s)

# Enable MPS GPU acceleration for Apple Silicon
export PYTORCH_ENABLE_MPS_FALLBACK=1

# Run the conversion
"$VENV_DIR/bin/python" "$SCRIPT_DIR/tts_convert.py" \
    --input "$INPUT_FILE" \
    --voice "$VOICE" \
    --speed "$SPEED" \
    --output "$OUTPUT_DIR"

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
MINUTES=$((ELAPSED / 60))
SECONDS=$((ELAPSED % 60))

echo ""
echo "============================================================"
echo "  SUCCESS!"
echo "  Final Audio: $OUTPUT_DIR/audiobook.wav"
echo "  Total Time:  ${MINUTES}m ${SECONDS}s"
echo "============================================================"
