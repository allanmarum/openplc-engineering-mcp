#!/usr/bin/env python3
"""Check the pinned toolchain through an external stdio MCP client and real CLI."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


async def verify(root: Path) -> None:
    pins = json.loads((REPO / "environment" / "validation.json").read_text())
    if platform.python_version() != pins["python"]:
        raise RuntimeError("Python patch differs from validation pin")
    if importlib.metadata.version("mcp") != pins["mcp_sdk"]:
        raise RuntimeError("MCP SDK differs from uv.lock baseline")
    if importlib.metadata.version("uv_build") != pins["build_backend"]:
        raise RuntimeError("Build backend differs from uv.lock baseline")
    cli = root / "bin" / "openplc-cli"
    evidence = root / "evidence"
    evidence.mkdir(exist_ok=True)

    def command(name: str, args: list[str]) -> dict:
        result = subprocess.run([str(cli), *args], capture_output=True, text=True, timeout=120)
        (evidence / f"{name}.stdout.json").write_text(result.stdout)
        (evidence / f"{name}.stderr.txt").write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(f"CLI {name} failed: {result.stderr}")
        payload = json.loads(result.stdout)
        if not payload.get("ok"):
            raise RuntimeError(f"CLI {name} did not report success: {payload}")
        return payload

    version = command("cli-version", ["--version"])
    if version["version"] != pins["openplc"]["version"]:
        raise RuntimeError("Wrong OpenPLC Editor version")
    arduino = root / "openplc-editor" / pins["arduino_cli"]["relative_path"]
    if digest(arduino) != pins["arduino_cli"]["sha256"]:
        raise RuntimeError("Bundled Arduino CLI hash differs")
    arduino_version = json.loads(
        subprocess.run(
            [str(arduino), "version", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
    )
    if arduino_version["VersionString"] != pins["arduino_cli"]["version"]:
        raise RuntimeError("Bundled Arduino CLI version differs")
    fixture_parent = root / "fixtures"
    fixture_parent.mkdir()
    command(
        "cli-create",
        ["create", "EnvironmentSmoke", "--path", str(fixture_parent), "--language", "st", "--json"],
    )
    project = fixture_parent / "EnvironmentSmoke"
    metadata = json.loads((project / "project.json").read_text())
    if not isinstance(metadata["data"]["configuration"]["resource"], dict):
        raise RuntimeError("CLI did not create the current configuration representation")
    device = project / "devices" / "configuration.json"
    configuration = json.loads(device.read_text())
    configuration["deviceBoard"] = pins["openplc"]["target"]
    device.write_text(json.dumps(configuration, indent=2) + "\n")
    source = project / "pous" / "programs" / "main.st"
    if not source.is_file():
        raise RuntimeError("CLI did not create a native ST POU")
    content = "PROGRAM main\nVAR\n    counter : INT := 0;\nEND_VAR\ncounter := counter + 1;\nEND_PROGRAM\n"
    source.write_text(content)
    types = project / "datatypes"
    types.mkdir(exist_ok=True)
    (types / "State.dt").write_text("TYPE\nState : (Stopped, Running) := Stopped;\nEND_TYPE\n")
    records = []
    server = StdioServerParameters(
        command=str(root / "mcp-venv" / "bin" / "openplc-engineering-mcp"), cwd=REPO, env=dict(os.environ)
    )
    with anyio.fail_after(240):
        async with stdio_client(server) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = sorted(tool.name for tool in listed.tools)
                if len(names) != 13:
                    raise RuntimeError(f"Expected thirteen MCP tools, got {names}")

                async def call(name: str, **extra) -> dict:
                    result = await session.call_tool(name, {"project_path": str(project), **extra})
                    if result.is_error or result.structured_content is None:
                        raise RuntimeError(f"MCP {name} failed: {result.content}")
                    payload = result.structured_content
                    records.append({"tool": name, "arguments": extra, "result": payload})
                    return payload

                await call("validate_project")
                pou = await call("read_pou", pou_name="main")
                if pou["content"] != content or pou["content_hash"] != "sha256:" + digest(source):
                    raise RuntimeError("Native ST read/hash mismatch")
                await call("list_datatypes")
                good = await call("compile_project")
                if not good["success"] or good["exit_code"] != 0:
                    raise RuntimeError(f"Real OpenPLC compilation failed: {good}")
                if good["output"].get("target") != pins["openplc"]["target"]:
                    raise RuntimeError("Wrong real compiler target")
                source.write_text(content.replace("counter :=", "missingCounter :="))
                bad = await call("compile_project")
                if bad["success"] or bad["exit_code"] != 4:
                    raise RuntimeError("Negative control did not produce a real compile failure")
                await call("get_diagnostics")
                source.write_text(content)
                recovered = await call("compile_project")
                if not recovered["success"]:
                    raise RuntimeError("Compilation did not recover after restoring valid ST")
    build = project / "build" / pins["openplc"]["target"]
    artifacts = {str(p.relative_to(project)): digest(p) for p in sorted(build.rglob("*")) if p.is_file()}
    if not any(name.endswith(".cpp") for name in artifacts):
        raise RuntimeError("OpenPLC did not materialize generated C++")
    packages = sorted(
        ({"name": d.metadata["Name"], "version": d.version} for d in importlib.metadata.distributions()),
        key=lambda item: item["name"].lower(),
    )
    (evidence / "mcp-smoke.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "python": sys.version,
                "packages": packages,
                "tools": names,
                "openplc": version,
                "arduino_cli": arduino_version,
                "target": pins["openplc"]["target"],
                "compile_scope": (
                    "STruC++ checking/code generation and Runtime v4 bundle; no runtime execution"
                ),
                "records": records,
                "artifacts": artifacts,
            },
            indent=2,
        )
        + "\n"
    )
    print("PASS: external MCP client, current-format fixture, real compile/failure/recovery")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    options = parser.parse_args()
    anyio.run(verify, options.root.resolve())
