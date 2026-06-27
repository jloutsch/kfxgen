"""
UTF-8 multibyte chunking boundary tests (#52).

Council finding (Feynman): the chunker at
`plugin/kfxgen/native_generator.py::_split_long_text` slices on
`len(text)` and `text[pos:pos+CHUNK_SIZE]`. The original concern was
that this might split surrogate pairs or otherwise corrupt multi-byte
UTF-8 characters at chunk boundaries.

Reality (Python 3): `str` is a sequence of Unicode *code points*, not
UTF-16 code units. Slicing always lands on a code-point boundary.
Surrogate pairs are an artifact of the UTF-16 encoding step that
Python `str` doesn't use internally. So the literal "split a surrogate
pair" failure mode is structurally impossible — unless we ever do byte-
level slicing somewhere downstream.

What this test actually probes:
1. The chunker accepts non-Latin inputs (emoji, CJK, mixed) without
   crashing.
2. Every chunk's text re-encodes as valid UTF-8 (no truncated multi-
   byte sequence).
3. Generated KFX builds successfully on these inputs.
4. The output's `$265` position-id map's character offsets stay within
   the chunked text's actual character count — i.e. byte-vs-char
   confusion didn't sneak in at the offset-emit layer.

Tier-1 + unit per the #42 oracle hierarchy.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.kfxlib_minimal.ion import IS  # noqa: E402
from kfxgen.native_generator import NativeKFXGenerator  # noqa: E402

from tests._kfx_introspect import by_type, load_fragments, val  # noqa: E402


def _generate(chapters):
    """Generate a KFX with the given chapter dicts; return the path."""
    gen = NativeKFXGenerator()
    with tempfile.NamedTemporaryFile(suffix=".kfx", delete=False) as f:
        path = f.name
    try:
        gen.generate_full_book(
            title="UTF-8 Chunking Test",
            author="A",
            chapters=chapters,
            output_path=path,
        )
    except Exception:
        os.unlink(path)
        raise
    return path


def _all_chunk_strings(frags) -> list[str]:
    """Return every string emitted in $145 content fragments. These are
    the chunked outputs the chunker produced; they're the assertion
    target for "did chunking corrupt UTF-8 at the boundary."""
    out: list[str] = []
    for f in by_type(frags, "$145"):
        v = val(f)
        # $145 is a struct with $146: list[str]
        chunks = v.get(IS("$146")) if hasattr(v, "get") else None
        if chunks:
            for s in chunks:
                if isinstance(s, str):
                    out.append(s)
    return out


def _max_position_id(frags) -> int:
    """Return the largest $185 (position-id) value across the $265 map.
    Used to assert $265 offsets stay within character-count bounds."""
    max_pos = 0
    for f in by_type(frags, "$265"):
        v = val(f)
        entries = v if isinstance(v, list) else v.get(IS("$181")) or []
        for e in entries:
            if hasattr(e, "get"):
                p = e.get(IS("$185"))
                if p is not None:
                    max_pos = max(max_pos, int(p))
    return max_pos


@pytest.mark.tier1
@pytest.mark.unit
class TestMultibyteChunking:
    """Probe the chunker against non-Latin input. Each test generates
    a book whose chapter text exceeds CHUNK_SIZE (2000 chars) using
    multibyte UTF-8 characters, then asserts the output is valid."""

    def test_emoji_only_chunks_are_valid_utf8(self):
        """`"😀" * 2500` — 2500 code points, ~10 KB UTF-8. Chunker
        should produce ~2 chunks; each must re-encode."""
        text = "😀" * 2500
        chapters = [
            {"title": "Emoji", "text": f"Emoji\n\n{text}"},
            {"title": "Plain", "text": "Plain\n\nEnd."},
        ]
        path = _generate(chapters)
        try:
            frags = load_fragments(path)
            strings = _all_chunk_strings(frags)
            assert strings, "no $145 chunk strings emitted"
            for s in strings:
                # Re-encoding through utf-8 must succeed without surrogate
                # or multi-byte truncation; if the chunker had byte-sliced
                # an emoji this would raise.
                s.encode("utf-8")
                # And the round-trip must be lossless.
                assert s.encode("utf-8").decode("utf-8") == s
        finally:
            os.unlink(path)

    def test_cjk_only_chunks_are_valid_utf8(self):
        """`"漢" * 2500` — 2500 code points, 7.5 KB UTF-8."""
        text = "漢" * 2500
        chapters = [{"title": "CJK", "text": f"CJK\n\n{text}"}]
        path = _generate(chapters)
        try:
            frags = load_fragments(path)
            strings = _all_chunk_strings(frags)
            assert strings, "no $145 chunk strings emitted"
            for s in strings:
                s.encode("utf-8").decode("utf-8")
        finally:
            os.unlink(path)

    def test_mixed_script_chunks_preserve_character_counts(self):
        """Interleaves ASCII, Latin-1, CJK, and emoji so any character-
        boundary off-by-one would corrupt one of the four scripts.

        Asserts character-count preservation rather than exact substring
        match: the chunker stores paragraphs as separate `$145` strings
        without their `\\n\\n` separators, so naïve concatenation
        wouldn't reconstitute the input. What we can assert is that the
        multi-byte characters survive intact — counts match what we put
        in, and every chunk re-decodes cleanly."""
        # Each cycle is 5 chars: ASCII letter + space + CJK + emoji + space
        cycle = "x 漢🎉 "
        text = cycle * 600  # 3000 chars total
        chapters = [{"title": "Mixed", "text": f"Mixed\n\n{text}"}]
        path = _generate(chapters)
        try:
            frags = load_fragments(path)
            strings = _all_chunk_strings(frags)
            assert strings
            joined = "".join(strings)
            # Each chunk must round-trip through UTF-8 cleanly.
            for s in strings:
                assert s.encode("utf-8").decode("utf-8") == s
            # Multi-byte character counts must be preserved exactly —
            # 600 of each non-ASCII character entered the chunker, so
            # 600 of each must come out. A boundary slice that dropped
            # half a code point would short-count.
            assert joined.count("漢") == 600
            assert joined.count("🎉") == 600
        finally:
            os.unlink(path)

    def test_position_ids_stay_within_character_count(self):
        """`$265` content positions are emitted by the chunker against
        character-count semantics. They should never exceed the total
        character count of the text they map. If byte-vs-char confusion
        snuck in, multibyte text would push max-position above
        len(text)."""
        text = "🎉" * 2500
        chapters = [{"title": "Emoji", "text": f"Emoji\n\n{text}"}]
        path = _generate(chapters)
        try:
            frags = load_fragments(path)
            max_pos = _max_position_id(frags)
            # Tight ceiling at 6000. The honest expected maximum on
            # this input (2500 chars + heading + paragraph spacing) is
            # well under 3000 with `CONTENT_POS_BASE=1000` + step. A
            # byte-confusion bug would land near 10000 (4 bytes/emoji
            # × 2500). 6000 gives the test ~2× margin against the
            # real-bug scenario while staying well below the true
            # honest output. A weaker bound at SECTION_POS_BASE (10000)
            # would barely catch the bug it's meant to surface.
            assert max_pos < 6000, (
                f"max position id {max_pos} >= 6000; byte-vs-char "
                f"confusion may have inflated offsets on multibyte input"
            )
        finally:
            os.unlink(path)

    def test_emoji_at_chunk_boundary_round_trips(self):
        """Construct text whose CHUNK_SIZE-th character is an emoji.
        Even though Python 3 slicing is code-point safe, this pins
        the invariant explicitly — if anyone ever rewrites the chunker
        in bytes, this test fires."""
        # CHUNK_SIZE - 1 ASCII chars + emoji + filler = boundary lands
        # on the emoji.
        chunk_size = NativeKFXGenerator.CHUNK_SIZE
        ascii_run = "a" * (chunk_size - 1)
        text = ascii_run + "😀" + "b" * 100  # boundary cuts *after* emoji
        chapters = [{"title": "Boundary", "text": f"Boundary\n\n{text}"}]
        path = _generate(chapters)
        try:
            frags = load_fragments(path)
            strings = _all_chunk_strings(frags)
            joined = "".join(strings)
            assert "😀" in joined, "boundary slice dropped the emoji"
        finally:
            os.unlink(path)
