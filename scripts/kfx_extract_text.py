#!/usr/bin/env python3
"""
Extract text content from KFX files.

Usage:
    python3 kfx_extract_text.py <input.kfx> [output.txt]

If output.txt is not specified, prints to stdout.
"""

import struct
import sys
from pathlib import Path


def extract_text_from_fragment_145(frag_data):
    """Extract text from a Fragment 145 chunk."""
    # Fragment 145 structure:
    # - ENTY header (21 bytes)
    # - Ion version marker (4 bytes)
    # - Ion struct with Field $146 (list of strings)

    # Skip headers (first 25 bytes)
    payload = frag_data[25:] if len(frag_data) > 25 else frag_data

    # Extract printable text
    text_chars = []
    for byte in payload:
        if 32 <= byte <= 126 or byte in [9, 10, 13]:  # Printable + whitespace
            text_chars.append(chr(byte))
        elif byte >= 128:  # UTF-8 multi-byte
            try:
                text_chars.append(chr(byte))
            except (ValueError, OverflowError):
                pass

    text = "".join(text_chars)

    # Clean up formatting
    # Remove Ion structure markers and clean whitespace
    text = text.replace("Þ", "\n")  # Ion struct markers often appear before paragraphs
    text = text.replace("Æ", "")
    text = text.replace("¾", "")
    text = text.replace("â", '"')
    text = text.replace("Ã©", "é")
    text = text.replace("Ã", "À")

    # Clean up excessive whitespace
    while "  " in text:
        text = text.replace("  ", " ")
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    return text.strip()


def extract_kfx_text(kfx_path):
    """Extract all text from a KFX file."""
    with open(kfx_path, "rb") as f:
        data = f.read()

    # Parse header
    magic, version, header_len, ci_offset, ci_len = struct.unpack("<4sHLLL", data[:18])

    # Find all Fragment 145s (content chunks)
    offset = 18
    text_chunks = []

    while offset < header_len - 24:
        chunk = data[offset : offset + 24]

        try:
            frag_type = struct.unpack("<L", chunk[4:8])[0]
            frag_offset = struct.unpack("<Q", chunk[8:16])[0]
            frag_len = struct.unpack("<Q", chunk[16:24])[0]

            if frag_type == 145 and frag_len > 0:
                frag_data = data[
                    header_len + frag_offset : header_len + frag_offset + frag_len
                ]
                text = extract_text_from_fragment_145(frag_data)
                if text:
                    text_chunks.append(text)
        except struct.error:
            pass

        offset += 24

    return "\n\n".join(text_chunks)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 kfx_extract_text.py <input.kfx> [output.txt]")
        print()
        print("Examples:")
        print("  python3 kfx_extract_text.py book.kfx")
        print("  python3 kfx_extract_text.py book.kfx extracted.txt")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    if not Path(input_file).exists():
        print(f"Error: File not found: {input_file}")
        sys.exit(1)

    print(f"Extracting text from: {input_file}")
    text = extract_kfx_text(input_file)

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"✓ Extracted {len(text):,} characters")
        print(f"✓ Saved to: {output_file}")
    else:
        print()
        print("=" * 80)
        print(text)
        print("=" * 80)
        print(f"\n({len(text):,} characters)")
