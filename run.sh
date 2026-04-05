#!/bin/bash
# Kokoro TTS: PDF to Audiobook
# ============================================================
# Usage:
#   ./run.sh [voice] [speed]
#
# Just place PDF files in the PDFs folder and run.
# All PDFs will be transcribed automatically.
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
OUTPUT_DIR="$SCRIPT_DIR/output"
PDFS_DIR="$SCRIPT_DIR/PDFs"

# Create PDFs folder if it doesn't exist
if [ ! -d "$PDFS_DIR" ]; then
    mkdir -p "$PDFS_DIR"
fi

# Default values
VOICE="${1:-af_heart}"
SPEED="${2:-1.0}"

# Check virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "Error: Virtual environment not found at $VENV_DIR"
    echo "Run: python3 -m venv venv && pip install -r requirements.txt"
    exit 1
fi

# Find all PDFs in PDFs folder
PDF_FILES=("$PDFS_DIR"/*.pdf)

# Check if any PDFs exist
if [ ! -f "${PDF_FILES[0]}" ]; then
    echo "Error: No PDF files found in $PDFS_DIR"
    echo "Please place your PDF files in the PDFs folder"
    exit 1
fi

# Count PDFs
PDF_COUNT=0
for f in "${PDF_FILES[@]}"; do
    if [ -f "$f" ]; then
        ((PDF_COUNT++))
    fi
done

if [ "$PDF_COUNT" -eq 0 ]; then
    echo "Error: No PDF files found in $PDFS_DIR"
    echo "Please place your PDF files in the PDFs folder"
    exit 1
fi

echo "============================================================"
echo "  Kokoro TTS: PDF to Audiobook"
echo "============================================================"
echo "  PDFs Found: $PDF_COUNT file(s)"
echo "  Output:     $OUTPUT_DIR/"
echo "  Voice:      $VOICE"
echo "  Speed:      $SPEED"
echo "============================================================"
echo ""

START_TIME=$(date +%s)

# Enable MPS GPU acceleration for Apple Silicon
export PYTORCH_ENABLE_MPS_FALLBACK=1

# Build list of all PDFs
ALL_PDFS=()
for f in "${PDF_FILES[@]}"; do
    if [ -f "$f" ]; then
        ALL_PDFS+=("$f")
    fi
done

# Run batch processing
"$VENV_DIR/bin/python" "$SCRIPT_DIR/tts_convert.py" \
    --batch "${ALL_PDFS[@]}" \
    --voice "$VOICE" \
    --speed "$SPEED" \
    --output "$OUTPUT_DIR"

# Delete processed PDFs
echo ""
echo "Deleting processed PDF files..."
for f in "${ALL_PDFS[@]}"; do
    if [ -f "$f" ]; then
        rm "$f"
        echo "  Deleted: $(basename "$f")"
    fi
done

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
MINUTES=$((ELAPSED / 60))
SECONDS=$((ELAPSED % 60))

echo ""
echo "============================================================"
echo "  SUCCESS!"
echo "  Processed: $PDF_COUNT PDF file(s)"
echo "  Output:    $OUTPUT_DIR/"
echo "  Total Time: ${MINUTES}m ${SECONDS}s"
echo "============================================================"