import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from openplc_engineering_mcp.openplc import compiler
from openplc_engineering_mcp.openplc.compiler import compile_project, get_diagnostics
from openplc_engineering_mcp.openplc.pous import UpdatePouResult, update_pou

FAKE_CLI = "/resolved/openplc-cli"


def make_project(root: Path) -> Path:
    root.mkdir()
    (root / "project.json").write_text(
        json.dumps({"meta": {"name": "Example", "type": "plc-project"}}), encoding="utf-8"
    )
    return root


def stub_process_termination(monkeypatch) -> None:
    """Keep unit tests away from the real kill/reap syscalls.

    The stub processes below carry a fake pid, so real termination helpers must
    never run against them; descendant cleanup is covered by real-process tests.
    """
    monkeypatch.setattr(compiler, "_terminate_process_tree", lambda process: None)
    monkeypatch.setattr(compiler, "_reap_terminated", lambda process: None)


@pytest.fixture
def cli_on_path(monkeypatch) -> str:
    """Resolve openplc-cli to a stable fake path so tests do not depend on the host."""
    monkeypatch.setattr(
        "openplc_engineering_mcp.openplc.compiler.shutil.which",
        lambda name: FAKE_CLI if name == "openplc-cli" else None,
    )
    return FAKE_CLI


def install_cli(
    monkeypatch,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    hang: bool = False,
    spawn_error: OSError | None = None,
) -> tuple[list[list[str]], list[int | None]]:
    """Replace the compiler's Popen with a stub recording argv and wait timeouts."""

    calls: list[list[str]] = []
    timeouts: list[int | None] = []

    class StubProcess:
        def __init__(self, args: list[str], **kwargs) -> None:
            calls.append(args)
            self.args = args
            self.pid = 0
            self.returncode = 0

        def communicate(self, input=None, timeout=None):  # noqa: A002 - matches Popen signature
            timeouts.append(timeout)
            if hang:
                raise subprocess.TimeoutExpired(self.args, timeout if timeout is not None else 0)
            self.returncode = returncode
            return stdout, stderr

        def poll(self):
            return self.returncode

    def stub_popen(args, **kwargs):
        if spawn_error is not None:
            raise spawn_error
        return StubProcess(args, **kwargs)

    monkeypatch.setattr("openplc_engineering_mcp.openplc.compiler.subprocess.Popen", stub_popen)
    stub_process_termination(monkeypatch)
    return calls, timeouts


def compile_success(monkeypatch, project: Path) -> None:
    """Store diagnostics through a successful mocked compilation of one project."""
    install_cli(
        monkeypatch,
        returncode=4,
        stdout="",
        stderr="main.st:1: error: undeclared variable\n",
    )
    compile_project(str(project))


def test_compile_project_and_get_diagnostics(tmp_path: Path, monkeypatch, cli_on_path: str) -> None:
    calls, _ = install_cli(
        monkeypatch,
        returncode=4,
        stdout='{"code":"compile_failed"}',
        stderr="main.st:1: error: undeclared variable\n",
    )

    compiled = compile_project(str(make_project(tmp_path / "project")))

    assert calls == [[cli_on_path, "compile", str((tmp_path / "project").resolve()), "--json"]]
    assert compiled == {
        "success": False,
        "exit_code": 4,
        "output": {"code": "compile_failed"},
    }
    assert get_diagnostics(str(tmp_path / "project")) == ["main.st:1: error: undeclared variable"]


def test_compile_project_diagnostics_exclude_platform_noise(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    project = make_project(tmp_path / "project")
    stderr = "\n".join(
        [
            "[165795:0904/202137.665319:ERROR:bus.cc(408)] Failed to connect to the bus",
            "File already exists at /home/allan/.config/open-plc-editor/User/settings.json.",
            "Skipping creation.",
            "main.st:1: error: undeclared variable",
            "[165795:0904/202137.665399:ERROR:object_proxy.cc(576)] Failed to call method",
        ]
    )
    install_cli(monkeypatch, returncode=4, stdout="", stderr=stderr)

    compiled = compile_project(str(project))

    assert compiled["success"] is False
    assert get_diagnostics(str(project)) == ["main.st:1: error: undeclared variable"]


def test_compile_project_diagnostics_preserve_genuine_platform_failures(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    fatal_line = (
        "[165795:0904/202137.665319:FATAL:setuid_sandbox_host.cc(163)]"
        " The SUID sandbox helper binary was found, but is not configured correctly"
    )
    unknown_source_line = (
        "[165795:0904/202137.665428:ERROR:gpu_init.cc(102)] GPU process isn't usable. Goodbye."
    )
    project = make_project(tmp_path / "project")
    install_cli(
        monkeypatch,
        returncode=4,
        stdout="",
        stderr="\n".join([fatal_line, unknown_source_line, "main.st:1: error: undeclared variable"]),
    )

    compile_project(str(project))

    assert get_diagnostics(str(project)) == [
        fatal_line,
        unknown_source_line,
        "main.st:1: error: undeclared variable",
    ]


def test_compile_project_reports_missing_cli_and_invalidates_diagnostics(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    project = make_project(tmp_path / "project")
    compile_success(monkeypatch, project)
    monkeypatch.setattr("openplc_engineering_mcp.openplc.compiler.shutil.which", lambda name: None)

    with pytest.raises(ToolError, match="openplc-cli was not found on PATH"):
        compile_project(str(project))

    with pytest.raises(ToolError, match="No compilation diagnostics are available"):
        get_diagnostics(str(project))


def test_compile_project_times_out_and_invalidates_diagnostics(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    project = make_project(tmp_path / "project")
    compile_success(monkeypatch, project)
    install_cli(monkeypatch, hang=True)

    with pytest.raises(ToolError, match="timed out after 300 seconds"):
        compile_project(str(project))

    with pytest.raises(ToolError, match="No compilation diagnostics are available"):
        get_diagnostics(str(project))


def test_compile_project_spawn_failure_invalidates_diagnostics(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    project = make_project(tmp_path / "project")
    compile_success(monkeypatch, project)
    install_cli(monkeypatch, spawn_error=PermissionError("simulated spawn failure"))

    with pytest.raises(ToolError, match="Could not run openplc-cli"):
        compile_project(str(project))

    with pytest.raises(ToolError, match="No compilation diagnostics are available"):
        get_diagnostics(str(project))


def test_compile_project_reports_invalid_json_but_keeps_completed_diagnostics(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    project = make_project(tmp_path / "project")
    install_cli(monkeypatch, returncode=4, stdout="not json", stderr="main.st:1: error: boom\n")

    with pytest.raises(ToolError, match="openplc-cli returned invalid JSON"):
        compile_project(str(project))

    assert get_diagnostics(str(project)) == ["main.st:1: error: boom"]


def test_compile_timeout_honors_environment_override(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    project = make_project(tmp_path / "project")
    _, timeouts = install_cli(monkeypatch, hang=True)
    monkeypatch.setenv("OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS", "7")

    with pytest.raises(ToolError, match="timed out after 7 seconds"):
        compile_project(str(project))

    assert timeouts[0] == 7


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5"])
def test_compile_timeout_rejects_invalid_environment_override(
    tmp_path: Path, monkeypatch, cli_on_path: str, value: str
) -> None:
    project = make_project(tmp_path / "project")
    install_cli(monkeypatch)
    monkeypatch.setenv("OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS", value)

    with pytest.raises(ToolError, match="Invalid OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS value"):
        compile_project(str(project))


def test_invalid_timeout_override_drops_diagnostics(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    """An attempt rejected before the CLI runs still invalidates stored lines.

    The policy is conservative: an attempt that produced no new result drops the
    previous diagnostics so nothing stale can be presented as current, even when
    the attempt failed before the compiler was ever started.
    """
    project = make_project(tmp_path / "project")
    compile_success(monkeypatch, project)
    install_cli(monkeypatch)
    monkeypatch.setenv("OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS", "abc")

    with pytest.raises(ToolError, match="Invalid OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS value"):
        compile_project(str(project))

    with pytest.raises(ToolError, match="No compilation diagnostics are available"):
        get_diagnostics(str(project))


def test_diagnostics_are_keyed_by_resolved_project_path(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    """Relative and absolute project paths share one diagnostics entry."""
    project = make_project(tmp_path / "project")
    install_cli(monkeypatch, returncode=4, stdout="", stderr="main.st:1: error: relative\n")
    monkeypatch.chdir(tmp_path)

    compile_project("project")

    assert get_diagnostics(str(project.resolve())) == ["main.st:1: error: relative"]


def test_diagnostics_of_different_projects_are_independent(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    first = make_project(tmp_path / "first")
    second = make_project(tmp_path / "second")
    install_cli(monkeypatch, returncode=4, stdout="", stderr="first.st:1: error: one\n")
    compile_project(str(first))
    install_cli(monkeypatch, returncode=4, stdout="", stderr="second.st:1: error: two\n")
    compile_project(str(second))

    assert get_diagnostics(str(first)) == ["first.st:1: error: one"]
    assert get_diagnostics(str(second)) == ["second.st:1: error: two"]


def test_compile_project_resolves_cli_through_path_extensions(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    """On Windows the CLI installs as openplc-cli.cmd; the resolved full path must be run."""
    project = make_project(tmp_path / "project")
    calls, _ = install_cli(monkeypatch, returncode=0, stdout='{"ok": true}', stderr="")

    compiled = compile_project(str(project))

    assert compiled == {"success": True, "exit_code": 0, "output": {"ok": True}}
    assert calls[0][0] == cli_on_path


def test_get_diagnostics_requires_compilation(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project")

    with pytest.raises(ToolError, match="No compilation diagnostics are available"):
        get_diagnostics(str(project))


class _GatedProcess:
    """A stub CLI whose first run blocks until the test releases it."""

    def __init__(self) -> None:
        self.pid = 0
        self.returncode = 0

    def communicate(self, input=None, timeout=None):  # noqa: A002 - matches Popen signature
        return '{"ok": 1}', ""


def install_gated_cli(monkeypatch, *, first_release: threading.Event) -> list[str]:
    """Install a stub CLI that reports entry/exit order and gates the first run."""
    order: list[str] = []
    gate_consumed = threading.Event()

    class GatedProcess(_GatedProcess):
        def __init__(self, args: list[str], **kwargs) -> None:
            super().__init__()

        def communicate(self, input=None, timeout=None):  # noqa: A002 - matches Popen signature
            order.append("enter")
            if not gate_consumed.is_set():
                gate_consumed.set()
                assert first_release.wait(10), "test never released the first compilation"
            order.append("exit")
            return '{"ok": 1}', ""

    monkeypatch.setattr("openplc_engineering_mcp.openplc.compiler.subprocess.Popen", GatedProcess)
    stub_process_termination(monkeypatch)
    return order


def test_simultaneous_compilations_of_one_project_are_serialized(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    project = make_project(tmp_path / "project")
    release = threading.Event()
    order = install_gated_cli(monkeypatch, first_release=release)

    first = threading.Thread(target=lambda: compile_project(str(project)))
    first.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not order:
        time.sleep(0.02)
    assert order == ["enter"], "first compilation never started"

    second = threading.Thread(target=lambda: compile_project(str(project)))
    second.start()
    time.sleep(0.5)
    assert order == ["enter"], f"second compilation interleaved with the first: {order}"

    release.set()
    first.join(10)
    second.join(10)
    assert order == ["enter", "exit", "enter", "exit"]
    # The latest completed compilation owns the diagnostics.
    assert get_diagnostics(str(project)) == []


def test_different_projects_compile_concurrently(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    first_project = make_project(tmp_path / "first")
    second_project = make_project(tmp_path / "second")
    release = threading.Event()
    order = install_gated_cli(monkeypatch, first_release=release)

    first = threading.Thread(target=lambda: compile_project(str(first_project)))
    first.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not order:
        time.sleep(0.02)

    second = threading.Thread(target=lambda: compile_project(str(second_project)))
    second.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and order.count("enter") < 2:
        time.sleep(0.02)
    # The second project's compilation runs (and may finish) while the first is
    # still blocked inside its own compilation.
    assert order.count("enter") == 2, f"projects did not compile concurrently: {order}"

    release.set()
    first.join(10)
    second.join(10)
    assert order.count("exit") == 2


def test_update_pou_waits_for_in_flight_compilation(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    project = make_project(tmp_path / "project")
    pou_dir = project / "pous" / "programs"
    pou_dir.mkdir(parents=True)
    source = b"PROGRAM MAIN\nEND_PROGRAM\n"
    pou = pou_dir / "MAIN.st"
    pou.write_bytes(source)
    source_hash = "sha256:" + hashlib.sha256(source).hexdigest()
    install_cli(monkeypatch, returncode=0, stdout='{"ok": true}', stderr="")
    compile_project(str(project))
    assert get_diagnostics(str(project)) == []

    release = threading.Event()
    install_gated_cli(monkeypatch, first_release=release)
    compiling = threading.Thread(target=lambda: compile_project(str(project)))
    compiling.start()
    deadline = time.monotonic() + 10
    lock = compiler.project_operation_lock(project.resolve())
    while time.monotonic() < deadline and not lock.locked():
        time.sleep(0.02)

    replacement = "PROGRAM MAIN\n    (* updated *)\nEND_PROGRAM\n"
    updated: list[UpdatePouResult] = []
    updating = threading.Thread(
        target=lambda: updated.append(update_pou(str(project), "MAIN", replacement, source_hash))
    )
    updating.start()
    time.sleep(0.5)
    assert pou.read_bytes() == source, "update_pou replaced bytes during a compilation"
    assert not updated, "update_pou finished during a compilation"

    release.set()
    compiling.join(10)
    updating.join(10)
    assert updated and updated[0]["content_hash"] == (
        "sha256:" + hashlib.sha256(replacement.encode("utf-8")).hexdigest()
    )
    # The physical replacement invalidated the concurrent compilation's diagnostics.
    with pytest.raises(ToolError, match="No compilation diagnostics are available"):
        get_diagnostics(str(project))
