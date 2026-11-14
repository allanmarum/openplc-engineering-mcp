# openplc-engineering-mcp

A small experimental Python MCP server that exposes domain-oriented engineering operations for the [OpenPLC Editor](https://github.com/Autonomy-Logic/openplc-editor) project.

The project uses the official Model Context Protocol Python SDK and stdio transport. Inspection tools are read-only; compilation is delegated to the authoritative `openplc-cli`.

**[Documentation](https://industrix-com-br.github.io/openplc-engineering-mcp/)**

## Quick start

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

The release support baseline is Ubuntu 24.04 LTS x86_64 with CPython 3.12.14. The [release contract](docs/release-contract.md) freezes the thirteen-tool interface and defines the evidence required before the release can be declared complete.

```bash
git clone https://github.com/allanmarum/openplc-engineering-mcp.git
cd openplc-engineering-mcp
uv sync --locked --python 3.12.14
uv run openplc-engineering-mcp
```

Run the test suite with:

```bash
uv run pytest
```

## Current tools

- `get_project_overview`
- `get_project_structure`
- `list_pous`
- `list_datatypes`
- `get_execution_configuration`
- `get_io_configuration`
- `read_pou`
- `update_pou`
- `list_variables`
- `list_global_variables`
- `validate_project`
- `compile_project`
- `get_diagnostics`

`get_project_overview` provides an engineering navigation map; `get_project_structure` inventories recognized physical artifacts. Use specialized inspection tools for details.

Inspection tools are read-only. `update_pou` replaces the complete content of one existing Structured Text POU with optimistic-concurrency and atomic-write protection. `compile_project` is a local write operation that requires `openplc-cli` on `PATH`.

## Documentation

Read the [published documentation](https://industrix-com-br.github.io/openplc-engineering-mcp/) for setup, architecture, MCP tools, OpenPLC project behavior, development, scope, and research context.

The source documentation remains in [`docs/`](docs/) and [`docs/index.md`](docs/index.md) is the entry point for agents working directly with the repository.

## Compatibility

The frozen OpenPLC Editor/CLI revision is [`f0bb418b9c3419e573dece4222558570f6a31378`](https://github.com/Autonomy-Logic/openplc-editor/tree/f0bb418b9c3419e573dece4222558570f6a31378), source version 4.2.12. Follow the [reproducible validation recipe](docs/validation-environment.md) for its installation, exact compiler dependencies, Runtime v4 compile-only target, and two independent clean-environment checks.

See [OpenPLC projects](docs/openplc-projects.md) for the current-format boundary. Legacy conversion remains outside the supported scope.

## Scope

The current version focuses on project, execution-configuration, physical I/O, POU, data-type, and variable inspection and shallow project validation, plus complete-content replacement of existing Structured Text POUs and compilation delegated to `openplc-cli`. POU creation/deletion/renaming, writes to other POU languages, data-type modification, resource-configuration modification, deployment, runtime control, variable forcing, authentication, HTTP transport, and generic shell/filesystem tools are not implemented.

See [`docs/scope.md`](docs/scope.md) for the current boundary.
