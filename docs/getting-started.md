# Getting started

Use this document for local setup and execution. For the complete pinned OpenPLC installation and repeated clean-environment check, follow [Reproducible validation environment](validation-environment.md).

## Requirements

- Release support baseline: Ubuntu 24.04 LTS x86_64 and CPython 3.12.14; see the [release contract](release-contract.md#platform-support)
- [uv](https://docs.astral.sh/uv/)
- `openplc-cli` on `PATH` when using `compile_project`
- Node.js/npm only when using MCP Inspector through `mcp dev`

Use the exact OpenPLC source revision and CLI installation in the [validation environment](validation-environment.md). See [OpenPLC projects](openplc-projects.md#compatibility-baseline) for the compatible project representation.

## Install

```bash
git clone https://github.com/allanmarum/openplc-engineering-mcp.git
cd openplc-engineering-mcp
uv sync --locked --python 3.12.14
```

## Run the MCP server

The package exposes the `openplc-engineering-mcp` console script and uses stdio transport.

```bash
uv run openplc-engineering-mcp
```

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
