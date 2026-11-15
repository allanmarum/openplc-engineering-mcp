# Getting started

Use this document for local setup, execution, and client configuration. For the complete pinned OpenPLC installation and repeated clean-environment check, follow [Reproducible validation environment](validation-environment.md).

## Requirements

- Release support baseline: Ubuntu 24.04 LTS x86_64 and CPython 3.12.14; see the [release contract](release-contract.md#platform-support)
- [uv](https://docs.astral.sh/uv/)
- `openplc-cli` on `PATH` when using `compile_project`
- Node.js/npm only when using MCP Inspector through `mcp dev`

Use the exact OpenPLC source revision and CLI installation in the [validation environment](validation-environment.md). See [OpenPLC projects](openplc-projects.md#compatibility-baseline) for the compatible project representation.

## Install

From a source checkout:

```bash
git clone https://github.com/allanmarum/openplc-engineering-mcp.git
cd openplc-engineering-mcp
uv sync --locked --python 3.12.14
```

From a published release, install the built wheel with any CPython 3.12 toolchain (for example `uv pip install openplc_engineering_mcp-<version>-py3-none-any.whl`) and use the console command installed by the package. The dependency ranges in the wheel metadata select libraries; the exact qualified versions come from the repository's `uv.lock`, as described in the [validation environment](validation-environment.md).

## Run the MCP server

The package exposes the `openplc-engineering-mcp` console script and uses stdio transport.

```bash
uv run openplc-engineering-mcp
```

## Connect an MCP client

The tested client configuration uses the official MCP Python SDK over local stdio:

- **command**: the absolute path to the installed `openplc-engineering-mcp` console script (from a checkout: `<checkout>/.venv/bin/openplc-engineering-mcp`);
- **arguments**: none;
- **environment**: inherit the client environment, with `openplc-cli` reachable on `PATH` for `compile_project`;
- **working directory**: any directory; the server resolves every project through the absolute `project_path` argument and never depends on the client's working directory;
- **credentials**: none; the server performs no network access or authentication.

```python
import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

server = StdioServerParameters(command="/absolute/path/to/openplc-engineering-mcp")

async def main() -> None:
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            print([tool.name for tool in listed.tools])

anyio.run(main)
```

This exact boundary — an external client process, real stdio, the installed console command, and a real `openplc-cli` on `PATH` — is exercised end to end by `scripts/verify-validation-environment.py` in the [reproducible environment](validation-environment.md) workflow. Desktop MCP applications can use the same command, arguments, and `PATH` requirement, but no desktop client was tested for this release; the SDK client above is the tested reference.

## Complete engineering example

One session performing the full documented workflow: inspect, read, replace with the exact-byte hash, compile, diagnose a failure, and recover.

```python
import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PROJECT = "/absolute/path/to/the/openplc/project"
server = StdioServerParameters(command="/absolute/path/to/openplc-engineering-mcp")

async def main() -> None:
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            await session.call_tool("validate_project", {"project_path": PROJECT})

            read = await session.call_tool(
                "read_pou", {"project_path": PROJECT, "pou_name": "main"}
            )
            content_hash = read.structured_content["content_hash"]

            replacement = "PROGRAM main\n    (* updated *)\nEND_PROGRAM\n"
            await session.call_tool(
                "update_pou",
                {
                    "project_path": PROJECT,
                    "pou_name": "main",
                    "content": replacement,
                    "expected_content_hash": content_hash,
                },
            )

            compiled = await session.call_tool("compile_project", {"project_path": PROJECT})
            if not compiled.structured_content["success"]:
                diagnostics = await session.call_tool(
                    "get_diagnostics", {"project_path": PROJECT}
                )
                print(diagnostics.structured_content["result"])

anyio.run(main)
```

## Troubleshooting

| Symptom | Meaning and next step |
| --- | --- |
| `openplc-cli was not found on PATH` | The console command's environment must include the OpenPLC CLI; see the [validation environment](validation-environment.md#verification-and-evidence) wrapper. |
| `Project path does not exist` / `does not contain project.json` | `project_path` must be a directory with a current-format `project.json`; check [`validate_project`](tools.md#validate_project). |
| `Unsupported OpenPLC project format` | The project uses a representation outside the current OpenPLC Editor format; see [OpenPLC projects](openplc-projects.md). |
| `POU changed since it was read` | The persisted bytes changed since `read_pou`; read again and use the new `content_hash`. |
| `POU language "..." is not supported for update` | Only Structured Text POUs can be replaced; see [`update_pou`](tools.md#update_pou). |
| `openplc-cli returned invalid JSON` | The CLI ran but printed non-JSON stdout; its stderr is still stored as diagnostics. |
| `openplc-cli compilation timed out after ... seconds` | Adjust `OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS` (positive integer) for the server process. |
| `Could not update POU "...": ...` | Filesystem failure during the atomic replacement; the original bytes were not truncated. |

## Operating limits

- `validate_project` and `update_pou` are intentionally shallow: metadata preconditions and the ST identity envelope, never compiler-level validation ([tools](tools.md#validate_project)).
- Compilations and POU updates on one project are serialized; a cancelled request stops waiting without starting work ([concurrency](tools.md#compile_project)).
- Diagnostics are process-local, keyed per resolved project, and invalidated by server-observed operations only; a restart starts empty ([diagnostics lifetime](tools.md#get_diagnostics)).
- Do not edit the same project in the OpenPLC Editor while operating on it through the MCP ([editor interaction](openplc-projects.md#pou-update)).
- Only the Ubuntu 24.04 / CPython 3.12.14 baseline is qualified; installation elsewhere is unsupported ([release contract](release-contract.md#platform-support)).

## Open with MCP Inspector

```bash
uv run mcp dev src/openplc_engineering_mcp/server.py --with-editable .
```

## Verify the project

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

For test conventions and development rules, continue with [`development.md`](development.md).
