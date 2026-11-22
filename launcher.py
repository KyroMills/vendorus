"""Start Vendorus as a desktop app.

Double-click Vendorus.app (Mac), Vendorus.command (Mac) or Vendorus.bat
(Windows), or run:

    python launcher.py               open Vendorus in its own app window
    python launcher.py --browser     open it in your web browser instead
    python launcher.py --background  start the dashboard in the background
    python launcher.py --stop        stop a dashboard started by this launcher
    python launcher.py --setup-only  create .venv and install requirements

The app window runs the dashboard on this computer only; closing the window
stops it. On first run the launcher creates .venv and installs
requirements.txt.
"""
from __future__ import annotations

import argparse
import atexit
import glob
import json
import os
import signal
import site
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATE_FILE = DATA / "launcher.json"
LOG_FILE = DATA / "launcher.log"
FIRST_PORT = 8501
PORT_RANGE = 20
STARTUP_TIMEOUT = 90  # seconds; the first start after install is slow
MIN_PYTHON = (3, 12)
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
APP_ID = "local.vendorus.app"


def say(message: str) -> None:
    line = f"[Vendorus] {message}"
    try:
        print(line, flush=True)
    except (OSError, ValueError, AttributeError):
        pass  # no console (pythonw on Windows, or the Mac app)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as log:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


# --- environment -------------------------------------------------------------

def venv_python(root: Path = ROOT, windowed: bool = False) -> Path:
    if IS_WINDOWS:
        return root / ".venv" / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
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
    say("First run: creating the Python environment (a few minutes)...")
    venv.EnvBuilder(with_pip=True).create(root / ".venv")
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "-r", str(root / "requirements.txt")], check=True
    )
    say("Environment ready.")
    return python


def use_project_packages(root: Path = ROOT) -> None:
    """Make .venv's packages importable when this file is run by a Python
    outside the venv (the Mac app runs its own copy of Python)."""
    if Path(sys.prefix).resolve() == (root / ".venv").resolve():
        return
    pattern = "Lib/site-packages" if IS_WINDOWS else "lib/python*/site-packages"
    for path in glob.glob(str(root / ".venv" / pattern)):
        site.addsitedir(path)


# --- running instance --------------------------------------------------------

def read_state(path: Path = STATE_FILE) -> dict | None:
    try:
        state = json.loads(path.read_text())
        return state if {"pid", "port"} <= set(state) else None
    except (OSError, ValueError):
        return None


def write_state(pid: int, port: int, path: Path = STATE_FILE, owner: int | None = None) -> None:
    """Remember the dashboard. `owner` is the launcher process that stops it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"pid": pid, "port": port}
    if owner is not None:
        state["owner"] = owner
    path.write_text(json.dumps(state))


def clear_state(path: Path = STATE_FILE) -> None:
    path.unlink(missing_ok=True)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
    DATA.mkdir(parents=True, exist_ok=True)
    log = open(LOG_FILE, "a")
    kwargs: dict = {"cwd": ROOT, "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if background:
            flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = flags
    elif background:
        kwargs["start_new_session"] = True  # keep running after the launcher exits
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
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
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


# --- app window ---------------------------------------------------------------

def system_prefers_dark() -> bool:
    try:
        if IS_MAC:
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, text=True, timeout=2).stdout
            return "Dark" in out
        if IS_WINDOWS:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:
        pass
    return False


def splash_html(message: str, detail: str = "", spinner: bool = True) -> str:
    """A small page shown in the window while the dashboard starts (or fails)."""
    spin = '<div class="spin"></div>' if spinner else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
:root {{ color-scheme: light dark; }}
body {{ margin: 0; height: 100vh; display: flex; align-items: center; justify-content: center;
  font-family: -apple-system, "Segoe UI", Inter, sans-serif; background: #F8FAFC; color: #0F172A; }}
@media (prefers-color-scheme: dark) {{ body {{ background: #0B1120; color: #E2E8F0; }} }}
.box {{ text-align: center; max-width: 520px; padding: 0 24px; }}
.logo {{ width: 64px; height: 64px; border-radius: 16px; background: #6366F1; display: inline-flex;
  align-items: center; justify-content: center; color: #fff; font-weight: 700; font-size: 32px; }}
.spin {{ width: 22px; height: 22px; border: 3px solid rgba(99,102,241,.25); border-top-color: #6366F1;
  border-radius: 50%; animation: s .8s linear infinite; margin: 24px auto 4px; }}
@keyframes s {{ to {{ transform: rotate(360deg); }} }}
h1 {{ font-size: 17px; font-weight: 600; margin: 18px 0 6px; }}
p {{ opacity: .65; font-size: 13px; line-height: 1.5; word-break: break-word; }}
</style></head><body><div class="box"><div class="logo">V</div>{spin}
<h1>{message}</h1><p>{detail}</p></div></body></html>"""


def app_icon_png() -> Path | None:
    """The app icon as a PNG (drawn once with Pillow and cached in data/)."""
    target = DATA / "app_icon.png"
    if target.exists():
        return target
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from make_icon import draw

        DATA.mkdir(parents=True, exist_ok=True)
        draw(str(target))
        return target
    except Exception:
        return None


def set_mac_identity() -> None:
    """Show "Vendorus" and its icon in the Dock and menu bar when started
    without the app bundle (e.g. from Vendorus.command)."""
    if not IS_MAC:
        return
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle()
        if bundle.bundleIdentifier() == APP_ID:
            return  # running as Vendorus.app: the bundle already says so
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = "Vendorus"
        icon = app_icon_png()
        if icon:
            image = NSImage.alloc().initWithContentsOfFile_(str(icon))
            NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:
        pass


def run_window(python: Path) -> int:
    """Open Vendorus in its own window. Closing the window stops the
    dashboard, if this launcher started it."""
    use_project_packages()
    try:
        import webview
    except ImportError:
        say("The app window needs pywebview (pip install pywebview); opening your browser instead.")
        return run_browser(python)

    existing = running_instance()
    owned: dict = {"process": None}

    def stop_owned(*_):
        if owned["process"] is not None:
            owned["process"] = None
            stop()

    background = "#0B1120" if system_prefers_dark() else "#F8FAFC"
    window = webview.create_window(
        "Vendorus", html=splash_html("Starting Vendorus…"),
        width=1280, height=860, min_size=(960, 640), background_color=background,
    )

    def boot():
        try:
            if existing and not process_alive(existing.get("owner", 0)):
                # A dashboard left from an earlier session: adopt it.
                port = existing["port"]
                write_state(existing["pid"], port, owner=os.getpid())
                owned["process"] = existing["pid"]
            elif existing:
                port = existing["port"]  # owned by another open launcher; just view it
            else:
                port = find_free_port()
                say(f"Starting on port {port}...")
                process = start_server(python, port, background=False)
                owned["process"] = process.pid
                write_state(process.pid, port, owner=os.getpid())
                if not wait_until_healthy(process, port):
                    stop_owned()
                    window.load_html(splash_html(
                        "Vendorus couldn't start", f"Details are in {LOG_FILE}", spinner=False))
                    return
            window.load_url(f"http://localhost:{port}")
        except Exception as exc:  # show the problem rather than a blank window
            say(f"Start-up failed: {exc}")
            window.load_html(splash_html("Vendorus couldn't start", str(exc), spinner=False))

    window.events.closed += stop_owned
    atexit.register(stop_owned)
    for sig in (signal.SIGTERM,) + (() if IS_WINDOWS else (signal.SIGHUP,)):
        signal.signal(sig, lambda *_: (stop_owned(), os._exit(0)))

    webview.settings["ALLOW_DOWNLOADS"] = True  # template downloads on the Import page
    set_mac_identity()
    icon = app_icon_png()
    webview.start(boot, icon=str(icon) if icon else None)
    stop_owned()
    return 0


def run_browser(python: Path, background: bool = False, open_it: bool = True) -> int:
    existing = running_instance()
    if existing:
        say(f"Already running on port {existing['port']}.")
        if open_it:
            open_browser(existing["port"])
        return 0

    port = find_free_port()
    say(f"Starting on port {port}...")
    process = start_server(python, port, background=background)
    write_state(process.pid, port, owner=None if background else os.getpid())

    if not wait_until_healthy(process, port):
        stop()
        say(f"The dashboard didn't start. Details are in {LOG_FILE}")
        return 1
    if open_it:
        open_browser(port)

    if background:
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


# --- Windows shortcut -----------------------------------------------------------

def install_windows_shortcut(root: Path = ROOT) -> Path | None:
    """Put a "Vendorus" shortcut with the app icon on the Windows desktop."""
    if not IS_WINDOWS:
        return None
    icon = DATA / "vendorus.ico"
    png = app_icon_png()
    if png and not icon.exists():
        from PIL import Image

        Image.open(png).save(icon, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    desktop = Path(os.path.expandvars(r"%USERPROFILE%")) / "Desktop"
    link = desktop / "Vendorus.lnk"
    target = venv_python(root, windowed=True)
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:VD_LINK);"
        "$s.TargetPath = $env:VD_TARGET; $s.Arguments = $env:VD_ARGS;"
        "$s.WorkingDirectory = $env:VD_ROOT; $s.IconLocation = $env:VD_ICON;"
        "$s.Description = 'Vendorus'; $s.Save()"
    )
    env = dict(os.environ, VD_LINK=str(link), VD_TARGET=str(target), VD_ARGS=f'"{root / "launcher.py"}"',
               VD_ROOT=str(root), VD_ICON=str(icon) if icon.exists() else "")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=env, check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return link


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start Vendorus.")
    parser.add_argument("--browser", action="store_true", help="open in your web browser")
    parser.add_argument("--background", action="store_true", help="start and return immediately")
    parser.add_argument("--stop", action="store_true", help="stop the running dashboard")
    parser.add_argument("--no-browser", action="store_true", help="with --background: don't open a browser")
    parser.add_argument("--setup-only", action="store_true", help="create .venv and install requirements")
    parser.add_argument("--install-shortcut", action="store_true", help="Windows: add a desktop shortcut")
    args = parser.parse_args(argv)

    if args.stop:
        say("Stopped." if stop() else "Not running.")
        return 0

    python = ensure_environment()
    if args.setup_only:
        return 0
    if args.install_shortcut:
        link = install_windows_shortcut()
        say(f"Shortcut created: {link}" if link else "Shortcuts are for Windows; on a Mac run scripts/make_mac_app.sh.")
        return 0
    if args.background or args.browser:
        return run_browser(python, background=args.background, open_it=not args.no_browser)
    return run_window(python)


if __name__ == "__main__":
    sys.exit(main())
