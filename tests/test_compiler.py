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


def test_compile_project_and_get_diagnostics(tmp_path: Path, monkeypatch, cli_on_path: str) -> None:
    project = make_project(tmp_path / "project")

    def fake_run(args, **kwargs):
        assert args == [cli_on_path, "compile", str(project.resolve()), "--json"]
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "check": False,
            "timeout": 300,
        }
        return subprocess.CompletedProcess(
            args,
            4,
            stdout='{"code":"compile_failed"}',
            stderr="main.st:1: error: undeclared variable\n",
        )

    monkeypatch.setattr("openplc_engineering_mcp.openplc.compiler.subprocess.run", fake_run)

    compiled = compile_project(str(project))

    assert compiled == {
        "success": False,
        "exit_code": 4,
        "output": {"code": "compile_failed"},
    }
    assert get_diagnostics(str(project)) == ["main.st:1: error: undeclared variable"]


def test_compile_project_diagnostics_exclude_platform_noise(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    project = make_project(tmp_path / "project")

    def fake_run(args, **kwargs):
        stderr = "\n".join(
            [
                "[165795:0904/202137.665319:ERROR:bus.cc(408)] Failed to connect to the bus",
                "File already exists at /home/allan/.config/open-plc-editor/User/settings.json.",
                "Skipping creation.",
                "main.st:1: error: undeclared variable",
                "[165795:0904/202137.665399:ERROR:object_proxy.cc(576)] Failed to call method",
            ]
        )
        return subprocess.CompletedProcess(args, 4, stdout="", stderr=stderr)

    monkeypatch.setattr("openplc_engineering_mcp.openplc.compiler.subprocess.run", fake_run)

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

    def fake_run(args, **kwargs):
        stderr = "\n".join([fatal_line, unknown_source_line, "main.st:1: error: undeclared variable"])
        return subprocess.CompletedProcess(args, 4, stdout="", stderr=stderr)

    monkeypatch.setattr("openplc_engineering_mcp.openplc.compiler.subprocess.run", fake_run)

    compile_project(str(make_project(tmp_path / "project")))

    assert get_diagnostics(str(tmp_path / "project")) == [
        fatal_line,
        unknown_source_line,
        "main.st:1: error: undeclared variable",
    ]


def test_compile_project_reports_missing_cli(tmp_path: Path, monkeypatch) -> None:
    project = make_project(tmp_path / "project")

    monkeypatch.setattr("openplc_engineering_mcp.openplc.compiler.shutil.which", lambda name: None)

    def unreachable_run(*args, **kwargs):
        raise AssertionError("subprocess.run must not be reached without a resolved CLI")

    monkeypatch.setattr(
        "openplc_engineering_mcp.openplc.compiler.subprocess.run", unreachable_run
    )

    with pytest.raises(ToolError, match="openplc-cli was not found on PATH"):
        compile_project(str(project))


def test_compile_project_times_out(tmp_path: Path, monkeypatch, cli_on_path: str) -> None:
    project = make_project(tmp_path / "project")

    def hanging_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr("openplc_engineering_mcp.openplc.compiler.subprocess.run", hanging_run)

    with pytest.raises(ToolError, match="timed out after 300 seconds"):
        compile_project(str(project))


def test_compile_project_resolves_cli_through_path_extensions(
    tmp_path: Path, monkeypatch, cli_on_path: str
) -> None:
    """On Windows the CLI installs as openplc-cli.cmd; the resolved full path must be run."""
    project = make_project(tmp_path / "project")
    seen: list[list[str]] = []

    def fake_run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr("openplc_engineering_mcp.openplc.compiler.subprocess.run", fake_run)

    compiled = compile_project(str(project))

    assert compiled == {"success": True, "exit_code": 0, "output": {"ok": True}}
    assert seen[0][0] == cli_on_path


def test_get_diagnostics_requires_compilation(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project")

    with pytest.raises(ToolError, match="No compilation diagnostics are available"):
        get_diagnostics(str(project))
