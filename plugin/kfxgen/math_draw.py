"""MathML equations drawn as glyph outlines, as Kindle Previewer draws them
(#235), by ziamath, bundled in `vendor/`.

A drawing is measured in thousandths of an em from its top left corner, y
downwards; `depth` is how far it reaches below the text's baseline. It
places paths: (glyph, path, matrix), the path a KFX path list (0 move,
1 line, 2 quadratic curve, 3 cubic curve, 4 close, each followed by its
points) and the matrix (a, b, c, d, e, f) placing it, as SVG's. A glyph's
path is the same in every equation, so a book stores it once, as Kindle
Previewer does; a fraction or root bar has no glyph and is placed as it
is drawn."""

import copy
import logging
import re
import threading
import xml.etree.ElementTree as ET
from collections import namedtuple

from . import _vendor

MathDrawing = namedtuple("MathDrawing", "width height depth paths")
#: Characters the maths font has no glyph for, drawn as text inside the
#: drawing by the Kindle's own font, as Kindle Previewer 4.0.3 draws Arabic
#: letters and digits (#708): the text and the matrix placing its origin
#: on the baseline.
MathText = namedtuple("MathText", "text matrix")
#: Previewer's text in a drawing: `$16` 16, scaled 51.978 in an equation
#: drawn 1000 units to the em, 0.83 of the maths font's size (probe P708).
TEXT_SIZE = 16
_TEXT_SCALE = 51.978 / 1000

#: ziamath's size: one unit is a thousandth of an em, as in Previewer's.
_EM = 1000
#: MathML's token elements: the text in each is one run of letters (#708).
_TOKENS = frozenset({"mi", "mn", "mo", "mtext", "ms"})
#: A glyph's path is kept in hundredths of its own units, in whole numbers.
_GLYPH = 100
_IDENTITY = (1, 0, 0, 1, 0, 0)
_NOT_DRAWN = {"annotation", "annotation-xml"}
_COMMANDS = {"M": (0, 2), "L": (1, 2), "Q": (2, 4), "C": (3, 6), "Z": (4, 0)}
_TOKEN = re.compile(r"[A-Za-z]|-?(?:\d+\.?\d*|\.\d+)(?:[eE]-?\d+)?")
#: The nodes ziamath may lay out for one equation. It lays each child of a
#: row out twice, so its work doubles with each row nested in a row: a
#: 60-deep continued fraction would run for hours. Of 24 equations in
#: real books the largest took 199, an 8-deep \cfrac 10,207, each about
#: 30 µs.
_STEPS = 20_000
_budget = threading.local()
#: The tallest bracket ziamath may build, in the font's ems (ziamath takes
#: a MathML em as 1.33 of them). It builds one from repeated parts in a
#: loop that grows with the square of their count: 1,333 took 0.14 s,
#: 4,000 1.4 s, 133,000 ran for over 3 minutes.
_TALLEST = 1_000
#: The most text an equation may hold. ziamath's time grows faster than
#: its length: 4,000 digits took 0.1 s, 50,000 10 s. The longest of 24
#: equations in real books held 101 characters.
_LONGEST = 4_000


class _OverBudget(Exception):
    pass


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ziamath():
    """The bundled ziamath, or None when it can't be loaded, each node it
    lays out counted against `_budget` and each bracket it builds no taller
    than `_TALLEST`."""
    try:
        if not _vendor.on_path():
            return None
        import ziamath
        from ziamath.mathtable import MathAssembly
        from ziamath.nodes.mnode import Mnode
    except Exception:
        return None
    if not getattr(Mnode.fromelement, "_budgeted", False):
        layout = Mnode.fromelement.__func__

        def fromelement(cls, element, parent, **kwargs):
            if _budget.left <= 0:
                raise _OverBudget
            _budget.left -= 1
            if _local(element.tag) not in _TOKENS:
                return layout(cls, element, parent, **kwargs)
            # The glyphs laid out here belong to this token (#708).
            tokens = _budget.__dict__.setdefault("tokens", [])
            _budget.serial = getattr(_budget, "serial", 0) + 1
            tokens.append(_budget.serial)
            try:
                return layout(cls, element, parent, **kwargs)
            finally:
                tokens.pop()

        fromelement._budgeted = True
        Mnode.fromelement = classmethod(fromelement)
        build = MathAssembly.assemble

        def assemble(self, reqsize, minoverlap):
            if reqsize > _TALLEST * self.font.info.layout.unitsperem:
                raise _OverBudget
            return build(self, reqsize, minoverlap)

        MathAssembly.assemble = assemble
    from ziamath.drawable import Glyph

    if not getattr(Glyph.draw, "_tagged", False):
        place, made = Glyph.draw, Glyph.__init__

        def init_glyph(self, *args, **kwargs):
            made(self, *args, **kwargs)
            tokens = getattr(_budget, "tokens", None)
            self.kfxgen_token = tokens[-1] if tokens else None

        def draw_glyph(self, x, y, svg):
            # The font's missing-glyph box: note the character it stands
            # for, to draw as text (#708).
            n = len(svg)
            out = place(self, x, y, svg)
            if self.glyph.index == 0 and len(svg) > n and svg[-1].tag == "use":
                svg[-1].set("data-char", self.char)
                # Its token, and where its pen starts and ends, to join
                # a token's letters into words.
                svg[-1].set("data-token", repr(getattr(self, "kfxgen_token", None)))
                svg[-1].set("data-x", repr(x))
                svg[-1].set("data-end", repr(out[0]))
            return out

        draw_glyph._tagged = True
        Glyph.draw = draw_glyph
        Glyph.__init__ = init_glyph
    return ziamath


#: Elements whose children read along a line: a right-to-left equation
#: has them reversed (#708). Kindle Previewer 4.0.3 reverses `<math>`,
#: `<mrow>` and a table row's cells (probe P708); the others are rows too
#: in MathML.
_ROWS = frozenset(
    {
        "math",
        "mrow",
        "mstyle",
        "mpadded",
        "mphantom",
        "merror",
        "menclose",
        "msqrt",
        "mtd",
        "mtr",
    }
)
#: Each character and its mirror image, by Unicode's Bidi_Mirroring_Glyph,
#: for an operator in a right-to-left equation. Previewer 4.0.3 swaps ( ),
#: { } and < > but leaves [ ], ⟨ ⟩ and ≤, so `[x]` reads `]x[` and ≤
#: changes its meaning; kfxgen mirrors them all (#708).
_PAIRS = (
    "()",
    "[]",
    "{}",
    "<>",
    "⟨⟩",
    "⟦⟧",
    "⟪⟫",
    "⌈⌉",
    "⌊⌋",
    "«»",
    "‹›",
    "≤≥",
    "≪≫",
    "≮≯",
    "≰≱",
    "⊂⊃",
    "⊆⊇",
    "⊄⊅",
    "∈∋",
    "∉∌",
    "⁅⁆",
    "⦃⦄",
    "⦅⦆",
)
_MIRROR = {a: b for a, b in _PAIRS} | {b: a for a, b in _PAIRS}
_SCRIPTS = {"msub": ("sub",), "msup": ("sup",), "msubsup": ("sub", "sup")}


def _right_to_left(math):
    """`math`, a right-to-left equation, laid out left to right as Kindle
    Previewer 4.0.3 does (#708): each row's children reversed, its
    operators mirrored, a script written before its base and a square root
    as an enclosure open on the left. Previewer keeps only a root's first
    child; kfxgen keeps them all. Text in a token keeps its own order."""
    ns = math.tag[: -len(_local(math.tag))]

    def element(tag, kids=(), **attrs):
        e = math.makeelement(ns + tag, attrs)
        e.extend(kids)
        return e

    for e in list(math.iter()):
        tag = _local(e.tag)
        if tag in _SCRIPTS and len(e) == 1 + len(_SCRIPTS[tag]):
            base, *scripts = list(e)
            sub = scripts[0] if "sub" in _SCRIPTS[tag] else element("none")
            sup = scripts[-1] if "sup" in _SCRIPTS[tag] else element("none")
            e.tag = ns + "mmultiscripts"
            e[:] = [base, element("mprescripts"), sub, sup]
        elif tag == "msqrt":
            e.tag = ns + "menclose"
            e.set("notation", "top right")
    # Listed first: reordering a row while walking the tree skipped some
    # of its children.
    for e in list(math.iter()):
        tag = _local(e.tag)
        if tag in _ROWS and len(e) > 1:
            kids = list(e)
            for kid in kids:
                kid.tail = None
            e[:] = kids[::-1]
        elif tag == "mo" and e.text:
            e.text = "".join(_MIRROR.get(c, c) for c in e.text)
        if e.get("dir") is not None:
            e.set("dir", "ltr")
    return math


def _source(math):
    """The equation as ziamath reads it: annotations out, `<semantics>`
    unwrapped, an `<maction>` replaced by its selected child, a
    right-to-left one laid out left to right (#708), and a hyphen-minus
    operator written as a minus sign, which keeps it clear of
    ziamath's rewrite of `<mo>-</mo>`, a regex that swallows the markup
    between two of them. None for an equation holding an `<mglyph>`, a
    picture ziamath doesn't draw, or more than `_LONGEST` characters."""
    from lxml import etree

    math = copy.deepcopy(math)
    math.tail = None
    if any(_local(e.tag) == "mglyph" for e in math.iter()):
        return None
    for e in list(math.iter()):
        parent = e.getparent()
        if _local(e.tag) == "maction" and parent is not None:
            kids = list(e)
            try:
                chosen = kids[int(e.get("selection", 1)) - 1] if kids else None
            except (ValueError, IndexError):
                chosen = kids[0]
            if chosen is not None:
                chosen.tail = e.tail
                parent.replace(e, chosen)
            else:
                parent.remove(e)
    for e in list(math.iter()):
        if _local(e.tag) in _NOT_DRAWN and e.getparent() is not None:
            e.getparent().remove(e)
        elif _local(e.tag) == "mo" and (e.text or "").strip() == "-":
            e.text = "−"
    for e in list(math.iter()):
        parent = e.getparent()
        if _local(e.tag) == "semantics" and parent is not None:
            at = parent.index(e)
            for i, child in enumerate(list(e)):
                parent.insert(at + i, child)
            parent.remove(e)
    if len("".join(math.itertext())) > _LONGEST:
        return None
    if (math.get("dir") or "").strip().lower() == "rtl":
        math = _right_to_left(math)
    return etree.tostring(math, encoding="unicode")


def _path(d, x0, y0, scale=1):
    """An SVG path's absolute M, L, H, V, Q, C and Z commands as a KFX
    path list, moved so (x0, y0) is 0, 0 and multiplied by `scale`; None
    for anything else."""
    out, x, y, start = [], 0.0, 0.0, (0.0, 0.0)
    tokens = _TOKEN.findall(d)
    i = 0
    while i < len(tokens):
        cmd = tokens[i]
        i += 1
        if cmd in ("H", "V"):
            v = float(tokens[i])
            i += 1
            x, y = (v, y) if cmd == "H" else (x, v)
            out += [1, round((x - x0) * scale), round((y - y0) * scale)]
            continue
        if cmd not in _COMMANDS:
            return None
        code, n = _COMMANDS[cmd]
        nums = [float(t) for t in tokens[i : i + n]]
        i += n
        out.append(code)
        for j in range(0, n, 2):
            out += [round((nums[j] - x0) * scale), round((nums[j + 1] - y0) * scale)]
        if n:
            x, y = nums[-2], nums[-1]
        if cmd == "M":
            start = (x, y)
        elif cmd == "Z":
            x, y = start
    return out


def _rect(e, x0, y0):
    x, y = float(e.get("x", 0)) - x0, float(e.get("y", 0)) - y0
    right, bottom = x + float(e.get("width", 0)), y + float(e.get("height", 0))
    x, y, right, bottom = (round(v) for v in (x, y, right, bottom))
    return [0, x, y, 1, right, y, 1, right, bottom, 1, x, bottom, 4]


class _Unknown(logging.Handler):
    """Notes ziamath's warning for an element it doesn't know, which it
    lays out as a plain row."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.seen = False

    def emit(self, record):
        if str(record.msg).startswith("Undefined element"):
            self.seen = True


def _placements(svg, x0, y0):
    """ziamath's SVG as (glyph, path, matrix) placements, moved so (x0, y0)
    is 0, 0; None for anything it can't place."""
    symbols, inside = {}, set()
    for e in svg.iter():
        if _local(e.tag) == "symbol":
            path = []
            for p in e.iter():
                if _local(p.tag) == "path":
                    inside.add(id(p))
                    part = _path(p.get("d", ""), 0, 0, _GLYPH)
                    if part is None:
                        return None
                    path += part
            symbols[e.get("id")] = ([float(v) for v in e.get("viewBox").split()], path)
    placed, ends, last_token = [], float("nan"), None
    for e in svg.iter():
        local = _local(e.tag)
        if local in ("svg", "g", "symbol") or id(e) in inside:
            continue
        if local == "use":
            href = e.get("href") or e.get("{http://www.w3.org/1999/xlink}href", "")
            (vx, vy, _vw, vh), path = symbols[href.lstrip("#")]
            # ziamath scales a glyph's width and height alike; a symbol's
            # height is the font's line, never 0.
            scale = float(e.get("height")) / vh
            x = float(e.get("x", 0)) - vx * scale - x0
            y = float(e.get("y", 0)) - vy * scale - y0
            char = e.get("data-char")
            if char:
                # A character the font lacks: text, a token's letters one
                # word while each starts where the last ended, so the
                # Kindle joins them (#708). Two tokens are two texts, as
                # two variables side by side (QA on #730). A symbol is 12
                # of its units to the em.
                k = round(scale * 12 * _TEXT_SCALE, 3)
                last = placed[-1] if placed else None
                token = e.get("data-token")
                if (
                    isinstance(last, MathText)
                    and token not in (None, "None")
                    and token == last_token
                    and abs(float(e.get("data-x", "nan")) - ends) < 0.5
                ):
                    placed[-1] = MathText(last.text + char, last.matrix)
                else:
                    placed.append(
                        MathText(char, (k, 0, 0, k, round(x, 1), round(y, 1)))
                    )
                ends, last_token = float(e.get("data-end", "nan")), token
                continue
            if not path:
                continue  # a space or an invisible operator: no width, no outline
            s = round(scale / _GLYPH, 6)
            placed.append(
                (href.lstrip("#"), tuple(path), (s, 0, 0, s, round(x, 1), round(y, 1)))
            )
        elif local == "rect":
            placed.append((None, tuple(_rect(e, x0, y0)), _IDENTITY))
        elif local == "path":
            path = _path(e.get("d", ""), x0, y0)
            if path is None:
                return None
            placed.append((None, tuple(path), _IDENTITY))
        else:
            return None
    if any(not p[1] for p in placed if not isinstance(p, MathText)):
        return None
    return placed


def draw(math):
    """The `<math>` element drawn, or None when it can't be."""
    zm = _ziamath()
    source = _source(math) if zm is not None else None
    if source is None:
        return None
    unknown = _Unknown()
    root = logging.getLogger()
    level = root.level
    if root.getEffectiveLevel() > logging.WARNING:
        root.setLevel(logging.WARNING)
    root.addHandler(unknown)
    try:
        # Each glyph a <symbol>, placed by a <use>.
        zm.config.svg2 = True
        zm.config.precision = 3
        _budget.left = _STEPS
        svg = ET.fromstring(zm.Math(source, size=_EM).svg())
        x0, y0, w, h = (float(v) for v in svg.get("viewBox").split())
    except Exception:
        return None
    finally:
        root.removeHandler(unknown)
        root.setLevel(level)
    if unknown.seen:
        return None
    try:
        paths = _placements(svg, x0, y0)
    except (ValueError, KeyError, ZeroDivisionError):
        return None
    if not paths:
        return None
    return MathDrawing(round(w), round(h), round(y0 + h), tuple(paths))
