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
