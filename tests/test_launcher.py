"""The desktop launcher: finding a port, remembering the running dashboard."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import launcher  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_files(monkeypatch, tmp_path):
    """Never touch the real data/launcher.json or launcher.log."""
    monkeypatch.setattr(launcher, "DATA", tmp_path / "data")
    monkeypatch.setattr(launcher, "STATE_FILE", tmp_path / "data" / "launcher.json")
    monkeypatch.setattr(launcher, "LOG_FILE", tmp_path / "data" / "launcher.log")


def test_tests_use_a_temporary_state_file(tmp_path):
    launcher.write_state(1, 8501)
    assert (tmp_path / "data" / "launcher.json").exists()
    assert launcher.read_state() == {"pid": 1, "port": 8501}


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    assert launcher.read_state(path) is None
    launcher.write_state(1234, 8505, path)
    assert launcher.read_state(path) == {"pid": 1234, "port": 8505}
    launcher.clear_state(path)
    assert launcher.read_state(path) is None


def test_corrupt_state_is_ignored(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json")
    assert launcher.read_state(path) is None
    path.write_text('{"pid": 1}')
    assert launcher.read_state(path) is None


def test_stale_state_from_a_dead_process_is_cleared(tmp_path):
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    path = tmp_path / "state.json"
    launcher.write_state(finished.pid, 8599, path)
    assert launcher.running_instance(path) is None
    assert not path.exists()


def test_process_alive():
    assert launcher.process_alive(os.getpid())
    assert not launcher.process_alive(0)


def test_busy_port_is_skipped():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert not launcher.port_free(port)
        assert launcher.find_free_port(port, 5) != port


def test_no_free_port_gives_a_clear_message(monkeypatch):
    monkeypatch.setattr(launcher, "port_free", lambda port: False)
    with pytest.raises(SystemExit, match="No free port"):
        launcher.find_free_port(9000, 3)


def test_server_command_is_local_only_and_headless(tmp_path):
    cmd = launcher.server_command(Path("/x/python"), 8502, root=tmp_path)
    assert cmd[:4] == ["/x/python", "-m", "streamlit", "run"]
    assert cmd[4] == str(tmp_path / "app" / "Home.py")
    flags = dict(zip(cmd[5::2], cmd[6::2]))
    assert flags["--server.headless"] == "true"
    assert flags["--server.port"] == "8502"
    assert flags["--server.address"] == "localhost"  # not reachable from other machines


def test_existing_environment_is_reused(tmp_path):
    python = launcher.venv_python(tmp_path)
    python.parent.mkdir(parents=True)
    python.touch()
    assert launcher.ensure_environment(tmp_path) == python


def test_stop_when_nothing_is_running(tmp_path):
    assert launcher.stop(tmp_path / "state.json") is False


def test_stop_ends_a_running_process(tmp_path):
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    path = tmp_path / "state.json"
    launcher.write_state(sleeper.pid, 8598, path)
    assert launcher.stop(path) is True
    assert sleeper.wait(timeout=10) is not None
    assert not path.exists()


def test_launch_files_exist_and_point_at_the_launcher():
    command = (ROOT / "Vendorus.command").read_text()
    assert "launcher.py" in command and os.access(ROOT / "Vendorus.command", os.X_OK)
    bat = (ROOT / "Vendorus.bat").read_bytes()
    assert b"launcher.py" in bat and b"\r\n" in bat  # Windows line endings


def test_port_used_on_all_addresses_is_not_free():
    """Regression: Streamlit listens on all addresses; binding 127.0.0.1 on
    the same port still succeeds on macOS, so a bind-only check was fooled."""
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        server.bind(("::", 0))
        server.listen()
        port = server.getsockname()[1]
        assert not launcher.port_free(port)


def test_state_records_the_owning_launcher(tmp_path):
    path = tmp_path / "state.json"
    launcher.write_state(10, 8501, path, owner=20)
    assert launcher.read_state(path) == {"pid": 10, "port": 8501, "owner": 20}


def test_splash_page_shows_message_and_follows_dark_mode():
    page = launcher.splash_html("Starting Vendorus…")
    assert "Starting Vendorus…" in page and "prefers-color-scheme: dark" in page
    assert 'class="spin"' in page
    assert 'class="spin"' not in launcher.splash_html("Failed", "details", spinner=False)


def test_app_icon_is_drawn_once(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "DATA", tmp_path)
    icon = launcher.app_icon_png()
    assert icon == tmp_path / "app_icon.png" and icon.stat().st_size > 1000
    assert launcher.app_icon_png() == icon


def test_window_falls_back_to_browser_without_pywebview(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_webview(name, *args, **kwargs):
        if name == "webview":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    calls = []
    monkeypatch.setattr(builtins, "__import__", no_webview)
    monkeypatch.setattr(launcher, "run_browser", lambda python: calls.append(python) or 0)
    assert launcher.run_window(Path("/x/python")) == 0
    assert calls == [Path("/x/python")]


def test_pywebview_is_a_requirement():
    assert "pywebview" in (ROOT / "requirements.txt").read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows behaviour")
def test_shortcut_is_windows_only():
    assert launcher.install_windows_shortcut() is None


def test_mac_app_builder_copies_gui_python_and_sets_identity():
    script = (ROOT / "scripts" / "make_mac_app.sh").read_text()
    assert "Resources/Python.app/Contents/MacOS/Python" in script
    assert "local.vendorus.app" in script and launcher.APP_ID == "local.vendorus.app"


def test_code_version_changes_when_code_changes(tmp_path):
    (tmp_path / "app").mkdir()
    page = tmp_path / "app" / "page.py"
    page.write_text("x = 1")
    os.utime(page, (1000, 1000))
    before = launcher.code_version(tmp_path)
    os.utime(page, (2000, 2000))
    assert launcher.code_version(tmp_path) != before
    (tmp_path / "app" / "new.py").write_text("")
    assert launcher.code_version(tmp_path).endswith(":2")


@pytest.fixture
def running_server(monkeypatch):
    """A stand-in dashboard: a live process, with health checks faked."""
    server = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(launcher, "healthy", lambda port, timeout=1.0: True)
    yield server
    server.kill()


def test_dashboard_with_current_code_is_reused(tmp_path, running_server):
    path = tmp_path / "state.json"
    launcher.write_state(running_server.pid, 8597, path, code="v2")
    assert launcher.reusable_instance(path, current_code="v2")["pid"] == running_server.pid


def test_dashboard_left_running_with_old_code_is_restarted(tmp_path, running_server):
    """Regression: a dashboard started before an update was reused, so new
    pages failed to import things the old code didn't have."""
    path = tmp_path / "state.json"
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()  # its launcher window is gone
    launcher.write_state(running_server.pid, 8597, path, owner=finished.pid, code="v1")
    assert launcher.reusable_instance(path, current_code="v2") is None
    assert running_server.wait(timeout=10) is not None  # the old dashboard was stopped
    assert not path.exists()


def test_old_code_dashboard_owned_by_an_open_window_is_left_alone(tmp_path, running_server):
    path = tmp_path / "state.json"
    launcher.write_state(running_server.pid, 8597, path, owner=os.getpid(), code="v1")
    assert launcher.reusable_instance(path, current_code="v2")["pid"] == running_server.pid
    assert running_server.poll() is None


def test_dashboards_from_before_this_fix_count_as_old(tmp_path, running_server):
    path = tmp_path / "state.json"
    launcher.write_state(running_server.pid, 8597, path)  # no code recorded
    assert launcher.reusable_instance(path, current_code="v2") is None
