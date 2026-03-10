from unittest.mock import patch

from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import Status, Window
from windows_mcp.tree.views import BoundingBox


def make_window(
    name: str,
    handle: int,
    process_id: int,
    status: Status = Status.NORMAL,
) -> Window:
    return Window(
        name=name,
        is_browser=False,
        depth=0,
        status=status,
        bounding_box=BoundingBox(left=0, top=0, right=100, bottom=100, width=100, height=100),
        handle=handle,
        process_id=process_id,
    )


def make_desktop() -> Desktop:
    with patch.object(Desktop, "__init__", lambda self: None):
        return Desktop()


class TestLaunchVerification:
    def test_wait_for_launched_window_returns_new_active_window(self):
        desktop = make_desktop()
        before = make_window("Before", handle=1, process_id=10)
        launched = make_window("New App", handle=2, process_id=20)

        desktop._collect_window_candidates = lambda: [launched]

        result = desktop._wait_for_launched_window(
            before_handles={before.handle},
            before_active_handle=before.handle,
            expected_name="New App",
            pid=0,
            timeout=0.01,
            poll_interval=0,
        )

        assert result == launched

    def test_wait_for_launched_window_uses_pid_match(self):
        desktop = make_desktop()
        existing = make_window("Existing", handle=1, process_id=10)
        launched = make_window("Localized Title", handle=1, process_id=99)

        desktop._collect_window_candidates = lambda: [launched, existing]

        result = desktop._wait_for_launched_window(
            before_handles={existing.handle},
            before_active_handle=existing.handle,
            expected_name="Different Name",
            pid=99,
            timeout=0.01,
            poll_interval=0,
        )

        assert result == launched

    def test_app_launch_returns_verified_window_name(self):
        desktop = make_desktop()
        existing = make_window("Terminal", handle=1, process_id=10)
        launched = make_window("latest.log - Notepad", handle=2, process_id=11)

        desktop._collect_window_candidates = lambda: [existing]
        desktop.launch_app = lambda name: ("", 0, 0)
        desktop._wait_for_launched_window = lambda **kwargs: launched

        result = desktop.app(mode="launch", name="记事本")

        assert result == "latest.log - Notepad launched."


class TestProtocolLaunchTargets:
    def test_launch_app_bypasses_start_menu_for_protocol_target(self):
        desktop = make_desktop()
        commands: list[str] = []

        def execute_command(command: str, timeout: int = 10):
            commands.append(command)
            return ("", 0)

        desktop.execute_command = execute_command
        desktop.get_apps_from_start_menu = lambda: {"should-not": "be-used"}

        response, status, pid = desktop.launch_app("ms-settings:bluetooth")

        assert response == ""
        assert status == 0
        assert pid == 0
        assert commands == ["Start-Process 'ms-settings:bluetooth'"]

    def test_protocol_detection_does_not_treat_drive_path_as_uri(self):
        desktop = make_desktop()

        assert desktop._is_protocol_target("ms-settings:bluetooth") is True
        assert desktop._is_protocol_target(r"C:\\Windows\\notepad.exe") is False


class TestSwitchVerification:
    def test_wait_for_foreground_handle_returns_false_on_timeout(self):
        desktop = make_desktop()

        with patch("windows_mcp.desktop.service.win32gui.GetForegroundWindow", return_value=1):
            result = desktop._wait_for_foreground_handle(target_handle=2, timeout=0.01, poll_interval=0)

        assert result is False

    def test_switch_app_fails_when_foreground_does_not_change(self):
        desktop = make_desktop()
        target = make_window("Untitled - Notepad", handle=22, process_id=220)
        desktop.desktop_state = type(
            "State",
            (),
            {
                "active_window": make_window("Calculator", handle=11, process_id=110),
                "windows": [target],
            },
        )()
        desktop.bring_window_to_top = lambda handle: None
        desktop._wait_for_foreground_handle = lambda handle: False

        with patch("windows_mcp.desktop.service.uia.IsIconic", return_value=False):
            result, status = desktop.switch_app("Notepad")

        assert status == 1
        assert result == "Failed to switch focus to Untitled - Notepad window."


class TestBringWindowToTop:
    def test_brings_window_to_top_falls_back_when_foreground_unchanged(self):
        desktop = make_desktop()
        fallback_calls: list[int] = []
        foreground_sequence = iter([100, 100])

        desktop._focus_window_fallback = lambda handle: fallback_calls.append(handle)

        with (
            patch("windows_mcp.desktop.service.win32gui.IsWindow", return_value=True),
            patch("windows_mcp.desktop.service.win32gui.IsIconic", return_value=False),
            patch("windows_mcp.desktop.service.win32gui.GetForegroundWindow", side_effect=lambda: next(foreground_sequence)),
            patch("windows_mcp.desktop.service.win32process.GetWindowThreadProcessId", side_effect=[(10, 1), (20, 2)]),
            patch("windows_mcp.desktop.service.win32process.AttachThreadInput"),
            patch("windows_mcp.desktop.service.win32gui.SetForegroundWindow"),
            patch("windows_mcp.desktop.service.win32gui.BringWindowToTop"),
            patch("windows_mcp.desktop.service.win32gui.SetWindowPos"),
        ):
            desktop.bring_window_to_top(200)

        assert fallback_calls == [200]
