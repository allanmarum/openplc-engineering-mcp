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
    return _install_fake_cli(monkeypatch, tmp_path, "cli-bin", FAKE_CLI_IMPL)


# A launcher fake openplc-cli: it spawns a long-lived descendant that inherits its
# stdout/stderr pipes and exits immediately, so the pipes stay open (blocking a
# naive communicate) while the process-tree root is already gone.
LAUNCHER_CLI_IMPL = """import os
import subprocess
import sys

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(120)"],
    stdin=subprocess.DEVNULL,
    close_fds=False,
)
with open(os.environ["FAKE_OPENPLC_CLI_PIDS"], "a", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()}\\n{child.pid}\\n")
"""


@pytest.fixture
def fake_openplc_cli_launcher(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """Install a fake openplc-cli that exits and leaves a pipe-holding descendant."""
    return _install_fake_cli(monkeypatch, tmp_path, "launcher-cli-bin", LAUNCHER_CLI_IMPL)


# A gated fake openplc-cli: every invocation records its process id, blocks until
# the test creates the release file, then completes successfully with one stderr
# diagnostic line, so one run can be held in flight and released on demand.
GATED_CLI_IMPL = """import json
import os
import sys
import time

with open(os.environ["FAKE_OPENPLC_CLI_INVOCATIONS"], "a", encoding="utf-8") as handle:
    handle.write(f"{os.getpid()}\\n")
release = os.environ["FAKE_OPENPLC_CLI_RELEASE"]
deadline = time.monotonic() + 30
while time.monotonic() < deadline and not os.path.exists(release):
    time.sleep(0.05)
sys.stderr.write("main.st:1: error: gated compile diagnostic\\n")
print(json.dumps({"ok": True}))
"""


@pytest.fixture
def gated_openplc_cli(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """Install a fake openplc-cli that completes only after the test releases it."""
    bin_dir = tmp_path / "gated-cli-bin"
    bin_dir.mkdir()
    invocations_file = tmp_path / "gated-cli-invocations.txt"
    release_file = tmp_path / "gated-cli-release.txt"
    if os.name == "nt":
        impl = bin_dir / "cli_impl.py"
        impl.write_text(GATED_CLI_IMPL, encoding="utf-8")
        executable = bin_dir / "openplc-cli.cmd"
        executable.write_text(f'@"{sys.executable}" "{impl}" %*\n', encoding="utf-8")
    else:
        executable = bin_dir / "openplc-cli"
        executable.write_text(f"#!{sys.executable}\n" + GATED_CLI_IMPL, encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setenv("FAKE_OPENPLC_CLI_INVOCATIONS", str(invocations_file))
    monkeypatch.setenv("FAKE_OPENPLC_CLI_RELEASE", str(release_file))
    return SimpleNamespace(
        path=executable,
        bin_dir=bin_dir,
        invocations_file=invocations_file,
        release_file=release_file,
    )


def wait_for_cli_invocations(invocations_file: Path, count: int = 1, timeout: float = 15) -> list[int]:
    """Wait until the gated fake CLI reports the expected number of invocations."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if invocations_file.exists():
            pids = [int(line) for line in invocations_file.read_text(encoding="utf-8").split() if line]
            if len(pids) >= count:
                return pids
        time.sleep(0.05)
    raise AssertionError(f"fake openplc-cli never reached {count} invocation(s)")


def _install_fake_cli(
    monkeypatch, tmp_path: Path, bin_dir_name: str, impl_source: str
) -> SimpleNamespace:
    bin_dir = tmp_path / bin_dir_name
    bin_dir.mkdir()
    pids_file = tmp_path / f"{bin_dir_name}-pids.txt"
    if os.name == "nt":
        impl = bin_dir / "cli_impl.py"
        impl.write_text(impl_source, encoding="utf-8")
        executable = bin_dir / "openplc-cli.cmd"
        executable.write_text(f'@"{sys.executable}" "{impl}" %*\n', encoding="utf-8")
    else:
        executable = bin_dir / "openplc-cli"
        executable.write_text(f"#!{sys.executable}\n" + impl_source, encoding="utf-8")
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
