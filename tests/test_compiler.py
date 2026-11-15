import json
import subprocess
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from openplc_engineering_mcp.openplc.compiler import compile_project, get_diagnostics

FAKE_CLI = "/resolved/openplc-cli"


def make_project(root: Path) -> Path:
    root.mkdir()
    (root / "project.json").write_text(
        json.dumps({"meta": {"name": "Example", "type": "plc-project"}}), encoding="utf-8"
    )
    return root


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
