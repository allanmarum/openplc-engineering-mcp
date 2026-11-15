# MCP tools

The [release contract](release-contract.md) freezes this thirteen-tool interface for the research release. This document remains the source for detailed inputs, outputs, errors, and operation semantics.

The current server registers exactly thirteen domain-oriented tools. All tools use `open_world_hint: false`. Inspection and diagnostics tools are read-only; `compile_project` and `update_pou` are registered with `read_only_hint: false`. Compilation may write local build artifacts through the OpenPLC CLI; `update_pou` replaces the persisted content of one existing Structured Text POU.

The public registrations live in `src/openplc_engineering_mcp/server.py`. OpenPLC behavior is grouped by responsibility under `src/openplc_engineering_mcp/openplc/`.

All project-inspection tools target the **current OpenPLC Editor project format only**. Historical project representations and backward-compatibility parsing are outside the supported contract. See [`scope.md`](scope.md).

| Tool | Read-only | Purpose |
| --- | --- | --- |
| `get_project_overview` | yes | Orient further inspection using a concise engineering navigation map |
| `get_project_structure` | yes | Inspect recognized files in an OpenPLC project |
| `list_pous` | yes | Discover Programs, Function Blocks, and Functions |
| `list_datatypes` | yes | Inspect project-defined enumerated, structure, and array data types |
| `get_execution_configuration` | yes | Inspect configured Tasks and Program Instances |
| `get_io_configuration` | yes | Inspect the selected device board and its active local physical I/O mapping |
| `read_pou` | yes | Read a POU by domain name without requiring its filesystem path |
| `update_pou` | no | Replace the complete content of an existing Structured Text POU |
| `list_variables` | yes | Inspect variables declared by a POU |
| `list_global_variables` | yes | Inspect the project's resource-level global variables |
| `validate_project` | yes | Check the MCP's shallow project preconditions |
| `compile_project` | no | Compile through `openplc-cli` |
| `get_diagnostics` | yes | Return diagnostics captured from the latest compilation |

## `get_project_overview`

Input: `project_path: str` (required).

Returns a concise engineering navigation map:

```json
{
  "name": "Conveyor",
  "type": "plc-project",
  "pous": {"programs": ["MAIN"], "function_blocks": ["Motor"], "functions": []},
  "datatypes": ["MotorState"],
  "global_variables": ["EmergencyStop"],
  "execution": {"tasks": ["MainTask"], "program_instances": ["MainInstance"]},
  "io": {"device_board": "Arduino Uno", "mapping_count": 1},
  "files": ["datatypes/MotorState.dt", "devices/configuration.json", "devices/pin-mapping.json", "pous/function-blocks/Motor.st", "pous/programs/MAIN.st", "project.json"]
}
```

All fields are present. POUs are grouped names from `list_pous`, including its global name deduplication and ordering. Data-type names follow `list_datatypes` order; resource global variable names, Task names and Program Instance names retain their dedicated readers' stored order. `mapping_count` counts the active board's I/O points only. Missing optional collections are empty; a PLC project without explicit board selection uses the existing `OpenPLC Simulator` default. For `plc-library`, `io` is `null` and physical I/O files are not parsed; other summaries follow the same readers, with empty execution collections when configuration is absent.

`files` is exactly the sorted, deduplicated recognized artifact inventory from `get_project_structure`, without its resolved root path. The distinction is intentional: `get_project_overview` answers what engineering elements exist for subsequent navigation, whereas `get_project_structure` inventories their recognized physical representation. Neither is a complete project/schema representation or a generic filesystem browser.

The operation composes existing domain readers and propagates their `ToolError` failures; malformed inspected data fails the call rather than yielding a partial overview. It returns no POU source, variable declarations, data-type definitions, complete mappings, diagnostics, or arbitrary file content. Readers may parse detailed data internally to produce these summaries. Reads are sequential, not an atomic project snapshot.

The agent can use POU names with `read_pou` and `list_variables`, then request `list_datatypes`, `list_global_variables`, `get_execution_configuration`, or `get_io_configuration` for relevant details. Subsequent `update_pou` and `compile_project` calls remain explicit agent decisions. The overview performs no writes or compilation and keeps no active project or cache.

## `get_project_structure`

Input:

- `project_path: str`

Returns:

- resolved project `path`;
- project `name`;
- project `type`;
- sorted list of recognized current-format project `files`.

Enumeration is fail-closed: a directory or file that cannot be inspected is a tool error naming the path, never a silent omission. Directory symlinks are not followed and file symlinks escaping the project root are excluded (see [`openplc-projects.md`](openplc-projects.md) for the full policy).

Historical JSON POU files are not included in the recognized POU layout. Use [`openplc-projects.md`](openplc-projects.md) for the supported structure.

## `list_pous`

Input:

- `project_path: str`

Returns the current-format Programs, Function Blocks, and Functions discovered from recognized POU source files. Each item contains:

- `name`;
- `type`;
- `language`;
- project-relative `path`.

Historical JSON-only POUs are not supported. See [`openplc-projects.md`](openplc-projects.md) for language mapping and discovery behavior.

## `list_datatypes`

Input:

- `project_path: str`.

Returns only project-defined data types persisted in the current OpenPLC format under `datatypes/**/*.dt`, sorted by name.

Enumeration:

```json
{
  "name": "OperatingMode",
  "kind": "enumerated",
  "values": ["Auto", "Manual", "Maintenance"],
  "initial_value": "Auto"
}
```

Structure:

```json
{
  "name": "MotorStatus",
  "kind": "structure",
  "fields": [
    {
      "name": "speed",
      "type": "REAL",
      "initial_value": "0.0",
      "documentation": "Current motor speed"
    }
  ]
}
```

Array:

```json
{
  "name": "TemperatureBuffer",
  "kind": "array",
  "base_type": "REAL",
  "dimensions": ["0..99"],
  "initial_value": null
}
```

Declared field types and initial values remain strings. Inline array fields therefore remain domain-readable declarations such as `ARRAY [0..9] OF REAL`; IEC literals are not evaluated. Multidimensional array bounds are returned independently.

Each `.dt` file must contain exactly one supported `TYPE ... END_TYPE` declaration, and its declared type name must match the file name case-insensitively. Malformed or unsupported definitions raise a tool error; the MCP does not silently skip a bad type and return a partial project view.

Embedded historical `project.json.data.dataTypes` definitions are not parsed. If they are the only data-type representation, the tool reports an unsupported OpenPLC project format.

The tool intentionally does not provide built-in IEC type listing, library data-type discovery, modification, recursive resolution, dependency graphs, semantic reference validation, or a complete IEC 61131-3 grammar.

## `get_execution_configuration`

Input:

- `project_path: str`.

Returns the configured execution model from `data.configuration.resource`:

```json
{
  "tasks": [
    {
      "name": "MainTask",
      "triggering": "Cyclic",
      "interval": "T#20ms",
      "priority": 0
    }
  ],
  "program_instances": [
    {
      "name": "MainInstance",
      "task": "MainTask",
      "program": "main"
    }
  ]
}
```

For a `Cyclic` Task, `interval` preserves the stored IEC `TIME` string exactly. For an `Interrupt` Task, the MCP returns `interval: null` rather than presenting the stored interval as cyclic timing.

Each Program Instance contains its name, Task reference, and Program reference exactly as stored. The MCP does not independently validate those references or evaluate IEC time literals.

A project with no execution configuration returns empty lists. Malformed current-format Task or Program Instance structures are MCP tool errors. The historical `data.configurations` representation is unsupported.

This tool reports the configured project model, not live runtime execution state.

## `get_io_configuration`

Input:

- `project_path: str`.

Returns the currently selected device board and only that board's current-format local pin mapping:

```json
{
  "device_board": "Arduino Uno",
  "io_points": [
    {
      "pin": "2",
      "pin_type": "digitalInput",
      "address": "%IX0.0",
      "alias": "StartButton"
    },
    {
      "pin": "13",
      "pin_type": "digitalOutput",
      "address": "%QX0.0",
      "alias": null
    }
  ]
}
```

The authoritative current-format sources are `devices/configuration.json` for `deviceBoard` and the per-board object in `devices/pin-mapping.json` for `DevicePin` mappings. Mapping entries for inactive boards are editor state and are not returned.

Each I/O point contains only:

- `pin` — physical pin identifier as stored;
- `pin_type` — `digitalInput`, `digitalOutput`, `analogInput`, or `analogOutput`;
- `address` — IEC address string as stored, without grammar revalidation or normalization;
- `alias` — stored alias, or `null` when the optional alias is absent.

Stored I/O point order is preserved. A missing pin-mapping file, an empty mapping, or an active board with no mapping returns an empty `io_points` list. When `devices/configuration.json` is absent, the tool follows the current OpenPLC default board, `OpenPLC Simulator`.

Malformed device JSON, invalid current-format structures, or malformed pin entries are tool errors. The MCP validates the complete per-board mapping file before returning the active subset, so malformed inactive-board data is not silently ignored. The historical flat pin array and legacy pin `name` field are intentionally unsupported rather than migrated.

The operation is available only for `plc-project` projects. It does not inspect PLC variables, resolve variable-to-alias references, inspect POU source, return communication settings, expose `vendorScreenData`, inspect remote devices or protocol configuration, or report live runtime I/O state. Vendor-specific VPP configuration remains outside this stable `DevicePin` contract.

## `read_pou`

Input:

- `project_path: str`;
- `pou_name: str`.

Returns one current-format POU source with:

- `name`;
- `type`;
- `language`;
- project-relative `path`;
- `content` exactly as read from the selected UTF-8 source file;
- `content_hash`, an exact-byte SHA-256 token of the persisted file.

The caller identifies the POU by name rather than by filesystem path. An empty `pou_name`, an unknown POU name, or an unreadable source file is exposed as an MCP tool error. The operation performs no parsing, normalization, dependency analysis, or modification of POU content.

The `content` and `content_hash` are derived from the same exact persisted bytes with no newline normalization, so the pair always describes one consistent version. The hash is the optimistic-concurrency token required by [`update_pou`](#update_pou).

## `update_pou`

Input:

- `project_path: str`;
- `pou_name: str`;
- `content: str`;
- `expected_content_hash: str`.

Replaces the complete persisted representation of an existing Structured Text POU selected by domain name. The caller never supplies a filesystem path; the MCP resolves the canonical POU file internally. The operation updates one existing POU in place — it does not create, delete, rename, move, or convert POUs.

Returns the updated POU identity and the new exact-byte hash:

```json
{
  "name": "MAIN",
  "content_hash": "sha256:..."
}
```

Writable scope:

- Structured Text (`.st`) is the only writable language in v1;
- supported existing POU types are Program, Function Block, and Function;
- the whole POU is replaced, so documentation, a Function return type, POU-local declaration blocks, body logic, comments, and formatting may all change together;
- POU-local variables are therefore modified through the complete replacement rather than separate variable tools.

Immutable identity:

- the POU name, type, language, and canonical path never change;
- the replacement must declare the same POU type keyword and exactly the target name;
- a Function replacement must declare a return type;
- the matching `END_PROGRAM` / `END_FUNCTION_BLOCK` / `END_FUNCTION` keyword must be present.

A rename, type change, or language change is a different domain operation and is rejected.

Optimistic concurrency:

- `expected_content_hash` is required and must match the exact bytes currently on disk;
- it is normally the `content_hash` returned by the `read_pou()` the edit is based on;
- a stale or malformed hash rejects the update and leaves the target unchanged.

The exact guarantee and its residual limits — no cross-process locking, and the race window between the final hash re-check and the atomic replacement — are detailed in [`openplc-projects.md`](openplc-projects.md#pou-update).

Update-time POU resolution is stricter than the read/list tools. If more than one recognized current-format source file shares the requested stem, the update is rejected as ambiguous instead of silently choosing one.

Existing POUs in other languages (`il`, `ld`, `fbd`, `python`, `cpp`) remain readable through the read tools but are rejected by `update_pou` before any mutation.

Persistence safety:

- the replacement is written to a temporary file in the target directory, flushed, `fsync`ed, re-checked against the expected hash, then atomically moved into place;
- the original POU is never truncated directly;
- every validation, concurrency, or I/O failure before the final replacement leaves the original bytes unchanged;
- no backup or history files are created.

`update_pou` deliberately does not compile. Semantic correctness remains the responsibility of an explicit `compile_project()` / `get_diagnostics()` step, which keeps persistence success and compilation success as separate observable facts.

An empty name, empty replacement, malformed expected hash, unknown or ambiguous POU, unsupported language, symbolic-link or non-regular target, stale hash, identity mismatch, missing terminal keyword, or filesystem failure is exposed as an MCP tool error.

This operation should not be used while the same project is actively open in the OpenPLC Editor. See [`openplc-projects.md`](openplc-projects.md) for the editor-interaction limitation.

## `list_variables`

Input:

- `project_path: str`;
- `pou_name: str`.

Returns variables declared by the selected current-format POU source in declaration order. Each item contains:

```json
{
  "name": "Start",
  "class": "input",
  "type": "BOOL",
  "location": null,
  "initial_value": null,
  "documentation": null
}
```

Supported variable classes are:

| OpenPLC declaration block | MCP class |
| --- | --- |
| `VAR_INPUT` | `input` |
| `VAR_OUTPUT` | `output` |
| `VAR_IN_OUT` | `inOut` |
| `VAR_EXTERNAL` | `external` |
| `VAR_TEMP` | `temp` |
| `VAR_GLOBAL` | `global` |
| `VAR` | `local` |

The `type` field preserves the declared type as a domain-readable string such as `BOOL`, `TON`, or `ARRAY[0..9] OF INT`. Initial values remain declaration-level strings and are not evaluated. A declared `AT` binding is returned as `location`; absent location, initial value, or documentation is returned as `null`.

A valid POU with no variable declarations returns an empty list. Empty or unknown POU names and malformed declarations that prevent reliable extraction are MCP tool errors. Historical JSON POU variable representations are not supported.

## `list_global_variables`

Input:

- `project_path: str`.

Returns the project's resource-level global variables stored under `data.configuration.resource.globalVariables` in stored order. Each item uses the same public variable representation as [`list_variables`](#list_variables):

```json
{
  "name": "EmergencyStop",
  "class": "global",
  "type": "BOOL",
  "location": "%IX0.0",
  "initial_value": null,
  "documentation": "Emergency stop input"
}
```

`class` is always `global` because the containing resource defines the scope. The declared type is read from the current OpenPLC structured type object's `value` field. Empty stored optional values are normalized to `null`.

The tool intentionally does not scan POUs for `VAR_GLOBAL` declarations and does not include named global variable lists (`globalVariableLists` / GVLs).

A project with no execution configuration or no `globalVariables` returns an empty list. Structurally malformed current-format data is raised as an MCP tool error. Historical configuration and alternate variable representations are unsupported.

## `validate_project`

Input:

- `project_path: str`

Returns a successful shallow validation result:

```json
{
  "valid": true,
  "name": "Example",
  "type": "plc-project",
  "warnings": []
}
```

Unrecoverable local precondition failures are MCP tool errors rather than `{ "valid": false }` results.

The tool intentionally validates only the minimum metadata needed by the MCP. Current supported project types are `plc-project` and `plc-library`; historical project type aliases are unsupported. The tool does not perform compiler-level or runtime-level validation.

## `compile_project`

Input:

- `project_path: str`

After resolving and validating the project path, the implementation runs:

```text
openplc-cli compile <resolved-project-path> --json
```

It returns:

- `success`: whether the CLI exited with status `0`;
- `exit_code`: the CLI exit code;
- `output`: parsed JSON from CLI `stdout`, or `null` when `stdout` is empty.

The command must be available as `openplc-cli` on `PATH`; the executable is resolved through PATH extensions (so the Windows `openplc-cli.cmd` shim is executable), and the process is bounded by a 300-second timeout. A missing executable, an OS error while starting the process, a compilation that exceeds the timeout, or non-JSON non-empty `stdout` is exposed as an MCP tool error.

The timeout defaults to 300 seconds and can be overridden for the server process with the `OPENPLC_MCP_COMPILE_TIMEOUT_SECONDS` environment variable (a positive integer; any other value is an MCP tool error). The tool runs the CLI in a worker thread and can be interrupted: when the timeout fires, when the client sends a `notifications/cancelled` for the in-flight request, or when the client disconnects, the CLI process **and all of its descendants** are terminated promptly (process-group kill on POSIX, where the group id is captured at spawn time so descendants of an already-exited CLI are still reached; `taskkill /T` on Windows), not left running until the timeout. Cleanup itself is bounded — the child is reaped with a grace period instead of waiting on pipe EOF, which a stuck descendant could delay indefinitely — and the abandoned attempt drops the project's previous diagnostics. After a cancelled or timed-out call the server remains usable on the same connection.

Practical limits, kept honest with what the tests establish: a descendant that deliberately starts its own session/process group escapes the POSIX group kill; on Windows — outside the supported qualification baseline — `taskkill /T` cannot reach descendants once the direct CLI process has already exited; and descendants the CLI itself leaves behind after a *successful* exit are not cleaned up.

Concurrency: compilations and [`update_pou`](#update_pou) calls targeting the **same** resolved project are serialized (the later operation waits; there is no queue, scheduler, or rejection). This makes diagnostics ownership deterministic — the latest completed compilation owns the stored diagnostics, and a physical POU replacement can never interleave with a compilation. Read-only tools and operations on different projects run concurrently.

Non-empty lines from CLI `stderr` are captured as diagnostics for [`get_diagnostics`](#get_diagnostics), except known-benign Electron platform-log lines — non-fatal Chromium-format lines (`[pid:date/time:LEVEL:source(line)]`) from the observed D-Bus chatter sources `bus.cc` and `object_proxy.cc` — and the editor's user-data scaffolding notices (`File already exists at ...` / `Skipping creation.`). Chromium lines from unknown sources and every `FATAL` line are preserved.

## `get_diagnostics`

Input:

- `project_path: str`

Returns the `stderr` lines captured from the project's most recent `compile_project` call, with Electron platform-log and scaffolding noise excluded (see [`compile_project`](#compile_project)).

Diagnostics are intentionally process-local and always describe the project's current compiled state:

- they are stored in memory, keyed by the resolved project path;
- a later completed compilation of the same project replaces the previous diagnostics;
- a successful `update_pou` that physically replaces a POU drops that project's diagnostics (a byte-identical no-op update does not);
- a compilation attempt that produces no new result — a missing CLI, a process start failure, or a timeout — also drops the previous diagnostics, so stored lines can never refer to an earlier compile attempt;
- they are not persisted across server restarts;
- calling this tool when no diagnostics are stored raises an MCP tool error.

An executed compilation with no `stderr` produces an empty list.

## Adding a tool

Before adding a new tool:

1. verify that the operation belongs inside the current scope;
2. confirm that the representation is part of the current OpenPLC Editor project format;
3. keep the public tool domain-oriented;
4. keep the MCP registration in `server.py` thin;
5. place OpenPLC-specific behavior in the smallest cohesive domain module;
6. test MCP contract behavior through the official MCP SDK client;
7. test domain behavior in the corresponding domain test module;
8. update this document when the public tool contract changes.
