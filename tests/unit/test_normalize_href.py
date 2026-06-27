"""
Tier-1 unit tests for _normalize_href / _find_manifest_item path-traversal
defense (#44).

Threat: an EPUB whose manifest carries an href like '../../etc/passwd' must
not produce a normalized value that any downstream code path could resolve
outside the EPUB scope. The basename strip in _normalize_href makes the
*current* IMG-token pipeline safe, but raw hrefs flow through chunk text in
memory. This is fail-closed defense in depth.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))

from kfxgen.converter import _find_manifest_item, _is_unsafe_href, _normalize_href


@pytest.mark.tier1
@pytest.mark.unit
class TestNormalizeHrefSafe:
    """Legitimate hrefs round-trip to their basename."""

    def test_plain_filename(self):
        assert _normalize_href("chapter1.xhtml") == "chapter1.xhtml"

    def test_strips_anchor(self):
        assert _normalize_href("chapter1.xhtml#section2") == "chapter1.xhtml"

    def test_strips_directory(self):
        assert _normalize_href("OEBPS/text/chapter1.xhtml") == "chapter1.xhtml"

    def test_strips_directory_and_anchor(self):
        assert _normalize_href("OEBPS/text/chapter1.xhtml#part") == "chapter1.xhtml"


@pytest.mark.tier1
@pytest.mark.unit
class TestNormalizeHrefUnsafe:
    """Adversarial hrefs collapse to '' so downstream lookups fail closed."""

    @pytest.mark.parametrize(
        "href",
        [
            "../../etc/passwd",
            "../../../etc/passwd",
            "../secret.xhtml",
            "OEBPS/../../../etc/passwd",
            r"..\..\windows\system32\config",
        ],
    )
    def test_traversal_segment_returns_empty(self, href):
        assert _normalize_href(href) == ""

    @pytest.mark.parametrize(
        "href",
        [
            "/etc/shadow",
            "/etc/passwd",
            r"\\server\share\file",
            "C:\\Windows\\System32\\config",
            "c:/users/justin/.ssh/id_rsa",
        ],
    )
    def test_absolute_path_returns_empty(self, href):
        assert _normalize_href(href) == ""

    @pytest.mark.parametrize(
        "href",
        [
            "http://evil.example/payload",
            "https://evil.example/payload.xhtml",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "JAVASCRIPT:alert(1)",
        ],
    )
    def test_url_scheme_returns_empty(self, href):
        assert _normalize_href(href) == ""

    def test_empty_input(self):
        assert _normalize_href("") == ""

    def test_anchor_then_traversal(self):
        # The anchor strip happens first, then the safety check sees '../..'.
        assert _normalize_href("../../etc/passwd#whatever") == ""

    @pytest.mark.parametrize(
        "href",
        [
            "%2e%2e/etc/passwd",
            "%2e%2e%2fetc%2fpasswd",
            "%2E%2E%2Fetc%2Fpasswd",  # uppercase hex
            "OEBPS%2f..%2f..%2fetc%2fpasswd",  # mixed encoded + literal
            "%2ffoo",  # encoded leading slash → absolute
            "%66%69%6c%65%3a%2f%2f%2fetc",  # 'file:///etc' fully encoded
            "%252e%252e/etc/passwd",  # double-encoded
            "%25252e%25252e/etc/passwd",  # triple-encoded
        ],
    )
    def test_percent_encoded_traversal_returns_empty(self, href):
        # #60: defends against %2e%2e and friends. Calibre normalizes most
        # hrefs before they reach kfxgen, so this is defense-in-depth. The
        # iterative decode in _is_unsafe_href catches multi-pass encodings.
        assert _normalize_href(href) == ""


@pytest.mark.tier1
@pytest.mark.unit
class TestRejectionLogged:
    """Rejection is observable — silent failure is its own bug (#59)."""

    def test_unsafe_href_emits_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="kfxgen.converter.security"):
            assert _normalize_href("../../etc/passwd") == ""
        assert any(
            "rejected unsafe href" in r.message and "../../etc/passwd" in r.message
            for r in caplog.records
        )

    def test_safe_href_emits_nothing(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="kfxgen.converter.security"):
            _normalize_href("OEBPS/text/chapter1.xhtml")
        assert caplog.records == []


def _percent_encode_n_passes(s, n):
    """Return s percent-encoded n times.

    Each pass replaces every char with its '%HH' form. After N passes a
    single byte 'X' becomes a string ~3**N chars long, so this is a
    cooperative test helper, not an oracle of legitimate encoding depth.
    """
    out = s
    for _ in range(n):
        out = "".join(f"%{ord(c):02x}" for c in out)
    return out


@pytest.mark.tier1
@pytest.mark.unit
class TestPercentEncodingDepthBoundary:
    """N4: after N1's iterate-until-stable fix, multi-pass encodings of any
    realistic depth should be caught. Bound at 16 iterations and a 4x
    length-growth check guard against pathological input."""

    @pytest.mark.parametrize("depth", [5, 6])
    def test_deep_encoding_still_caught(self, depth):
        href = _percent_encode_n_passes("../etc/passwd", depth)
        assert _is_unsafe_href(href) is True

    def test_at_iteration_cap_caught_by_length_or_bound(self):
        # 16-pass encoding will trip the length-bound long before the
        # iteration cap; either way the input is rejected as malformed.
        # We don't construct the full string (3**16 expansion is huge) —
        # the test lives at depth 8, which already exceeds 4x growth on
        # the first decode pass relative to the original.
        href = _percent_encode_n_passes("..", 8)
        assert _is_unsafe_href(href) is True


@pytest.mark.tier1
@pytest.mark.unit
def test_is_unsafe_href_truthtable():
    assert _is_unsafe_href("../foo") is True
    assert _is_unsafe_href("/etc/passwd") is True
    assert _is_unsafe_href("C:\\foo") is True
    assert _is_unsafe_href("file:///etc/passwd") is True
    assert _is_unsafe_href("foo.xhtml") is False
    assert _is_unsafe_href("OEBPS/foo.xhtml") is False
    assert _is_unsafe_href("") is False  # empty handled by _normalize_href


# Pieces an attacker might assemble an href from: traversal, separators,
# scheme fragments, percent-encodings (single and multi-pass), and benign
# filename parts. Joining a random sample of these explores far more shapes
# than the enumerated cases above.
_HREF_PIECES = st.sampled_from(
    [
        "..",
        ".",
        "/",
        "\\",
        "%2e",
        "%2f",
        "%2E",
        "%5c",
        "%252e",
        "%25252e",
        "foo",
        "chapter1.xhtml",
        ":",
        "//",
        "://",
        "C:",
        "http:",
        "file:",
        "javascript:",
        "data:",
        "#anchor",
        " ",
        "x",
        "OEBPS",
        "ö",
        "🎉",
    ]
)
_href_strategy = st.lists(_HREF_PIECES, max_size=12).map("".join)

_HREF_FUZZ = settings(max_examples=300, deadline=1000)


@pytest.mark.tier1
@pytest.mark.unit
class TestNormalizeHrefProperty:
    """G3 (#125): href safety as a property over generated strings, not an
    enumerated case list. The enumerated tests above stay as named regressions;
    this catches bypasses they don't enumerate. The core invariant: a non-empty
    result is always a pure basename — no separators, no traversal, no scheme —
    so no downstream resolver could escape the book root with it."""

    @_HREF_FUZZ
    @given(href=_href_strategy)
    def test_never_raises(self, href):
        # Neither helper may raise on any input — a crash is its own DoS.
        _normalize_href(href)
        _is_unsafe_href(href)

    @_HREF_FUZZ
    @given(href=_href_strategy)
    def test_result_is_safe_basename_or_empty(self, href):
        out = _normalize_href(href)
        if out == "":
            return
        assert "/" not in out, f"separator leaked through: {out!r}"
        assert "\\" not in out, f"backslash leaked through: {out!r}"
        assert out != "..", "bare traversal segment returned"
        lowered = out.lower()
        assert "://" not in lowered
        assert not lowered.startswith(("javascript:", "data:", "file:"))

    @_HREF_FUZZ
    @given(
        prefix=st.sampled_from(["", "OEBPS/", "a/b/", "text/"]),
        depth=st.integers(min_value=1, max_value=8),
    )
    def test_any_traversal_depth_rejected(self, prefix, depth):
        # Any href carrying a real `..` segment collapses to '' regardless of
        # how deep the traversal goes or what legitimate prefix precedes it.
        href = prefix + "/".join([".."] * depth) + "/etc/passwd"
        assert _normalize_href(href) == ""

    @pytest.mark.parametrize(
        "href,expected",
        [
            (r"....\\", ""),  # the G3 falsifying example: trailing backslash residue
            (r"foo\bar.xhtml", "bar.xhtml"),  # backslash dir component stripped
            (r"OEBPS\text\chapter1.xhtml", "chapter1.xhtml"),
        ],
    )
    def test_backslash_separator_stripped_from_basename(self, href, expected):
        # #125 regression: the basename strip splits on both / and \, so a
        # Windows-style backslash never survives into the returned basename.
        assert _normalize_href(href) == expected

    @pytest.mark.parametrize(
        "href",
        [
            "..../file:",  # G3 falsifying example: basename 'file:' surfaced
            "foo/javascript:alert(1)",  # scheme as final segment, surfaces on strip
            "dir/data:payload",
            "dir/C:relative",  # drive-letter fragment surfaced as basename
        ],
    )
    def test_scheme_surfaced_by_basename_strip_rejected(self, href):
        # #125 regression: stripping directories can surface a scheme/drive
        # fragment that passed the full-href check; the basename is re-checked
        # so the function fails closed as its docstring promises.
        assert _normalize_href(href) == ""


@pytest.mark.tier1
@pytest.mark.unit
class TestFindManifestItemRejects:
    """_find_manifest_item must return None for hrefs that would normalize to ''.

    This guards against a future code path where a manifest's hrefs dict
    happens to be keyed by the raw attacker string (Calibre normalizes its
    OEB hrefs, so this is defense in depth).
    """

    def _book_with_hrefs(self, hrefs_dict):
        book = MagicMock()
        manifest = MagicMock()
        manifest.hrefs = hrefs_dict
        manifest.__iter__ = lambda self: iter([])
        book.manifest = manifest
        return book

    @pytest.mark.parametrize(
        "attacker_href",
        [
            "../../etc/passwd",
            "/etc/shadow",
            "file:///etc/passwd",
            "C:\\secrets",
        ],
    )
    def test_returns_none_for_unsafe_href(self, attacker_href):
        # Even if a manifest happens to contain the attacker's exact string as
        # a key, the lookup must fail closed.
        bait = MagicMock()
        book = self._book_with_hrefs({attacker_href: bait})
        assert _find_manifest_item(book, attacker_href) is None

    def test_legit_lookup_still_works(self):
        legit = MagicMock()
        book = self._book_with_hrefs({"chapter1.xhtml": legit})
        assert _find_manifest_item(book, "chapter1.xhtml") is legit
