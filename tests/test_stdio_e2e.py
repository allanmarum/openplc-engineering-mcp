"""Exercise the deployable MCP boundary without an OpenPLC CLI installation."""

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import anyio
import pytest
from conftest import wait_for_cli_invocations, wait_for_process_exit
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_stdio_project_round_trip(tmp_path: Path) -> None:
    (tmp_path / "project.json").write_text(
        json.dumps({"meta": {"name": "Stdio project", "type": "plc-project"}}), encoding="utf-8"
    )
    source = "PROGRAM MAIN\nEND_PROGRAM\n"
    pou = tmp_path / "pous" / "programs" / "MAIN.st"
    pou.parent.mkdir(parents=True)
    pou.write_bytes(source.encode("utf-8"))
    replacement = "PROGRAM MAIN\n    (* Updated through stdio: ação *)\nEND_PROGRAM\n"
    arguments = {"project_path": str(tmp_path), "pou_name": "MAIN"}
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "openplc_engineering_mcp.server"],
        cwd=tmp_path,
    )

    # Bound protocol waits; the SDK owns and reaps the child even on failure/cancellation.
    with anyio.fail_after(30):
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert {"validate_project", "read_pou", "update_pou"} <= {
                    tool.name for tool in listed.tools
                }

                validated = await session.call_tool("validate_project", {"project_path": str(tmp_path)})
                assert not validated.is_error
                assert validated.structured_content == {
                    "valid": True,
                    "name": "Stdio project",
                    "type": "plc-project",
                    "warnings": [],
                }

                original = await session.call_tool("read_pou", arguments)
                assert not original.is_error
                assert original.structured_content is not None
                assert original.structured_content["name"] == "MAIN"
                assert original.structured_content["content"] == source
                assert original.structured_content["content_hash"] == (
                    "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()
                )

                updated = await session.call_tool(
                    "update_pou",
                    {
                        **arguments,
                        "content": replacement,
                        "expected_content_hash": original.structured_content["content_hash"],
                    },
                )
                assert not updated.is_error
                assert updated.structured_content is not None
                new_hash = updated.structured_content["content_hash"]
                assert new_hash != original.structured_content["content_hash"]
                assert new_hash == "sha256:" + hashlib.sha256(replacement.encode("utf-8")).hexdigest()

                reread = await session.call_tool("read_pou", arguments)
                assert not reread.is_error
                assert reread.structured_content is not None
                assert reread.structured_content["content"] == replacement
                assert reread.structured_content["content_hash"] == new_hash

    # Reaching here also requires both SDK contexts to finish their shutdown paths.
    assert pou.read_bytes() == replacement.encode("utf-8")


def _fake_cli_pids(pids_file: Path) -> list[int]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if pids_file.exists():
            return [int(line) for line in pids_file.read_text(encoding="utf-8").split() if line]
        time.sleep(0.1)
    raise AssertionError("fake openplc-cli never reported its process ids")


class _RawStdioSession:
    """Drive one server process with raw JSON-RPC lines and a background reader."""

    def __init__(self, command: list[str], env: dict[str, str], cwd: Path) -> None:
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(cwd),
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        stdout = self.process.stdout
        assert stdout is not None
        for line in stdout:
            self.lines.put(line)
        stdout.close()

    def send(self, message: dict) -> None:
        stdin = self.process.stdin
        assert stdin is not None
        stdin.write(json.dumps(message) + "\n")
        stdin.flush()

    def next_message(self, timeout: float) -> dict | None:
        try:
            return json.loads(self.lines.get(timeout=timeout))
        except queue.Empty:
            return None

    def initialize(self) -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "lifecycle-probe", "version": "0"},
                },
            }
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            message = self.next_message(1)
            if message is not None and message.get("id") == 1:
                break
        else:
            raise AssertionError("server never answered initialize")
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close_stdin_and_wait(self, timeout: float) -> float:
        started = time.monotonic()
        assert self.process.stdin is not None
        self.process.stdin.close()
        self.process.wait(timeout=timeout)
        if self.process.stderr is not None:
            self.process.stderr.close()
        return time.monotonic() - started


def _server_env(fake_openplc_cli, timeout_seconds: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(fake_openplc_cli.bin_dir), env.get("PATH", "")])
    env["OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS"] = timeout_seconds
    return env


def test_stdio_cancellation_terminates_in_flight_compilation(
    tmp_path: Path, fake_openplc_cli
) -> None:
    """A real notifications/cancelled stops the compile tree promptly.

    The compile timeout is 120 seconds, so the CLI tree dying (and the session
    staying usable) can only be explained by the cancellation path, never by
    waiting out the timeout. The project path also carries a space and accented
    characters through the whole boundary.
    """
    project = tmp_path / "projeto ação"
    project.mkdir()
    (project / "project.json").write_text(
        json.dumps({"meta": {"name": "Stdio cancel", "type": "plc-project"}}), encoding="utf-8"
    )
    session = _RawStdioSession(
        [sys.executable, "-m", "openplc_engineering_mcp.server"],
        _server_env(fake_openplc_cli, "120"),
        tmp_path,
    )
    try:
        session.initialize()
        session.send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "compile_project", "arguments": {"project_path": str(project)}},
            }
        )
        pids = _fake_cli_pids(fake_openplc_cli.pids_file)

        # Cancel the in-flight request and give the server time to answer wrongly.
        session.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            message = session.next_message(1)
            assert message is None or message.get("id") != 2, "cancelled request was answered"

        # The abandoned CLI tree dies within the interruption bound, not the timeout.
        wait_for_process_exit(pids, timeout=30)

        # The same connection keeps serving unrelated tools afterwards.
        session.send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "validate_project", "arguments": {"project_path": str(project)}},
            }
        )
        recovered = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            recovered = session.next_message(1)
            if recovered is not None and recovered.get("id") == 3:
                break
        assert recovered is not None and recovered.get("id") == 3
        assert not recovered["result"]["isError"]
    finally:
        elapsed = session.close_stdin_and_wait(timeout=30)
        assert elapsed < 30, f"server shutdown took {elapsed:.1f}s"


def test_stdio_disconnect_terminates_in_flight_compilation(
    tmp_path: Path, fake_openplc_cli
) -> None:
    """A client vanishing mid-compilation still gets a bounded, descendant-free shutdown.

    With a 120-second compile timeout, a prompt exit proves the disconnect path
    terminates the compilation instead of waiting for the timeout to fire.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.json").write_text(
        json.dumps({"meta": {"name": "Stdio disconnect", "type": "plc-project"}}), encoding="utf-8"
    )
    session = _RawStdioSession(
        [sys.executable, "-m", "openplc_engineering_mcp.server"],
        _server_env(fake_openplc_cli, "120"),
        project,
    )
    session.initialize()
    session.send(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "compile_project", "arguments": {"project_path": str(project)}},
        }
    )
    pids = _fake_cli_pids(fake_openplc_cli.pids_file)

    # The client disappears while the compilation is still running.
    elapsed = session.close_stdin_and_wait(timeout=45)
    assert elapsed < 45, f"server waited {elapsed:.1f}s after disconnect; timeout is 120s"

    wait_for_process_exit(pids)


def _gated_server_env(gated_openplc_cli) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(gated_openplc_cli.bin_dir), env.get("PATH", "")])
    env["OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS"] = "120"
    return env


def _await_response(session: _RawStdioSession, request_id: int, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = session.next_message(1)
        if message is not None and message.get("id") == request_id:
            return message
    raise AssertionError(f"server never answered request {request_id}")


def _invocation_count(invocations_file: Path) -> int:
    if not invocations_file.exists():
        return 0
    return len([line for line in invocations_file.read_text(encoding="utf-8").split() if line])


def test_stdio_cancelled_lock_wait_never_compiles_and_serialization_holds(
    tmp_path: Path, gated_openplc_cli
) -> None:
    """A notifications/cancelled for a request still waiting for the project lock.

    The first compilation holds the project lock inside the gated CLI, so the
    second request can only be waiting for it. Cancelling that request must end
    the wait without answering it and without ever spawning a second CLI, while
    the first compilation completes undisturbed and keeps diagnostics ownership.
    """
    project = tmp_path / "projeto"
    project.mkdir()
    (project / "project.json").write_text(
        json.dumps({"meta": {"name": "Stdio wait cancel", "type": "plc-project"}}), encoding="utf-8"
    )
    session = _RawStdioSession(
        [sys.executable, "-m", "openplc_engineering_mcp.server"],
        _gated_server_env(gated_openplc_cli),
        tmp_path,
    )
    try:
        session.initialize()
        session.send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "compile_project", "arguments": {"project_path": str(project)}},
            }
        )
        wait_for_cli_invocations(gated_openplc_cli.invocations_file, count=1)

        # A second compilation of the same project can only be waiting for the lock.
        session.send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "compile_project", "arguments": {"project_path": str(project)}},
            }
        )
        time.sleep(0.5)
        session.send(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 3}}
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            message = session.next_message(1)
            assert message is None or message.get("id") != 3, "cancelled waiting request was answered"

        # The cancelled request never spawned its own CLI; the first compile finishes.
        gated_openplc_cli.release_file.touch()
        finished = _await_response(session, 2)
        assert not finished["result"]["isError"]
        assert finished["result"]["structuredContent"] == {
            "success": True,
            "exit_code": 0,
            "output": {"ok": True},
        }
        assert _invocation_count(gated_openplc_cli.invocations_file) == 1

        session.send(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "get_diagnostics", "arguments": {"project_path": str(project)}},
            }
        )
        diagnostics = _await_response(session, 4)
        assert not diagnostics["result"]["isError"]
        assert diagnostics["result"]["structuredContent"] == {
            "result": ["main.st:1: error: gated compile diagnostic"]
        }
    finally:
        elapsed = session.close_stdin_and_wait(timeout=30)
        assert elapsed < 30, f"server shutdown took {elapsed:.1f}s"

    # Diagnostics are process-local: a restarted server starts with none stored.
    restarted = _RawStdioSession(
        [sys.executable, "-m", "openplc_engineering_mcp.server"],
        _gated_server_env(gated_openplc_cli),
        tmp_path,
    )
    try:
        restarted.initialize()
        restarted.send(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "get_diagnostics", "arguments": {"project_path": str(project)}},
            }
        )
        answer = _await_response(restarted, 9)
        assert answer["result"]["isError"]
    finally:
        restarted.close_stdin_and_wait(timeout=30)
