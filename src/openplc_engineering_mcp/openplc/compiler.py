"""OpenPLC CLI compilation and process-local diagnostics."""

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import TypedDict

from mcp.server.mcpserver.exceptions import ToolError

from openplc_engineering_mcp.openplc.project import load_project

_COMPILE_TIMEOUT_SECONDS = 300
_TIMEOUT_ENV_VAR = "OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS"


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


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Best-effort termination of the compiler process and all its descendants.

    ``Popen.kill()`` reaches only the direct child, so a hanging CLI tree needs a
    group kill on POSIX (the child was started in its own session) and ``taskkill /T``
    on Windows.
    """
    if process.poll() is not None:
        return
    with contextlib.suppress(OSError):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)


def _run_cli(executable: str, root: Path, timeout: int) -> tuple[int, str, str]:
    """Run one bounded CLI compilation, leaving no descendants alive on any exit path."""
    popen_kwargs: dict[str, bool] = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(
            [executable, "compile", str(root), "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **popen_kwargs,
        )
    except FileNotFoundError as exc:
        raise ToolError("openplc-cli was not found on PATH") from exc
    except OSError as exc:
        raise ToolError(f"Could not run openplc-cli: {exc}") from exc

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        with contextlib.suppress(Exception):
            process.communicate()
        raise ToolError(f"openplc-cli compilation timed out after {timeout} seconds") from exc
    except BaseException:
        # Cancellation or abandonment of the wait must not orphan the CLI tree.
        _terminate_process_tree(process)
        with contextlib.suppress(Exception):
            process.communicate()
        raise
    return process.returncode, stdout, stderr


def compile_project(project_path: str) -> CompileResult:
    """Compile an OpenPLC project and cache stderr diagnostics for this process."""
    root, _, _ = load_project(project_path)

    # Resolve through PATH extensions so Windows `.cmd` shims are executable;
    # CreateProcess does not apply PATHEXT resolution to a bare program name.
    executable = shutil.which("openplc-cli")
    try:
        if executable is None:
            raise ToolError("openplc-cli was not found on PATH")
        returncode, stdout, stderr = _run_cli(executable, root, _compile_timeout())
    except BaseException:
        # A compilation attempt that produced no new result invalidates the
        # previous one: stored diagnostics must never describe an earlier attempt.
        _LAST_DIAGNOSTICS.pop(str(root), None)
        raise

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
