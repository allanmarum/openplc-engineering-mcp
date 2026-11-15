#!/usr/bin/env python3
"""Smoke-check an installed openplc-engineering-mcp console command over stdio.

This exercises the distribution artifact rather than the source checkout: the
server under test is the console command installed by the wheel, launched from
an unrelated working directory, with a project created for the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

SOURCE = "PROGRAM MAIN\nEND_PROGRAM\n"
REPLACEMENT = "PROGRAM MAIN\n    (* updated by the installed server *)\nEND_PROGRAM\n"


def sha256_token(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


async def smoke(command: str, project: Path) -> None:
    (project / "pous" / "programs").mkdir(parents=True)
    (project / "project.json").write_text(
        json.dumps({"meta": {"name": "Smoke", "type": "plc-project"}}), encoding="utf-8"
    )
    pou = project / "pous" / "programs" / "MAIN.st"
    pou.write_bytes(SOURCE.encode())

    async def call(name: str, arguments: dict) -> tuple[object, object | None, list[str]]:
        result = await session.call_tool(name, arguments)
        text = [
            TextContent.model_validate(item).text
            for item in result.content
            if isinstance(item, TextContent)
        ]
        return result.is_error, result.structured_content, text

    # The server runs from an unrelated working directory; only the console
    # command and the stdio protocol are involved.
    server = StdioServerParameters(command=command, cwd=str(project.parent))
    with anyio.fail_after(60):
        async with stdio_client(server) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                listed = await session.list_tools()
                if len(listed.tools) != 13:
                    raise RuntimeError(f"expected thirteen tools, found {len(listed.tools)}")

                is_error, payload, _ = await call(
                    "validate_project", {"project_path": str(project)}
                )
                if is_error or payload != {
                    "valid": True,
                    "name": "Smoke",
                    "type": "plc-project",
                    "warnings": [],
                }:
                    raise RuntimeError(f"validate_project failed: {payload}")

                is_error, payload, _ = await call(
                    "read_pou", {"project_path": str(project), "pou_name": "MAIN"}
                )
                if is_error or not isinstance(payload, dict) or payload["content"] != SOURCE:
                    raise RuntimeError(f"read_pou failed: {payload}")
                if payload["content_hash"] != sha256_token(SOURCE.encode()):
                    raise RuntimeError(f"read_pou hash mismatch: {payload}")

                is_error, payload, _ = await call(
                    "update_pou",
                    {
                        "project_path": str(project),
                        "pou_name": "MAIN",
                        "content": REPLACEMENT,
                        "expected_content_hash": sha256_token(SOURCE.encode()),
                    },
                )
                if is_error or payload != {
                    "name": "MAIN",
                    "content_hash": sha256_token(REPLACEMENT.encode()),
                }:
                    raise RuntimeError(f"update_pou failed: {payload}")

                is_error, payload, _ = await call(
                    "read_pou", {"project_path": str(project), "pou_name": "MAIN"}
                )
                if is_error or not isinstance(payload, dict) or payload["content"] != REPLACEMENT:
                    raise RuntimeError(f"reread failed: {payload}")

    if pou.read_bytes() != REPLACEMENT.encode():
        raise RuntimeError("installed server did not persist the replacement")
    print("PASS: installed console command served the stdio round trip")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", required=True, help="installed openplc-engineering-mcp path")
    options = parser.parse_args()
    project = Path(tempfile.mkdtemp(prefix="openplc-mcp-wheel-smoke-")) / "project"
    anyio.run(smoke, options.command, project)


if __name__ == "__main__":
    main()
