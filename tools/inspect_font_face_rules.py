"""Dump Calibre Stylizer.font_face_rules + manifest font magic for an EPUB.

Phase-0 spike for #15: confirms the real shape of `@font-face` rules Calibre
exposes, so the generator's font-carriage code reads them correctly.

Run with Calibre's bundled Python:
  /Applications/calibre.app/Contents/MacOS/calibre-debug \
      tools/inspect_font_face_rules.py <book.epub>

Prints only structural shape (rule type, field values, font magic bytes) — it
does not print or persist book identity beyond what the operator's EPUB path
already reveals locally. Do not commit its output.
"""
import os
import sys


def build_oeb(path, log):
    """Build a Stylizer-ready OEBBook from an EPUB, mirroring the output
    pipeline the kfxgen plugin receives."""
    from calibre.ebooks.conversion.plumber import Plumber, create_oebbook

    pl = Plumber(path, path + ".ignore.epub", log)
    pl.setup_options()
    ext = path.rsplit(".", 1)[-1].lower()
    workdir = os.path.join(os.path.dirname(os.path.abspath(path)), "_ffr_work")
    os.makedirs(workdir, exist_ok=True)
    with pl.input_plugin:
        opf = pl.input_plugin.convert(open(path, "rb"), pl.opts, ext, log, workdir)
    oeb = create_oebbook(log, opf, pl.opts)
    oeb.opts = pl.opts  # real output pipeline attaches this; Stylizer needs it
    return oeb


def main(path):
    from calibre.utils.logging import Log

    log = Log()
    oeb = build_oeb(path, log)
    from calibre.ebooks.oeb.stylizer import Stylizer

    printed = False
    for item in oeb.spine:
        try:
            st = Stylizer(
                item.data, item.href, oeb, oeb.opts,
                getattr(oeb.opts, "output_profile", None),
            )
        except Exception as e:
            print("stylizer-fail", e)
            continue
        rules = getattr(st, "font_face_rules", []) or []
        if rules:
            print("FONT_FACE_RULES count:", len(rules))
            for r in rules:
                print("  TYPE:", type(r).__module__ + "." + type(r).__name__)
                style = getattr(r, "style", None)
                if style is not None:
                    for prop in ("font-family", "src", "font-weight", "font-style"):
                        print(f"    {prop}: {style.getPropertyValue(prop)!r}")
                print(
                    "    has .get:", hasattr(r, "get"),
                    "| has __getitem__:", hasattr(r, "__getitem__"),
                )
            printed = True
            break
    if not printed:
        print("NO font_face_rules found on any spine item")

    print("MANIFEST_FONTS:")
    for it in oeb.manifest:
        href = getattr(it, "href", "") or ""
        if href.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
            data = getattr(it, "data", b"") or b""
            magic = bytes(data[:4]).hex() if isinstance(data, (bytes, bytearray)) else "?"
            n = len(data) if hasattr(data, "__len__") else "?"
            print(f"  .{href.rsplit('.', 1)[-1]} magic {magic} len {n}")


if __name__ == "__main__":
    main(sys.argv[1])
