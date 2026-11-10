"""A link's colour from a `:link` or `:visited` rule (#733).

Kindle Previewer 4.0.3 (probe P733, decoded) reads `:link` and `:visited` as
always true: such a rule sets a link's colour as the same rule without them
would, the more specific rule winning, then the later one. It writes that
colour as the link's, `$576`/`$577`. `:hover`, `:active` and `:focus` set
none, and a colour in the link's `style` attribute wins over both. It drops
a rule with a bare `a:visited` in its selector (P741m/n/o, #741 QA).

calibre's Stylizer keeps these rules apart from the element's style, and its
flattened CSS loses their place in the cascade, so kfxgen reads them before
flattening (`mark_link_state_colours`, from `specialize_css_for_output`) and
records the winner on the link. These tests give it calibre's rule tuples,
(sort key, selector, cssdict, selector text, href), in cascade order.

Through calibre: tests/integration/test_calibre_link_state_colour.py.
"""

from lxml import etree

from kfxgen.converter import (
    _SOURCE_LINK_COLOUR,
    _specified_colour,
    mark_link_state_colours,
)

XHTML = "http://www.w3.org/1999/xhtml"


def _doc(body):
    return etree.fromstring(f'<html xmlns="{XHTML}"><body>{body}</body></html>')


class _Stylizer:
    """calibre's Stylizer as far as these read it: `rules` in cascade order,
    and `style(elem).get("color")`, the colour an element declares."""

    def __init__(self, rules, colours=None):
        self.rules = [
            ((0, i), None, {"color": c}, t, None) for i, (t, c) in enumerate(rules)
        ]
        self._colours = colours or {}

    def style(self, elem):
        return {"color": self._colours.get(elem.get("id"))}


def _by_class(root):
    """A selector matcher for these tests: `a.NAME`, `.NAME`, `p.NAME a`,
    each with any `:pseudo` dropped, as calibre's engine matches them."""

    def select(text):
        base = text.split(":")[0]
        if base.startswith("p.") and base.endswith(" a"):
            cls = base[2:-2]
            return [
                a
                for p in root.iter(f"{{{XHTML}}}p")
                if cls in (p.get("class") or "").split()
                for a in p.iter(f"{{{XHTML}}}a")
            ]
        names = base.lstrip("a").lstrip(".").split(".")
        return [
            e
            for e in root.iter()
            if isinstance(e.tag, str)
            and set(names) <= set((e.get("class") or "").split())
        ]

    return select


def _marked(rules, body):
    root = _doc(body)
    mark_link_state_colours(root, _Stylizer(rules), _by_class(root))
    return {
        e.get("id"): e.get(_SOURCE_LINK_COLOUR)
        for e in root.iter()
        if isinstance(e.tag, str) and e.get("id")
    }


def test_a_link_rule_gives_a_link_its_colour():
    assert _marked([("a.x:link", "green")], '<a id="a" class="x" href="#t">w</a>') == {
        "a": "green"
    }
    assert _marked(
        [("a.x:visited", "purple")], '<a id="a" class="x" href="#t">w</a>'
    ) == {"a": "purple"}


def test_the_later_rule_wins_link_or_visited():
    # P733 L03 and L04: neither state comes first; the cascade decides.
    link = '<a id="a" class="x" href="#t">w</a>'
    assert _marked([("a.x:link", "green"), ("a.x:visited", "purple")], link) == {
        "a": "purple"
    }
    assert _marked([("a.x:visited", "purple"), ("a.x:link", "green")], link) == {
        "a": "green"
    }


def test_a_later_plain_rule_keeps_calibres_colour():
    # P733 L08: `.x:link` then `.x.y`, the same specificity: the plain rule
    # is later, so calibre's own colour stands and nothing is recorded.
    link = '<a id="a" class="x y" href="#t">w</a>'
    assert _marked([(".x:link", "green"), (".x.y", "#999999")], link) == {"a": None}
    assert _marked([(".x.y", "#999999"), (".x:link", "green")], link) == {"a": "green"}


def test_hover_active_and_focus_give_no_colour():
    # P733 L05, L20, L26: not alone, and not after a :link rule.
    link = '<a id="a" class="x" href="#t">w</a>'
    assert _marked([("a.x:hover", "orange")], link) == {"a": None}
    assert _marked([("a.x:link", "green"), ("a.x:hover", "orange")], link) == {
        "a": "green"
    }
    assert _marked([("a.x:active", "orange"), ("a.x:focus", "red")], link) == {
        "a": None
    }
    # P741q L02: a later :focus rule leaves the :link colour.
    assert _marked([("a.x:link", "green"), ("a.x:focus", "red")], link) == {
        "a": "green"
    }


def _marked_all(rules):
    """Each selector here matches the one link, as calibre's engine would."""
    root = _doc('<p class="c"><a id="a" class="x" href="#t">w</a></p>')
    links = list(root.iter(f"{{{XHTML}}}a"))
    mark_link_state_colours(root, _Stylizer(rules), lambda _text: links)
    return links[0].get(_SOURCE_LINK_COLOUR)


def test_a_rule_naming_a_bare_a_visited_is_dropped():
    # P741m/n/o (#741 QA): Previewer drops a rule whose selector has `a`
    # straight before `:visited`, in any case and in any position.
    assert _marked_all([("a:link", "green"), ("a:visited", "purple")]) == "green"
    assert _marked_all([("a:visited", "purple"), ("a:link", "green")]) == "green"
    for text in (
        "a:visited",
        "p a:visited",
        ".c a:visited",
        "a:visited.x",
        "A:VISITED",
    ):
        assert _marked_all([(text, "purple")]) is None, text
    # P741p L08: so a plain rule before it keeps calibre's colour.
    assert _marked_all([("p a", "green"), ("a:visited", "purple")]) is None


def test_a_qualified_visited_rule_is_kept():
    # P741o L07, L11, L12, L18 and P733 L02.
    for text in (
        ":visited",
        "*:visited",
        ".c :visited",
        "a.x:visited",
        "a[href]:visited",
        "a:not(.y):visited",
    ):
        assert _marked_all([(text, "purple")]) == "purple", text


def test_a_colour_in_the_links_style_wins():
    # P733 L07, L17.
    link = '<a id="a" class="x" href="#t" style="color: #999999">w</a>'
    assert _marked([("a.x:link", "green")], link) == {"a": None}
    # A colour named only inside another property doesn't count.
    link = '<a id="a" class="x" href="#t" style="border-color: red">w</a>'
    assert _marked([("a.x:link", "green")], link) == {"a": "green"}


def test_only_a_link_with_an_href_is_marked():
    # P733 L21: an <a> without an href is no link, and a :link rule on
    # another element is none of a link's.
    body = '<a id="a" class="x">w</a><span id="s" class="x">w</span>'
    assert _marked([(".x:link", "green")], body) == {"a": None, "s": None}


def test_a_descendant_rule_reaches_the_links_in_it():
    # P733 L18: `p.g a:link`.
    body = '<p class="g"><a id="a" href="#t">w</a></p><p><a id="b" href="#t">w</a></p>'
    assert _marked([("p.g a:link", "blue")], body) == {"a": "blue", "b": None}


def test_a_book_without_such_rules_is_left_alone():
    root = _doc('<a id="a" class="x" href="#t">w</a>')

    def select(_text):
        raise AssertionError("no selector is matched without a :link rule")

    mark_link_state_colours(root, _Stylizer([("a.x", "green")]), select)
    assert root.find(f".//{{{XHTML}}}a").get(_SOURCE_LINK_COLOUR) is None


def test_the_recorded_colour_replaces_calibres_and_reaches_inside_the_link():
    root = _doc(
        '<p id="p"><a id="a" href="#t"><i id="i">w</i></a></p>'
        f'<p id="q"><a id="b" href="#t" {_SOURCE_LINK_COLOUR}="inherit">w</a></p>'
    )
    a = root.find(f".//{{{XHTML}}}a")
    a.set(_SOURCE_LINK_COLOUR, "green")
    st = _Stylizer([], {"p": "red", "a": "#999999", "q": "red"})
    find = {e.get("id"): e for e in root.iter() if isinstance(e.tag, str)}
    assert _specified_colour(st, find["a"]) == "green"
    # Inherited by what the link holds.
    assert _specified_colour(st, find["i"]) == "green"
    # `inherit` (and `currentColor`) take the colour around the link.
    assert _specified_colour(st, find["b"]) == "red"
    assert _specified_colour(st, find["p"]) == "red"
