"""Start the Vendorus dashboard and open it in your browser.

Double-click Vendorus.command (Mac) or Vendorus.bat (Windows), or run:

    python launcher.py              start, open the browser, stop on Ctrl+C
    python launcher.py --background start in the background and return
    python launcher.py --stop       stop a dashboard started by this launcher

If the dashboard is already running, it just opens the browser again.
On first run it creates the .venv and installs requirements.txt.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "data" / "launcher.json"
LOG_FILE = ROOT / "data" / "launcher.log"
FIRST_PORT = 8501
PORT_RANGE = 20
STARTUP_TIMEOUT = 90  # seconds; the first start after install is slow
MIN_PYTHON = (3, 12)
IS_WINDOWS = os.name == "nt"


def say(message: str) -> None:
    print(f"[Vendorus] {message}", flush=True)


# --- environment -------------------------------------------------------------

def venv_python(root: Path = ROOT) -> Path:
    if IS_WINDOWS:
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def ensure_environment(root: Path = ROOT) -> Path:
    """Return the project's Python, creating .venv and installing packages
    the first time."""
    python = venv_python(root)
    if python.exists():
        return python
    if sys.version_info < MIN_PYTHON:
        raise SystemExit(
            f"Vendorus needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer "
            f"(this is {sys.version.split()[0]}). Install it from python.org and try again."
        )
    say("First run: creating the Python environment (a minute or two)...")
    venv.EnvBuilder(with_pip=True).create(root / ".venv")
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "-r", str(root / "requirements.txt")], check=True
    )
    say("Environment ready.")
    return python


# --- running instance --------------------------------------------------------

def read_state(path: Path = STATE_FILE) -> dict | None:
    try:
        state = json.loads(path.read_text())
        return state if {"pid", "port"} <= set(state) else None
    except (OSError, ValueError):
        return None


def write_state(pid: int, port: int, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "port": port}))


def clear_state(path: Path = STATE_FILE) -> None:
    path.unlink(missing_ok=True)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def healthy(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/_stcore/health", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def running_instance(path: Path = STATE_FILE) -> dict | None:
    """The dashboard this launcher started, if it's still up."""
    state = read_state(path)
    if state and process_alive(state["pid"]) and healthy(state["port"]):
        return state
    if state:
        clear_state(path)  # left over from a crash or restart
    return None


def port_free(port: int) -> bool:
    """True only if nothing is listening on the port, on any address.

    Binding alone isn't enough: on macOS a server listening on all addresses
    (like Streamlit) doesn't stop another program binding 127.0.0.1 on the
    same port. So first try to connect, over IPv4 and IPv6.
    """
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex((host, port)) == 0:
                    return False  # something answered
        except OSError:
            pass  # e.g. IPv6 not available
    for family, host in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                # Allow ports that were just closed (TIME_WAIT); live servers
                # were already caught by the connect check above.
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, port))
        except OSError as exc:
            if family == socket.AF_INET or exc.errno == getattr(socket, "EADDRINUSE", 48) or "in use" in str(exc):
                return False
    return True


def find_free_port(start: int = FIRST_PORT, count: int = PORT_RANGE) -> int:
    for port in range(start, start + count):
        if port_free(port):
            return port
    raise SystemExit(f"No free port between {start} and {start + count - 1}.")


# --- start / stop -------------------------------------------------------------

def server_command(python: Path, port: int, root: Path = ROOT) -> list[str]:
    return [
        str(python), "-m", "streamlit", "run", str(root / "app" / "Home.py"),
        "--server.headless", "true",
        "--server.port", str(port),
        "--server.address", "localhost",
        "--browser.gatherUsageStats", "false",
    ]


def start_server(python: Path, port: int, background: bool) -> subprocess.Popen:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "a")
    kwargs: dict = {"cwd": ROOT, "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if background:
        # Keep running after this launcher exits.
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
    return subprocess.Popen(server_command(python, port), **kwargs)


def wait_until_healthy(process: subprocess.Popen, port: int, timeout: float = STARTUP_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if healthy(port):
            # Make sure it's our server answering: ours must still be running
            # a moment later (a server that couldn't get the port exits).
            time.sleep(1.5)
            return process.poll() is None and healthy(port)
        time.sleep(0.5)
    return False


def stop(path: Path = STATE_FILE) -> bool:
    state = read_state(path)
    clear_state(path)
    if not state or not process_alive(state["pid"]):
        return False
    pid = state["pid"]
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return True
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not process_alive(pid):
            break
        time.sleep(0.25)
    else:
        os.kill(pid, signal.SIGKILL)
    return True


def open_browser(port: int) -> None:
    url = f"http://localhost:{port}"
    say(f"Opening {url}")
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the Vendorus dashboard.")
    parser.add_argument("--background", action="store_true", help="start and return immediately")
    parser.add_argument("--stop", action="store_true", help="stop the running dashboard")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    args = parser.parse_args(argv)

    if args.stop:
        say("Stopped." if stop() else "Not running.")
        return 0

    existing = running_instance()
    if existing:
        say(f"Already running on port {existing['port']}.")
        if not args.no_browser:
            open_browser(existing["port"])
        return 0

    python = ensure_environment()
    port = find_free_port()
    say(f"Starting on port {port}...")
    process = start_server(python, port, background=args.background)
    write_state(process.pid, port)

    if not wait_until_healthy(process, port):
        stop()
        say(f"The dashboard didn't start. Details are in {LOG_FILE}")
        return 1
    if not args.no_browser:
        open_browser(port)

    if args.background:
        say("Running in the background.")
        return 0

    say("Running. Close this window or press Ctrl+C to stop.")

    def shut_down(*_):
        raise KeyboardInterrupt

    if not IS_WINDOWS:
        signal.signal(signal.SIGHUP, shut_down)  # Terminal window closed
    signal.signal(signal.SIGTERM, shut_down)
    try:
        process.wait()
        say(f"The dashboard stopped. Details are in {LOG_FILE}")
        return process.returncode or 0
    except KeyboardInterrupt:
        say("Stopping...")
        stop()
        return 0
    finally:
        if read_state() and read_state()["pid"] == process.pid and process.poll() is not None:
            clear_state()


if __name__ == "__main__":
    sys.exit(main())
