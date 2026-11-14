# Research release contract

Contract revision: **1**, established on **2026-11-14**.

This contract fixes the delivery scope of the initial research release. It is a scope and acceptance specification, not a release announcement or evidence that qualification has passed. The package version and source commit will be recorded with the qualified release.

## Frozen interface

The server must expose exactly the following thirteen tools through the official MCP Python SDK over local stdio. Detailed parameters, return schemas, errors, and operation semantics remain in [MCP tools](tools.md).

| Tool | Permitted effect | Detailed contract |
| --- | --- | --- |
| `get_project_overview` | Read-only engineering summary | [Overview](tools.md#get_project_overview) |
| `get_project_structure` | Read-only recognized artifact inventory | [Structure](tools.md#get_project_structure) |
| `list_pous` | Read-only POU discovery | [POUs](tools.md#list_pous) |
| `list_datatypes` | Read-only project-defined type inspection | [Data types](tools.md#list_datatypes) |
| `get_execution_configuration` | Read-only configured Tasks and Program Instances | [Execution configuration](tools.md#get_execution_configuration) |
| `get_io_configuration` | Read-only active-board physical I/O inspection | [I/O configuration](tools.md#get_io_configuration) |
| `read_pou` | Read-only source and exact-byte hash | [POU reading](tools.md#read_pou) |
| `update_pou` | Replace one existing ST POU | [POU update](tools.md#update_pou) |
| `list_variables` | Read-only POU declaration inspection | [POU variables](tools.md#list_variables) |
| `list_global_variables` | Read-only resource global-variable inspection | [Global variables](tools.md#list_global_variables) |
| `validate_project` | Read-only shallow precondition checks | [Project validation](tools.md#validate_project) |
| `compile_project` | Invoke the OpenPLC CLI; local build artifacts may be written | [Compilation](tools.md#compile_project) |
| `get_diagnostics` | Read-only compiler-diagnostic retrieval | [Diagnostics](tools.md#get_diagnostics) |

The existing discovery assertion in `tests/test_server.py` must continue to match this exact set. Adding, removing, or renaming a tool requires an explicit contract revision. Contract-compatible corrections may refine error handling and lifecycle behavior without adding capabilities; corresponding tool documentation must be updated with the implementation.

## Project and mutation boundary

- Support only the current OpenPLC Editor representation described in [OpenPLC projects](openplc-projects.md), including native POU source, `data.configuration`, canonical `.dt` files, and current device/I/O files.
- Preserve the existing `plc-project` and `plc-library` distinctions and tool-specific applicability rules.
- Keep the OpenPLC CLI/compiler authoritative for compilation semantics. The release evidence must identify an exact compatible OpenPLC commit and toolchain; a moving branch name is insufficient for reproducibility.
- Limit source modification to complete replacement of one existing Structured Text Program, Function Block, or Function through `update_pou`. Name, category, language, and canonical path remain unchanged; the expected exact-byte hash is required.
- POU-local declarations may change as part of that replacement. Separate variable-editing, POU creation/deletion/renaming, configuration editing, and writes in other POU languages remain outside this release.
- Keep compilation as a separate explicit operation. A successful write establishes persistence; a successful compilation establishes compiler acceptance only. Runtime control behavior remains unvalidated by these operations.

Other exclusions and architectural non-goals remain in [Scope](scope.md). Legacy conversion, deployment, runtime control, forcing, HTTP transport, and authentication are subsequent-scope capabilities.

## Platform support

The release has one supported qualification baseline:

| Component | Frozen baseline |
| --- | --- |
| Operating system | Ubuntu 24.04 LTS, x86_64 |
| Interpreter | CPython 3.12.14 |
| Dependency installation | `uv sync --locked --python 3.12.14`, using the committed `uv.lock` |
| MCP transport | Local stdio |
| Compiler integration | Compatible local `openplc-cli` on `PATH`, with the exact OpenPLC commit, target, and toolchain recorded in release evidence |

The CI checks job explicitly selects Ubuntu 24.04, CPython 3.12.14, and uv 0.13.6. The [validation environment](validation-environment.md) fixes OpenPLC commit `f0bb418b9c3419e573dece4222558570f6a31378`, the compiler target, and verified external artifacts. Installed dependency versions are determined by the committed locks and recorded per run.

`pyproject.toml` retains its Python installation minimum of `>=3.12`; that lower bound does not qualify every later interpreter. Other Python minor versions, Linux distributions or architectures, Windows, and macOS are unqualified for this contract. Installation success on one of them does not establish release support. Extending the supported baseline requires corresponding evidence and a contract revision.

Selecting this baseline does not claim that real OpenPLC integration has already passed on it. Full support qualification requires every completion criterion below.

## Completion criteria

The research release is complete only when all criteria have recorded passing evidence for the same candidate commit. These are acceptance requirements; this document does not mark pending engineering work as implemented.

| ID | Required outcome | Evidence required |
| --- | --- | --- |
| RC-01 | Exactly thirteen discoverable tools with correct parameters, structured returns, and read/write annotations | SDK contract tests and an external stdio discovery record matching the manifest above |
| RC-02 | A reproducible supported environment | OS/architecture, exact Python and dependency versions, MCP/OpenPLC commits, compiler target and toolchain, and successful clean-environment setup |
| RC-03 | Correct read-only inspection of representative current-format projects and libraries | Reference projects with non-empty POUs, types, declarations, Tasks/instances, and I/O; expected/observed results and unchanged engineering-file hashes |
| RC-04 | Bounded ST POU replacement with preserved identity, exact-byte hashes, atomic persistence, and failure cleanup | Positive cases for all three POU categories; stale/invalid hash, ambiguity, unsupported-language, filesystem-failure, and non-target-file preservation checks; Editor reopen check |
| RC-05 | Comprehensible errors and continued service after invalid inputs | Malformed metadata/JSON/declarations, missing files, unreadable files, unsupported representations, and documented path/symlink behavior exercised through the MCP boundary |
| RC-06 | Correct and bounded real compilation | Successful and failed CLI runs, JSON/exit-code handling, missing CLI, paths with spaces/Unicode, timeout/cancellation behavior, child-process cleanup, and continued server usability |
| RC-07 | Explicit and tested diagnostic validity and lifetime | Results before compilation, after source edits, subsequent success/failure, timeout, project changes, and restart; documented historical/invalidation policy and verified noise filtering |
| RC-08 | Reliable client lifecycle and local transport | External-client initialization, repeated calls, project switching, error recovery, clean stdout protocol, shutdown/disconnection behavior, concurrency policy, and a usable client configuration |
| RC-09 | Independent real-toolchain integration validation | Automated external SDK client -> stdio server -> real project -> real OpenPLC CLI flow, including inspect/read/update/reread, compile error, correction, and compile success; expected outputs and before/after files checked independently |
| RC-10 | Passing software and documentation checks | `uv run pytest`, `uv run ruff check .`, `uv run pyright`, `uv build`, and `mkdocs build --strict`, with results tied to the candidate commit |
| RC-11 | Installable distribution artifacts | Wheel/source distribution with required metadata and license; clean installation of the built wheel and a successful console-command/client smoke run without the source checkout |
| RC-12 | Complete and consistent operating instructions | Verified setup and OpenPLC preparation, supported versions, client configuration, full read/update/compile/diagnose example, troubleshooting, state/concurrency limits, and agreement between docs and implementation |
| RC-13 | A traceable final delivery | Version/tag, exact commit and lockfile, distribution artifacts, closed in-scope defects, accepted limitations, release notes, and the archived evidence satisfying RC-01 through RC-12 |

For each criterion, the qualification record must identify the command or procedure, environment, expected and observed results, verdict, and evidence location. A criterion may only be marked passed when that evidence exists. Unit tests with a substituted compiler and the existing compiler-free stdio round trip do not replace RC-09.

An independent user must be able to install the indicated version, connect the documented MCP client, and complete the engineering workflow using only the delivered artifacts and instructions. LLM comparison trials and dissertation completion are downstream research work; they are not server-release qualification gates.

## Scope changes

Before extending tools, supported formats, mutation capabilities, or supported platforms, revise this contract and the affected [scope](scope.md) and [tool](tools.md) documents explicitly. Preserve the original contract and qualified release through version control. Fixes, documentation corrections, and validation work that satisfy the frozen boundary remain in scope.
