from unittest.mock import patch

import pytest

from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import DesktopState


def make_desktop() -> Desktop:
    with patch.object(Desktop, "__init__", lambda self: None):
        return Desktop()


class TestDesktopStateFreshness:
    def test_require_fresh_desktop_state_rejects_missing_state(self):
        desktop = make_desktop()
        desktop.desktop_state = None

        with pytest.raises(ValueError, match="Please call Snapshot first"):
            desktop.require_fresh_desktop_state()

    def test_require_fresh_desktop_state_rejects_stale_state(self):
        desktop = make_desktop()
        desktop.desktop_state = DesktopState(
            active_desktop={"name": "Desktop 1"},
            all_desktops=[],
            active_window=None,
            windows=[],
            captured_at_epoch=100.0,
        )

        with patch("windows_mcp.desktop.service.time", return_value=112.5):
            with pytest.raises(ValueError, match="12.5s old"):
                desktop.require_fresh_desktop_state(max_age_seconds=10.0)

    def test_require_fresh_desktop_state_accepts_recent_state(self):
        desktop = make_desktop()
        desktop.desktop_state = DesktopState(
            active_desktop={"name": "Desktop 1"},
            all_desktops=[],
            active_window=None,
            windows=[],
            captured_at_epoch=100.0,
        )

        with patch("windows_mcp.desktop.service.time", return_value=105.0):
            desktop.require_fresh_desktop_state(max_age_seconds=10.0)
