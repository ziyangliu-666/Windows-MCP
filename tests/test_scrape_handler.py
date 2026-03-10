from __future__ import annotations

from types import SimpleNamespace

from windows_mcp.server_core import scrape_handler
from windows_mcp.tree.views import BoundingBox, Center, ScrollElementNode, TextElementNode, TreeState


class FakeDesktop:
    def __init__(self, tree_state: TreeState):
        self.tree_state = tree_state

    def get_state(self, *, use_vision: bool, use_dom: bool):
        return SimpleNamespace(tree_state=self.tree_state)


class FakeRuntime:
    def __init__(self, tree_state: TreeState):
        self.desktop = FakeDesktop(tree_state)


def make_dom_node(vertical_scroll_percent: float) -> ScrollElementNode:
    box = BoundingBox(left=0, top=0, right=100, bottom=100, width=100, height=100)
    center = Center(x=50, y=50)
    return ScrollElementNode(
        name="Document",
        control_type="Document",
        window_name="Example Domain - Google Chrome",
        bounding_box=box,
        center=center,
        metadata={"vertical_scroll_percent": vertical_scroll_percent},
    )


def test_scrape_handler_reads_dom_scroll_percent_from_metadata():
    tree_state = TreeState(
        dom_node=make_dom_node(0),
        dom_informative_nodes=[TextElementNode(text="Example Domain")],
    )
    runtime = FakeRuntime(tree_state)

    result = scrape_handler(runtime, url="https://example.com/", use_dom=True)

    assert "Reached top" in result
    assert "Example Domain" in result


def test_scrape_handler_handles_missing_dom_scroll_percent():
    tree_state = TreeState(
        dom_node=make_dom_node(0),
        dom_informative_nodes=[TextElementNode(text="Example Domain")],
    )
    tree_state.dom_node.metadata = {}
    runtime = FakeRuntime(tree_state)

    result = scrape_handler(runtime, url="https://example.com/", use_dom=True)

    assert "Reached top" in result
