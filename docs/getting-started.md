# Getting started

Use this document for local setup and execution only.

## Requirements

- Release support baseline: Ubuntu 24.04 LTS x86_64 and CPython 3.12.x; see the [release contract](release-contract.md#platform-support)
- [uv](https://docs.astral.sh/uv/)
- `openplc-cli` on `PATH` when using `compile_project`
- Node.js/npm only when using MCP Inspector through `mcp dev`

The headless OpenPLC CLI used by `compile_project()` was merged into the OpenPLC Editor `development` branch on August 24, 2026 (PR #1026) and is newer than the latest published Editor release v4.2.11. See [`openplc-projects.md`](openplc-projects.md#compatibility-baseline) for the complete compatibility boundary.

## Install

```bash
git clone https://github.com/allanmarum/openplc-engineering-mcp.git
cd openplc-engineering-mcp
uv sync --locked --python 3.12
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
