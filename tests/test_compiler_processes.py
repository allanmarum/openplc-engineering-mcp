"""Real-process coverage for compiler descendant cleanup.

These tests spawn actual CLI processes with actual descendants; mocked
``subprocess`` tests cannot detect an orphaned process tree.
"""

import contextlib
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import anyio
import anyio.to_thread
import pytest
from conftest import wait_for_process_exit
from mcp.server.mcpserver.exceptions import ToolError

from openplc_engineering_mcp.openplc import compiler
from openplc_engineering_mcp.openplc.compiler import CompileResult, compile_project, get_diagnostics


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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


def test_timeout_kills_descendants_of_an_exited_launcher(
    tmp_path, monkeypatch, fake_openplc_cli_launcher
) -> None:
    """A launcher CLI that exits while its descendant holds the pipes open.

    The direct child is gone, so killing only the live child would miss the
    descendant entirely, and an unbounded ``communicate()`` would block on the
    held pipes until the descendant exits by itself.
    """
    project = make_project(tmp_path / "project")
    resolve_cli(monkeypatch, fake_openplc_cli_launcher)
    monkeypatch.setenv("OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS", "2")

    with pytest.raises(ToolError, match="timed out after 2 seconds"):
        compile_project(str(project))

    pids = reported_pids(fake_openplc_cli_launcher.pids_file)
    if os.name == "nt":
        # Windows has no group kill that reaches descendants of an already-exited
        # root; cleanup stays bounded there, but descendant termination is a
        # documented practical limit of the unsupported platform.
        pids = pids[:1]
    wait_for_process_exit(pids)


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


@pytest.mark.anyio
async def test_cancellation_terminates_the_cli_tree_and_drops_diagnostics(
    tmp_path, monkeypatch, fake_openplc_cli
) -> None:
    """The async entry point stops a real CLI tree promptly on cancellation.

    The timeout is 120 seconds, so every assertion below can only pass through
    the cancellation path, never by waiting out the compile timeout.
    """
    project = make_project(tmp_path / "project")
    resolve_cli(monkeypatch, fake_openplc_cli)
    monkeypatch.setenv("OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS", "120")
    compiler._LAST_DIAGNOSTICS[str(project.resolve())] = ["main.st:1: error: stale"]

    completed: list[CompileResult] = []

    async def compile_task() -> None:
        with contextlib.suppress(anyio.get_cancelled_exc_class()):
            completed.append(await compiler.compile_project_async(str(project)))

    pids: list[int] = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(compile_task)
        pids = await anyio.to_thread.run_sync(lambda: reported_pids(fake_openplc_cli.pids_file))
        tg.cancel_scope.cancel()

    assert completed == []
    wait_for_process_exit(pids)
    with pytest.raises(ToolError, match="No compilation diagnostics are available"):
        get_diagnostics(str(project))
