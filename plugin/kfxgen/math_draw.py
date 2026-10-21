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

#: ziamath's size: one unit is a thousandth of an em, as in Previewer's.
_EM = 1000
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


class _OverBudget(Exception):
    pass


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ziamath():
    """The bundled ziamath, or None when it can't be loaded, each node it
    lays out counted against `_budget`."""
    try:
        if not _vendor.on_path():
            return None
        import ziamath
        from ziamath.nodes.mnode import Mnode
    except Exception:
        return None
    if not getattr(Mnode.fromelement, "_budgeted", False):
        layout = Mnode.fromelement.__func__

        def fromelement(cls, element, parent, **kwargs):
            if _budget.left <= 0:
                raise _OverBudget
            _budget.left -= 1
            return layout(cls, element, parent, **kwargs)

        fromelement._budgeted = True
        Mnode.fromelement = classmethod(fromelement)
    return ziamath


def _source(math):
    """The equation as ziamath reads it: annotations out, `<semantics>`
    unwrapped, an `<maction>` replaced by its selected child, and a
    hyphen-minus operator written as a minus sign, which keeps it clear of
    ziamath's rewrite of `<mo>-</mo>`, a regex that swallows the markup
    between two of them. None for an equation holding an `<mglyph>`, a
    picture ziamath doesn't draw."""
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
    placed = []
    for e in svg.iter():
        local = _local(e.tag)
        if local in ("svg", "g", "symbol") or id(e) in inside:
            continue
        if local == "use":
            href = e.get("href") or e.get("{http://www.w3.org/1999/xlink}href", "")
            (vx, vy, _vw, vh), path = symbols[href.lstrip("#")]
            if not path:
                continue  # a space or an invisible operator: no width, no outline
            # ziamath scales a glyph's width and height alike; a symbol's
            # height is the font's line, never 0.
            scale = float(e.get("height")) / vh
            x = float(e.get("x", 0)) - vx * scale - x0
            y = float(e.get("y", 0)) - vy * scale - y0
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
    if any(not path for _key, path, _m in placed):
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
