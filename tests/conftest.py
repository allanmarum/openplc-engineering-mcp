"""Shared test fixtures and markers."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def _symlinks_supported() -> bool:
    """Windows only allows os.symlink with admin rights or developer mode enabled."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.touch()
            (Path(tmp) / "link").symlink_to(target)
    except OSError:
        return False
    return True


requires_symlinks = pytest.mark.skipif(
    not _symlinks_supported(), reason="creating symlinks requires elevation on this platform"
)

# A fake openplc-cli: it spawns a long-lived descendant, records both process ids
# for assertions, and then hangs like a stuck compiler.
FAKE_CLI_IMPL = """import os
import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
with open(os.environ["FAKE_OPENPLC_CLI_PIDS"], "a", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()}\\n{child.pid}\\n")
time.sleep(120)
"""


@pytest.fixture
def fake_openplc_cli(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """Install a hanging fake openplc-cli that reports its whole process tree."""
    bin_dir = tmp_path / "cli-bin"
    bin_dir.mkdir()
    pids_file = tmp_path / "fake-cli-pids.txt"
    if os.name == "nt":
        impl = bin_dir / "cli_impl.py"
        impl.write_text(FAKE_CLI_IMPL, encoding="utf-8")
        executable = bin_dir / "openplc-cli.cmd"
        executable.write_text(f'@"{sys.executable}" "{impl}" %*\n', encoding="utf-8")
    else:
        executable = bin_dir / "openplc-cli"
        executable.write_text(f"#!{sys.executable}\n" + FAKE_CLI_IMPL, encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setenv("FAKE_OPENPLC_CLI_PIDS", str(pids_file))
    return SimpleNamespace(path=executable, bin_dir=bin_dir, pids_file=pids_file)


def pid_alive(pid: int) -> bool:
    """Return whether a process id still exists on this platform."""
    if os.name == "nt":
        listing = subprocess.run(
            ["tasklist", "/NH", "/FI", f"PID eq {pid}"], capture_output=True, text=True
        ).stdout
        return any(line.split()[1:2] == [str(pid)] for line in listing.splitlines() if line.split())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_process_exit(pids: list[int], timeout: float = 30.0) -> None:
    """Assert that every process id exits within the timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(pid_alive(pid) for pid in pids):
            return
        time.sleep(0.25)
    alive = [pid for pid in pids if pid_alive(pid)]
    raise AssertionError(f"processes did not exit: {alive}")
