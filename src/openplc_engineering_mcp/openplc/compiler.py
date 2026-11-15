"""OpenPLC CLI compilation and process-local diagnostics."""

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
from pathlib import Path
from typing import TypedDict

import anyio
import anyio.to_thread
from mcp.server.mcpserver.exceptions import ToolError

from openplc_engineering_mcp.openplc.project import load_project

_COMPILE_TIMEOUT_SECONDS = 300
_TIMEOUT_ENV_VAR = "OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS"
_REAP_GRACE_SECONDS = 10


class CompileResult(TypedDict):
    success: bool
    exit_code: int
    output: object | None


_CHROMIUM_LOG_LINE = re.compile(r"^\[\d+(?::\d+)?:\d+/\d+\.\d+:([A-Z]+):([^(\]]+)\(\d+\)]")

_BENIGN_CHROMIUM_SOURCES = frozenset({"bus.cc", "object_proxy.cc"})


def _is_platform_noise(line: str) -> bool:
    """Known-benign Electron noise reaches stderr without being compile diagnostics."""
    if line.startswith("File already exists at ") or line == "Skipping creation.":
        return True
    match = _CHROMIUM_LOG_LINE.match(line)
    if match is None:
        return False
    level, source = match.group(1), match.group(2)
    return level != "FATAL" and source in _BENIGN_CHROMIUM_SOURCES


_LAST_DIAGNOSTICS: dict[str, list[str]] = {}

_PROJECT_LOCKS: dict[str, threading.Lock] = {}
_PROJECT_LOCKS_GUARD = threading.Lock()


def project_operation_lock(root: Path) -> threading.Lock:
    """Return the lock serializing compilation and POU updates for one project.

    Concurrent tools run on worker threads, so without serialization a second
    compilation or an ``update_pou`` could interleave with a running compile and
    make diagnostics ownership nondeterministic. Keying by resolved project path
    keeps unrelated projects independent; read-only tools never take the lock.
    """
    with _PROJECT_LOCKS_GUARD:
        lock = _PROJECT_LOCKS.get(str(root))
        if lock is None:
            lock = threading.Lock()
            _PROJECT_LOCKS[str(root)] = lock
        return lock


def _compile_timeout() -> int:
    """Return the compile timeout in seconds, honoring the environment override."""
    raw = os.environ.get(_TIMEOUT_ENV_VAR)
    if raw is None:
        return _COMPILE_TIMEOUT_SECONDS
    try:
        timeout = int(raw)
    except ValueError:
        timeout = 0
    if timeout <= 0:
        raise ToolError(f'Invalid {_TIMEOUT_ENV_VAR} value: "{raw}"')
    return timeout


def _resolve_cli() -> str:
    # Resolve through PATH extensions so Windows `.cmd` shims are executable;
    # CreateProcess does not apply PATHEXT resolution to a bare program name.
    executable = shutil.which("openplc-cli")
    if executable is None:
        raise ToolError("openplc-cli was not found on PATH")
    return executable


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Best-effort termination of the compiler process and all its descendants.

    ``Popen.kill()`` reaches only the direct child, so a hanging CLI tree needs a
    group kill on POSIX (the child was started in its own session, so its pid is
    the process-group id even after the child itself has exited and left
    descendants behind) and ``taskkill /T`` on Windows. Killing an already-dead
    target is a suppressed no-op, so this stays safe to call from both the worker
    thread and the cancellation path.
    """
    with contextlib.suppress(OSError):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=_REAP_GRACE_SECONDS,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)


def _reap_terminated(process: subprocess.Popen) -> None:
    """Bound cleanup after termination: reap the direct child without waiting on pipes.

    ``communicate()`` would wait for pipe EOF, which a surviving descendant that
    inherited the CLI's stdout/stderr can delay indefinitely. ``wait()`` depends
    only on the direct child. The pipe handles are released once the terminated
    tree's EOF unblocks communicate's internal reader threads; closing the text
    streams here instead would block behind those very threads.
    """
    with contextlib.suppress(Exception):
        process.wait(timeout=_REAP_GRACE_SECONDS)
    with contextlib.suppress(Exception):
        process.kill()
    with contextlib.suppress(Exception):
        process.wait(timeout=_REAP_GRACE_SECONDS)


class _CompileProcessHandle:
    """Bridges the async cancellation path and the worker thread running the CLI.

    The abandoned worker thread cannot be interrupted, so cancellation must
    terminate the process tree from the async side. The lock makes "register"
    (worker thread) and "abandon" (cancellation) race-free: whichever runs second
    observes the other's state, so the tree is terminated exactly once.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._abandoned = False

    def register(self, process: subprocess.Popen) -> bool:
        """Adopt a freshly spawned CLI process; return whether it is already abandoned."""
        with self._lock:
            self._process = process
            return self._abandoned

    @property
    def abandoned(self) -> bool:
        with self._lock:
            return self._abandoned

    def abandon(self) -> None:
        """Mark the run abandoned and terminate whatever process tree exists."""
        with self._lock:
            self._abandoned = True
            process = self._process
        if process is not None:
            _terminate_process_tree(process)


def _run_cli(
    executable: str, root: Path, timeout: int, handle: _CompileProcessHandle | None = None
) -> tuple[int, str, str]:
    """Run one bounded CLI compilation, leaving no owned descendants alive."""
    command = [executable, "compile", str(root), "--json"]
    try:
        if os.name == "nt":
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
        else:
            # A dedicated session makes the CLI process group killable as a whole.
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
    except FileNotFoundError as exc:
        raise ToolError("openplc-cli was not found on PATH") from exc
    except OSError as exc:
        raise ToolError(f"Could not run openplc-cli: {exc}") from exc

    if handle is not None and handle.register(process):
        # Cancellation arrived before the worker thread could wait on the process.
        _terminate_process_tree(process)
        _reap_terminated(process)
        raise ToolError("openplc-cli compilation was interrupted before it could run")
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        _reap_terminated(process)
        raise ToolError(f"openplc-cli compilation timed out after {timeout} seconds") from exc
    except BaseException:
        # Cancellation or abandonment of the wait must not orphan the CLI tree.
        _terminate_process_tree(process)
        _reap_terminated(process)
        raise
    if handle is not None and handle.abandoned:
        # The wait was abandoned while it ran; the caller already stopped waiting,
        # so clean up and report nothing that could be mistaken for a result.
        _terminate_process_tree(process)
        _reap_terminated(process)
        raise ToolError("openplc-cli compilation was interrupted")
    return process.returncode, stdout, stderr


def _record_result(root: Path, returncode: int, stdout: str, stderr: str) -> CompileResult:
    _LAST_DIAGNOSTICS[str(root)] = [
        stripped
        for line in stderr.splitlines()
        if (stripped := line.strip()) and not _is_platform_noise(stripped)
    ]

    output: object | None = None
    if stdout.strip():
        try:
            output = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ToolError("openplc-cli returned invalid JSON") from exc

    return {
        "success": returncode == 0,
        "exit_code": returncode,
        "output": output,
    }


def compile_project(project_path: str) -> CompileResult:
    """Compile an OpenPLC project and cache stderr diagnostics for this process."""
    root, _, _ = load_project(project_path)
    with project_operation_lock(root):
        try:
            executable = _resolve_cli()
            timeout = _compile_timeout()
            returncode, stdout, stderr = _run_cli(executable, root, timeout)
        except BaseException:
            # A compilation attempt that produced no new result invalidates the
            # previous one: stored diagnostics must never describe an earlier attempt.
            _LAST_DIAGNOSTICS.pop(str(root), None)
            raise
        return _record_result(root, returncode, stdout, stderr)


async def compile_project_async(project_path: str) -> CompileResult:
    """Compile an OpenPLC project, terminating the CLI tree promptly on interruption.

    The official SDK executes tools in worker threads it will not interrupt, so a
    synchronous tool would keep compiling until the timeout even after the client
    cancels the request or disconnects. This entry point runs the same bounded
    CLI call but abandons the worker thread and terminates the process tree as
    soon as the await is cancelled.
    """
    root, _, _ = load_project(project_path)
    lock = project_operation_lock(root)
    lock_held: list[bool] = []

    def acquire_lock() -> None:
        lock.acquire()
        lock_held.append(True)

    try:
        # A non-abandonable acquire always lets the worker thread finish, so the
        # flag above is set before cancellation can surface at the await. This
        # cannot leak the lock even when the wait for a long compile is cancelled.
        await anyio.to_thread.run_sync(acquire_lock)
        handle = _CompileProcessHandle()
        try:
            executable = _resolve_cli()
            timeout = _compile_timeout()
            returncode, stdout, stderr = await anyio.to_thread.run_sync(
                lambda: _run_cli(executable, root, timeout, handle), abandon_on_cancel=True
            )
        except BaseException:
            # Cancellation, disconnection, or any other interruption: stop the
            # CLI tree now instead of waiting out the compile timeout, and drop
            # stale diagnostics like any other attempt without a new result.
            handle.abandon()
            _LAST_DIAGNOSTICS.pop(str(root), None)
            raise
        return _record_result(root, returncode, stdout, stderr)
    finally:
        if lock_held:
            lock.release()


def get_diagnostics(project_path: str) -> list[str]:
    """Return diagnostics from the project's most recent completed compilation."""
    root, _, _ = load_project(project_path)
    try:
        return _LAST_DIAGNOSTICS[str(root)]
    except KeyError as exc:
        raise ToolError("No compilation diagnostics are available for this project") from exc


def invalidate_diagnostics(project_path: str) -> None:
    """Drop cached diagnostics because the project's compiled sources changed."""
    root, _, _ = load_project(project_path)
    _LAST_DIAGNOSTICS.pop(str(root), None)
