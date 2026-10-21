"""MathML equations drawn as glyph outlines, as Kindle Previewer draws them
(#235), by ziamath, bundled in `vendor/`.

A drawing is measured in thousandths of an em from its top left corner, y
downwards; `depth` is how far it reaches below the text's baseline. Each
path is a KFX path list: 0 move, 1 line, 2 quadratic curve, 3 cubic curve,
4 close, each followed by its points."""

import copy
import logging
import re
import xml.etree.ElementTree as ET
from collections import namedtuple

from . import _vendor

MathDrawing = namedtuple("MathDrawing", "width height depth paths")

#: ziamath's size: one unit is a thousandth of an em, as in Previewer's.
_EM = 1000
_NOT_DRAWN = {"annotation", "annotation-xml"}
_COMMANDS = {"M": (0, 2), "L": (1, 2), "Q": (2, 4), "C": (3, 6), "Z": (4, 0)}
_TOKEN = re.compile(r"[A-Za-z]|-?(?:\d+\.?\d*|\.\d+)(?:[eE]-?\d+)?")


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ziamath():
    """The bundled ziamath, or None when it can't be loaded."""
    try:
        if not _vendor.on_path():
            return None
        import ziamath
    except Exception:
        return None
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


def _path(d, x0, y0):
    """An SVG path's absolute M, L, H, V, Q, C and Z commands as a KFX
    path list, moved so (x0, y0) is 0, 0; None for anything else."""
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
            out += [1, round(x - x0), round(y - y0)]
            continue
        if cmd not in _COMMANDS:
            return None
        code, n = _COMMANDS[cmd]
        nums = [float(t) for t in tokens[i : i + n]]
        i += n
        out.append(code)
        for j in range(0, n, 2):
            out += [round(nums[j] - x0), round(nums[j + 1] - y0)]
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
        # Each glyph as a path in place, not a <symbol> and its <use>.
        zm.config.svg2 = False
        zm.config.precision = 1
        svg = ET.fromstring(zm.Math(source, size=_EM).svg())
        x0, y0, w, h = (float(v) for v in svg.get("viewBox").split())
    except Exception:
        return None
    finally:
        root.removeHandler(unknown)
        root.setLevel(level)
    if unknown.seen:
        return None
    paths = []
    for e in svg.iter():
        local = _local(e.tag)
        if local == "path":
            path = _path(e.get("d", ""), x0, y0)
        elif local == "rect":
            path = _rect(e, x0, y0)
        elif local in ("svg", "g"):
            continue
        else:
            return None
        if not path:
            return None
        paths.append(tuple(path))
    if not paths:
        return None
    return MathDrawing(round(w), round(h), round(y0 + h), tuple(paths))
