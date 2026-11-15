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

# Reference fixture contents. Expected MCP results below are derived from these
# bytes and from the fixture files themselves, never from server responses.
MAIN_CONTENT = (
    "PROGRAM main\nVAR\n    counter : INT := 0;\nEND_VAR\ncounter := counter + 1;\nEND_PROGRAM\n"
)
MOTOR_CONTENT = (
    "FUNCTION_BLOCK Motor\n"
    "VAR_INPUT\n    Start : BOOL;\nEND_VAR\n"
    "VAR_OUTPUT\n    Running : BOOL;\nEND_VAR\n"
    "    Running := Start;\n"
    "END_FUNCTION_BLOCK\n"
)
ADD_TEN_CONTENT = (
    "FUNCTION AddTen : INT\n"
    "VAR_INPUT\n    value : INT;\nEND_VAR\n"
    "    AddTen := value + 10;\n"
    "END_FUNCTION\n"
)
DATATYPE_CONTENT = "TYPE\nState : (Stopped, Running) := Stopped;\nEND_TYPE\n"
BROKEN_CONTENT = MAIN_CONTENT.replace("counter :=", "missingCounter :=")
ACCENTED_BROKEN = "PROGRAM main\nVAR\n    value : INT := 0;\nEND_VAR\nvalue := ;\nEND_PROGRAM\n"
ACCENTED_FIXED = "PROGRAM main\nVAR\n    value : INT := 0;\nEND_VAR\nvalue := 1;\nEND_PROGRAM\n"
GLOBAL_VARIABLE = {
    "name": "EmergencyStop",
    "type": {"definition": "base-type", "value": "BOOL"},
    "location": "",
    "initialValue": "",
    "documentation": "",
}
EXPECTED_MAIN_VARIABLES = [
    {
        "name": "counter",
        "class": "local",
        "type": "INT",
        "location": None,
        "initial_value": "0",
        "documentation": None,
    }
]
EXPECTED_MOTOR_VARIABLES = [
    {
        "name": "Start",
        "class": "input",
        "type": "BOOL",
        "location": None,
        "initial_value": None,
        "documentation": None,
    },
    {
        "name": "Running",
        "class": "output",
        "type": "BOOL",
        "location": None,
        "initial_value": None,
        "documentation": None,
    },
]
EXPECTED_ADD_TEN_VARIABLES = [
    {
        "name": "value",
        "class": "input",
        "type": "INT",
        "location": None,
        "initial_value": None,
        "documentation": None,
    }
]
EXPECTED_GLOBAL_VARIABLES = [
    {
        "name": "EmergencyStop",
        "class": "global",
        "type": "BOOL",
        "location": None,
        "initial_value": None,
        "documentation": None,
    }
]
EXPECTED_DATATYPES = [
    {"name": "State", "kind": "enumerated", "values": ["Stopped", "Running"], "initial_value": "Stopped"}
]


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def project_files(project: Path) -> list[str]:
    """List every fixture file outside build/, as an oracle independent of the MCP."""
    return sorted(
        path.relative_to(project).as_posix()
        for path in project.rglob("*")
        if path.is_file() and path.relative_to(project).parts[0] != "build"
    )


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
    resource = metadata["data"]["configuration"]["resource"]
    if not isinstance(resource, dict):
        raise RuntimeError("CLI did not create the current configuration representation")
    device = project / "devices" / "configuration.json"
    configuration = json.loads(device.read_text())
    configuration["deviceBoard"] = pins["openplc"]["target"]
    device.write_text(json.dumps(configuration, indent=2) + "\n")

    # Seed one representative ST POU of each category, a data type, a non-empty
    # active-board pin mapping, and a resource global variable. Exact bytes are
    # written so hashes and snapshots never depend on platform newline handling.
    (project / "pous" / "programs" / "main.st").write_bytes(MAIN_CONTENT.encode())
    (project / "pous" / "function-blocks").mkdir(exist_ok=True)
    (project / "pous" / "function-blocks" / "Motor.st").write_bytes(MOTOR_CONTENT.encode())
    (project / "pous" / "functions").mkdir(exist_ok=True)
    (project / "pous" / "functions" / "AddTen.st").write_bytes(ADD_TEN_CONTENT.encode())
    (project / "datatypes").mkdir(exist_ok=True)
    (project / "datatypes" / "State.dt").write_bytes(DATATYPE_CONTENT.encode())
    pin_mapping_path = project / "devices" / "pin-mapping.json"
    pin_mapping = json.loads(pin_mapping_path.read_text()) if pin_mapping_path.is_file() else {}
    expected_io_points = [
        {"pin": "2", "pin_type": "digitalInput", "address": "%IX0.0", "alias": "StartButton"},
        {"pin": "13", "pin_type": "digitalOutput", "address": "%QX0.0", "alias": None},
    ]
    pin_mapping[pins["openplc"]["target"]] = [
        {"pin": "2", "pinType": "digitalInput", "address": "%IX0.0", "alias": "StartButton"},
        {"pin": "13", "pinType": "digitalOutput", "address": "%QX0.0"},
    ]
    pin_mapping_path.write_text(json.dumps(pin_mapping, indent=2) + "\n")
    resource["globalVariables"] = [GLOBAL_VARIABLE]
    (project / "project.json").write_text(json.dumps(metadata, indent=2) + "\n")

    # Independent expectations derived from the fixture bytes and files above.
    source = project / "pous" / "programs" / "main.st"
    expected_files = project_files(project)
    expected_pous = [
        {"name": "AddTen", "type": "function", "language": "st", "path": "pous/functions/AddTen.st"},
        {
            "name": "Motor",
            "type": "function-block",
            "language": "st",
            "path": "pous/function-blocks/Motor.st",
        },
        {"name": "main", "type": "program", "language": "st", "path": "pous/programs/main.st"},
    ]
    expected_execution = {
        "tasks": [
            {
                "name": task["name"],
                "triggering": task["triggering"],
                "interval": task["interval"] if task["triggering"] == "Cyclic" else None,
                "priority": task["priority"],
            }
            for task in resource["tasks"]
        ],
        "program_instances": [
            {"name": instance["name"], "task": instance["task"], "program": instance["program"]}
            for instance in resource["instances"]
        ],
    }
    if not expected_execution["tasks"] or not expected_execution["program_instances"]:
        raise RuntimeError("CLI fixture lacks the representative task and program instance")
    expected_overview = {
        "name": "EnvironmentSmoke",
        "type": "plc-project",
        "pous": {"programs": ["main"], "function_blocks": ["Motor"], "functions": ["AddTen"]},
        "datatypes": ["State"],
        "global_variables": ["EmergencyStop"],
        "execution": {
            "tasks": [task["name"] for task in expected_execution["tasks"]],
            "program_instances": [item["name"] for item in expected_execution["program_instances"]],
        },
        "io": {"device_board": pins["openplc"]["target"], "mapping_count": len(expected_io_points)},
        "files": expected_files,
    }
    pou_contents = {"main": MAIN_CONTENT, "Motor": MOTOR_CONTENT, "AddTen": ADD_TEN_CONTENT}
    expected_variables = {
        "main": EXPECTED_MAIN_VARIABLES,
        "Motor": EXPECTED_MOTOR_VARIABLES,
        "AddTen": EXPECTED_ADD_TEN_VARIABLES,
    }

    def engineering_snapshot() -> dict[str, str]:
        return {
            relative: digest(project / relative) for relative in project_files(project)
        }

    baseline = engineering_snapshot()
    records: list[dict] = []
    checks: list[dict] = []

    def expect(label: str, expected: object, observed: object) -> None:
        if expected != observed:
            raise RuntimeError(f"{label}: expected {expected!r}, observed {observed!r}")
        checks.append({"check": label, "expected": expected, "observed": observed})

    # A second project whose parent path carries spaces and accented characters,
    # seeded with a syntax error for the failure/recovery control below.
    accented_parent = fixture_parent / "pasta com acentuação"
    command(
        "cli-create-accented",
        ["create", "AcaoSmoke", "--path", str(accented_parent), "--language", "st", "--json"],
    )
    accented_project = accented_parent / "AcaoSmoke"
    accented_device = accented_project / "devices" / "configuration.json"
    accented_configuration = json.loads(accented_device.read_text())
    accented_configuration["deviceBoard"] = pins["openplc"]["target"]
    accented_device.write_text(json.dumps(accented_configuration, indent=2) + "\n")
    accented_source = accented_project / "pous" / "programs" / "main.st"
    if not accented_source.is_file():
        raise RuntimeError("CLI did not create a native ST POU in the accented path")
    accented_source.write_bytes(ACCENTED_BROKEN.encode())

    server = StdioServerParameters(
        command=str(root / "mcp-venv" / "bin" / "openplc-engineering-mcp"), cwd=REPO, env=dict(os.environ)
    )
    with anyio.fail_after(480):
        async with stdio_client(server) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = sorted(tool.name for tool in listed.tools)
                expect("thirteen discoverable tools", 13, len(names))

                async def call(name: str, **extra) -> dict:
                    result = await session.call_tool(name, {"project_path": str(project), **extra})
                    if result.is_error or result.structured_content is None:
                        raise RuntimeError(f"MCP {name} failed: {result.content}")
                    payload = result.structured_content
                    records.append({"tool": name, "arguments": extra, "result": payload})
                    return payload

                async def call_error(name: str, match: str, **extra) -> str:
                    result = await session.call_tool(name, {"project_path": str(project), **extra})
                    if not result.is_error:
                        raise RuntimeError(f"MCP {name} unexpectedly succeeded: {result.content}")
                    text = next(
                        (item.text for item in result.content if hasattr(item, "text")), ""
                    )
                    if match not in text:
                        raise RuntimeError(f"MCP {name} error did not mention {match!r}: {text}")
                    records.append({"tool": name, "arguments": extra, "error": text})
                    return text

                # Read-only inspection checked against fixture-derived expectations.
                expect(
                    "validate_project",
                    {"valid": True, "name": "EnvironmentSmoke", "type": "plc-project", "warnings": []},
                    await call("validate_project"),
                )
                structure = await call("get_project_structure")
                expect("recognized file inventory", expected_files, structure["files"])
                expect("resolved project root", str(project.resolve()), structure["path"])
                expect("engineering overview", expected_overview, await call("get_project_overview"))
                expect("POU discovery", expected_pous, (await call("list_pous"))["result"])
                for pou_name in sorted(pou_contents):
                    pou = await call("read_pou", pou_name=pou_name)
                    expected_entry = next(item for item in expected_pous if item["name"] == pou_name)
                    expect(
                        f"read {pou_name} identity",
                        expected_entry,
                        {key: pou[key] for key in expected_entry},
                    )
                    expect(f"read {pou_name} content", pou_contents[pou_name], pou["content"])
                    expect(
                        f"read {pou_name} hash",
                        "sha256:" + sha256_hex(pou_contents[pou_name].encode()),
                        pou["content_hash"],
                    )
                    expect(
                        f"variables of {pou_name}",
                        expected_variables[pou_name],
                        (await call("list_variables", pou_name=pou_name))["result"],
                    )
                expect("data types", EXPECTED_DATATYPES, (await call("list_datatypes"))["result"])
                expect(
                    "execution configuration",
                    expected_execution,
                    await call("get_execution_configuration"),
                )
                expect(
                    "resource global variables",
                    EXPECTED_GLOBAL_VARIABLES,
                    (await call("list_global_variables"))["result"],
                )
                expect(
                    "active-board I/O",
                    {"device_board": pins["openplc"]["target"], "io_points": expected_io_points},
                    await call("get_io_configuration"),
                )

                # Positive compile control with the real pinned compiler.
                good = await call("compile_project")
                expect("compile succeeds", True, good["success"])
                expect("compile exit code", 0, good["exit_code"])
                expect("compiler target", pins["openplc"]["target"], good["output"].get("target"))
                expect("compiler status", True, good["output"].get("ok"))

                # Stale-hash rejection leaves the persisted bytes untouched.
                await call_error(
                    "update_pou",
                    "POU changed since it was read",
                    pou_name="main",
                    content=MAIN_CONTENT,
                    expected_content_hash="sha256:" + "0" * 64,
                )
                expect(
                    "source unchanged after stale-hash rejection",
                    sha256_hex(MAIN_CONTENT.encode()),
                    digest(source),
                )

                # Real undeclared-reference failure, diagnostics, and recovery.
                main_read = await call("read_pou", pou_name="main")
                broken = await call(
                    "update_pou",
                    pou_name="main",
                    content=BROKEN_CONTENT,
                    expected_content_hash=main_read["content_hash"],
                )
                expect(
                    "broken replacement hash",
                    "sha256:" + sha256_hex(BROKEN_CONTENT.encode()),
                    broken["content_hash"],
                )
                bad = await call("compile_project")
                expect("undeclared reference fails", False, bad["success"])
                expect("failure exit code", 4, bad["exit_code"])
                expect("failure code", "compile_failed", bad["output"].get("error", {}).get("code"))
                diagnostics = (await call("get_diagnostics"))["result"]
                if not any("missingCounter" in line or "MISSINGCOUNTER" in line for line in diagnostics):
                    raise RuntimeError(f"undeclared-reference diagnostic was not captured: {diagnostics}")
                scaffold_noise = ("File already exists", "Skipping creation.")
                if any(line.startswith(scaffold_noise) for line in diagnostics):
                    raise RuntimeError(f"platform noise leaked into diagnostics: {diagnostics}")
                checks.append(
                    {
                        "check": "failure diagnostics captured",
                        "expected": "non-empty, noise-free",
                        "observed": diagnostics,
                    }
                )
                restored = await call(
                    "update_pou",
                    pou_name="main",
                    content=MAIN_CONTENT,
                    expected_content_hash=broken["content_hash"],
                )
                expect(
                    "restored hash",
                    "sha256:" + sha256_hex(MAIN_CONTENT.encode()),
                    restored["content_hash"],
                )
                reread = await call("read_pou", pou_name="main")
                expect(
                    "POU identity preserved through updates",
                    {"name": "main", "type": "program", "language": "st", "path": "pous/programs/main.st"},
                    {key: reread[key] for key in ("name", "type", "language", "path")},
                )
                expect("restored content", MAIN_CONTENT, reread["content"])
                recovered = await call("compile_project")
                expect("recovered compile succeeds", True, recovered["success"])

                # The whole flow changed no engineering source: every file outside
                # build/ is byte-identical to the seeded baseline.
                expect("engineering sources unchanged by the whole flow", baseline, engineering_snapshot())

                # Library applicability control on the same session.
                command(
                    "cli-create-library",
                    [
                        "create",
                        "LibSmoke",
                        "--path",
                        str(fixture_parent),
                        "--type",
                        "plc-library",
                        "--language",
                        "st",
                        "--json",
                    ],
                )
                library = fixture_parent / "LibSmoke"
                expect(
                    "library validation",
                    {"valid": True, "name": "LibSmoke", "type": "plc-library", "warnings": []},
                    await call("validate_project", project_path=str(library)),
                )
                library_overview = await call("get_project_overview", project_path=str(library))
                expect("library overview has no I/O section", None, library_overview["io"])
                expect("library overview file inventory", project_files(library), library_overview["files"])
                await call_error(
                    "get_io_configuration",
                    "only available for OpenPLC plc-project",
                    project_path=str(library),
                )

                # Syntax-failure and recovery control at a path with spaces and
                # accented characters (distinct from the undeclared-reference
                # control above).
                syntax_bad = await call("compile_project", project_path=str(accented_project))
                if syntax_bad["success"]:
                    raise RuntimeError("Syntax-error control unexpectedly compiled")
                syntax_diagnostics = await call(
                    "get_diagnostics", project_path=str(accented_project)
                )["result"]
                if not syntax_diagnostics:
                    raise RuntimeError("Syntax-error compilation captured no diagnostics")
                checks.append(
                    {
                        "check": "syntax diagnostics captured",
                        "expected": "non-empty",
                        "observed": syntax_diagnostics,
                    }
                )
                broken_read = await call(
                    "read_pou", project_path=str(accented_project), pou_name="main"
                )
                expect("accented-path read content", ACCENTED_BROKEN, broken_read["content"])
                await call(
                    "update_pou",
                    project_path=str(accented_project),
                    pou_name="main",
                    content=ACCENTED_FIXED,
                    expected_content_hash=broken_read["content_hash"],
                )
                accented_recovery = await call(
                    "compile_project", project_path=str(accented_project)
                )
                if not accented_recovery["success"]:
                    raise RuntimeError("Accented-path project did not compile after correction")

    build = project / "build" / pins["openplc"]["target"]
    artifacts = {str(p.relative_to(project)): digest(p) for p in sorted(build.rglob("*")) if p.is_file()}
    required_artifacts = [
        f"build/{pins['openplc']['target']}/src/pou_MAIN.cpp",
        f"build/{pins['openplc']['target']}/src/configuration.cpp",
        f"build/{pins['openplc']['target']}/src/program.st",
        f"build/{pins['openplc']['target']}/src/strucpp_runtime/include/iec_types.hpp",
    ]
    for required in required_artifacts:
        if required not in artifacts:
            raise RuntimeError(f"OpenPLC build artifact missing: {required}")
    checks.append(
        {
            "check": "C++ and Runtime v4 bundle artifacts",
            "expected": required_artifacts,
            "observed": [name for name in artifacts if name in required_artifacts],
        }
    )
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
                "checks": checks,
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
