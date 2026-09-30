import pytest

from tests._kfx_introspect import iter_entries


def _node(eid, *children):
    node = {"$155": eid}
    if children:
        node["$146"] = list(children)
    return node


@pytest.mark.unit
def test_iter_entries_walks_nested_containers_in_reading_order():
    tree = [
        _node(1),
        _node(2, _node(3, _node(4, _node(5), _node(6)))),
        _node(7),
    ]
    assert [e["$155"] for e in iter_entries(tree)] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.unit
def test_iter_entries_ignores_a_non_list_146():
    # An entry's $145 content reference has no $146, but a $145 *fragment*
    # holds strings under $146; the walker must never descend into strings.
    assert [e["$155"] for e in iter_entries([{"$155": 1, "$146": "text"}])] == [1]
