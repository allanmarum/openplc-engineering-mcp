"""Real-process coverage for compiler descendant cleanup.

These tests spawn actual CLI processes with actual descendants; mocked
``subprocess`` tests cannot detect an orphaned process tree.
"""

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import wait_for_process_exit
from mcp.server.mcpserver.exceptions import ToolError

from openplc_engineering_mcp.openplc import compiler
from openplc_engineering_mcp.openplc.compiler import compile_project, get_diagnostics


def make_project(root: Path) -> Path:
    root.mkdir()
    (root / "project.json").write_text(
        json.dumps({"meta": {"name": "Example", "type": "plc-project"}}), encoding="utf-8"
    )
    return root


def resolve_cli(monkeypatch, cli: SimpleNamespace) -> None:
    monkeypatch.setattr(
        compiler.shutil, "which", lambda name: str(cli.path) if name == "openplc-cli" else None
    )


def reported_pids(pids_file: Path) -> list[int]:
    """Wait for the fake CLI to report its own and its descendant's process ids."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if pids_file.exists():
            return [int(line) for line in pids_file.read_text(encoding="utf-8").split() if line]
        time.sleep(0.1)
    raise AssertionError("fake openplc-cli never reported its process ids")


class SimulatedCancellation(BaseException):
    """Models an asyncio cancellation arriving while the CLI wait is in progress."""


def test_timeout_terminates_the_whole_process_tree(tmp_path, monkeypatch, fake_openplc_cli) -> None:
    project = make_project(tmp_path / "project")
    resolve_cli(monkeypatch, fake_openplc_cli)
    monkeypatch.setenv("OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS", "2")
    compiler._LAST_DIAGNOSTICS[str(project.resolve())] = ["main.st:1: error: stale"]

    with pytest.raises(ToolError, match="timed out after 2 seconds"):
        compile_project(str(project))

    wait_for_process_exit(reported_pids(fake_openplc_cli.pids_file))
    with pytest.raises(ToolError, match="No compilation diagnostics are available"):
        get_diagnostics(str(project))


def test_abandoned_wait_terminates_the_whole_process_tree(
    tmp_path, monkeypatch, fake_openplc_cli
) -> None:
    project = make_project(tmp_path / "project")
    resolve_cli(monkeypatch, fake_openplc_cli)
    interrupted = False

    class InterruptiblePopen(subprocess.Popen):
        """A real process whose first wait is abandoned like an async cancellation.

        The wait is only abandoned once the fake CLI tree is provably running,
        otherwise the cleanup could race ahead of the descendant spawn.
        """

        def communicate(self, input=None, timeout=None):  # noqa: A002 - matches Popen signature
            nonlocal interrupted
            if not interrupted:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not fake_openplc_cli.pids_file.exists():
                    time.sleep(0.05)
                interrupted = True
                raise SimulatedCancellation
            return super().communicate(input, timeout=timeout)

    monkeypatch.setattr(compiler.subprocess, "Popen", InterruptiblePopen)

    with pytest.raises(SimulatedCancellation):
        compile_project(str(project))

    wait_for_process_exit(reported_pids(fake_openplc_cli.pids_file))
