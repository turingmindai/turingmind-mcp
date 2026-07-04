"""macOS launchd installer for the V2 API server (port 8477)."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.error import URLError
from urllib.request import urlopen

LABEL = "com.turingmind.api"
ENV_FILE = Path.home() / ".turingmind" / "env"
LOG_DIR = Path.home() / ".turingmind"


def repo_root() -> Path:
    """Return the turingmind-mcp repository/package root."""
    return Path(__file__).resolve().parent.parent.parent


def load_env_file(path: Path = ENV_FILE) -> Dict[str, str]:
    """Parse KEY=VALUE lines from a dotenv-style file."""
    if not path.is_file():
        return {}
    result: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def default_env(repo_dir: Path, port: int) -> Dict[str, str]:
    """Built-in launchd environment; ~/.turingmind/env overrides except venv python."""
    python = repo_dir / ".venv" / "bin" / "python3"
    file_env = load_env_file()
    env: Dict[str, str] = {
        "TURINGMIND_PYTHON": str(python),
        "TURINGMIND_API_PORT": str(port),
        "TURINGMIND_LOCAL_API_URL": f"http://127.0.0.1:{port}",
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    }
    env.update(file_env)
    env["TURINGMIND_PYTHON"] = str(python)
    if "TURINGMIND_API_PORT" not in file_env:
        env["TURINGMIND_API_PORT"] = str(port)
    return env


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def wrapper_script(repo_dir: Path) -> Path:
    return repo_dir / "scripts" / "run-api-server.sh"


def write_plist(repo_dir: Path, port: int = 8477) -> Path:
    """Write LaunchAgent plist with merged environment."""
    wrapper = wrapper_script(repo_dir)
    if not wrapper.is_file():
        raise FileNotFoundError(f"Missing wrapper script: {wrapper}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    plist_path().parent.mkdir(parents=True, exist_ok=True)

    data = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(wrapper)],
        "EnvironmentVariables": default_env(repo_dir, port),
        "WorkingDirectory": str(repo_dir),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "StandardOutPath": str(LOG_DIR / "api-server.out.log"),
        "StandardErrorPath": str(LOG_DIR / "api-server.err.log"),
    }
    with plist_path().open("wb") as handle:
        plistlib.dump(data, handle)
    return plist_path()


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _service() -> str:
    return f"{_domain()}/{LABEL}"


def _run_launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
    )


def unload() -> None:
    _run_launchctl("bootout", _service())
    _run_launchctl("remove", LABEL)


def load_plist(path: Path) -> None:
    unload()
    time.sleep(1)
    result = _run_launchctl("bootstrap", _domain(), str(path))
    if result.returncode != 0:
        time.sleep(2)
        unload()
        result = _run_launchctl("bootstrap", _domain(), str(path))
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"launchctl bootstrap failed: {msg}")
    _run_launchctl("enable", _service())
    _run_launchctl("kickstart", "-k", _service())


def health_ok(port: int = 8477, timeout: float = 2.0) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/v2/health", timeout=timeout) as resp:
            return resp.status == 200
    except (URLError, OSError, TimeoutError):
        return False


def launchd_loaded() -> bool:
    result = subprocess.run(
        ["launchctl", "list"],
        capture_output=True,
        text=True,
    )
    return LABEL in (result.stdout or "")


def ensure_venv(repo_dir: Path) -> Path:
    """Create venv and editable install if missing."""
    python = repo_dir / ".venv" / "bin" / "python3"
    if python.is_file():
        return python
    subprocess.run([sys.executable, "-m", "venv", str(repo_dir / ".venv")], check=True)
    subprocess.run(
        [str(python), "-m", "pip", "install", "-e", str(repo_dir)],
        check=True,
    )
    return python


def install(repo_dir: Optional[Path] = None, port: int = 8477, wait_seconds: int = 15) -> int:
    """Install and start the launchd agent. Returns 0 on success."""
    root = repo_dir or repo_root()
    wrapper = wrapper_script(root)
    if not wrapper.is_file():
        print(f"❌ Not found: {wrapper}", file=sys.stderr)
        print("   Run from a turingmind-mcp checkout or pip install -e .", file=sys.stderr)
        return 1

    os.chmod(wrapper, os.stat(wrapper).st_mode | 0o111)
    ensure_venv(root)
    python = root / ".venv" / "bin" / "python3"
    if not python.is_file():
        print(f"❌ venv python missing: {python}", file=sys.stderr)
        return 1

    try:
        path = write_plist(root, port=port)
        load_plist(path)
    except Exception as exc:
        print(f"❌ launchd install failed: {exc}", file=sys.stderr)
        return 1

    print(f"✅ Installed {LABEL} on port {port}")
    if ENV_FILE.is_file():
        print(f"   Loaded env from {ENV_FILE}")
    print(f"   Logs: {LOG_DIR}/api-server.out.log")

    for _ in range(wait_seconds):
        if health_ok(port):
            print(f"✅ API server healthy on {port}")
            return 0
        time.sleep(1)

    print("⚠️  Server not responding yet — check api-server.err.log", file=sys.stderr)
    return 1


def uninstall() -> int:
    unload()
    path = plist_path()
    if path.is_file():
        path.unlink()
    print(f"✅ Uninstalled {LABEL}")
    return 0


def status(port: int = 8477) -> int:
    if launchd_loaded():
        print(f"✅ launchd: {LABEL} loaded")
    else:
        print(f"⚪ launchd: {LABEL} not loaded")
    if health_ok(port):
        print(f"✅ API: http://127.0.0.1:{port}/api/v2/health")
        return 0
    print(f"❌ API: not responding on port {port}")
    return 1


def diagnose_daemon(port: int = 8477) -> None:
    """Print daemon health lines for `turingmind diagnose`."""
    python = repo_root() / ".venv" / "bin" / "python3"
    if python.is_file():
        try:
            subprocess.run(
                [str(python), "-m", "uvicorn", "--help"],
                capture_output=True,
                timeout=5,
                check=True,
            )
            print("✅ venv uvicorn available")
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            print("❌ venv missing uvicorn — run: .venv/bin/python -m pip install -e .")
    else:
        print("⚪ venv not found (optional for PyPI-only installs)")

    if launchd_loaded():
        print(f"✅ launchd agent {LABEL} loaded")
    else:
        print(f"⚪ launchd agent {LABEL} not loaded")
        print("   Install: turingmind install-api-daemon")

    if health_ok(port):
        print(f"✅ V2 API server responding on :{port}")
    else:
        print(f"❌ V2 API server not responding on :{port}")
        print("   Plugin hooks need http://127.0.0.1:8477")


def offer_cursor_daemon_install(repo_dir: Optional[Path] = None) -> None:
    """Prompt to install launchd when setting up Cursor."""
    root = repo_dir or repo_root()
    if not wrapper_script(root).is_file():
        print("\n⚠️  V2 API daemon installer not found in this install.")
        print("   From a git checkout run: bash scripts/install-launchd.sh")
        return
    if health_ok():
        print("\n✅ V2 API server already running on :8477")
        return
    print("\n🔧 Cursor plugin hooks require the V2 API server on http://127.0.0.1:8477")
    try:
        response = input("Install background API server (launchd, survives reboot)? [Y/n]: ")
    except EOFError:
        response = "n"
    if response.strip().lower() in ("n", "no"):
        print("Skipped — start manually: python -m turingmind_mcp.api_server")
        return
    code = install(root)
    if code != 0:
        print("   Try: bash scripts/install-launchd.sh")


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="TuringMind V2 API launchd daemon")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("install", help="Install and start launchd agent")
    sub.add_parser("uninstall", help="Remove launchd agent")
    sub.add_parser("status", help="Check launchd and API health")
    args = parser.parse_args(argv)
    if args.cmd == "install":
        return install()
    if args.cmd == "uninstall":
        return uninstall()
    if args.cmd == "status":
        return status()
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
