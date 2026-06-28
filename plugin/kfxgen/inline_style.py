"""Inline emphasis run/span computation for KFX styling (#9).

Pure, Calibre-independent: turns a paragraph's ordered (text, flags) segments
into whitespace-normalized text plus character spans, ready to become KFX $142
spans. See docs/superpowers/specs/2026-06-28-inline-emphasis-css-typography-design.md.
"""

FLAG_ITALIC = "italic"
FLAG_BOLD = "bold"


def normalize_runs(segments):
    """Collapse whitespace across (text, flags) segments and return
    (normalized_text, spans). Mirrors the converter's existing
    `" ".join(text.split())` rule: each run of ASCII whitespace becomes a
    single space and leading/trailing space is stripped. `spans` are maximal
    (start, length, flags) ranges with non-empty flags, offset into the text.
    """
    chars = []
    flags_per_char = []
    prev_space = True  # strip leading whitespace
    for text, flags in segments:
        for ch in text:
            if ch.isspace():
                if not prev_space:
                    chars.append(" ")
                    # a collapsed space carries its own segment's flags so
                    # "italic italic" stays one span rather than fragmenting.
                    flags_per_char.append(flags)
                    prev_space = True
            else:
                chars.append(ch)
                flags_per_char.append(flags)
                prev_space = False
    # strip trailing space
    while chars and chars[-1] == " ":
        chars.pop()
        flags_per_char.pop()

    text_out = "".join(chars)
    spans = []
    i = 0
    n = len(flags_per_char)
    while i < n:
        f = flags_per_char[i]
        if not f:
            i += 1
            continue
        j = i + 1
        while j < n and flags_per_char[j] == f:
            j += 1
        spans.append((i, j - i, f))
        i = j
    return text_out, spans
