# Reproducible validation environment

This is the installation recipe for the local stdio research server. The machine-readable pins are in [`environment/validation.json`](https://github.com/allanmarum/openplc-engineering-mcp/blob/main/environment/validation.json). They select a headless build of the OpenPLC Editor source and a real compiler smoke check.

## Frozen versions

| Component | Version or revision | Dependency record |
| --- | --- | --- |
| Host | Ubuntu 24.04 LTS, x86_64 | Host release and installed OS packages are captured per run |
| MCP interpreter | CPython 3.12.14 | `.python-version`; installed with the pinned uv binary |
| uv | 0.13.6 | Linux archive SHA-256 in `environment/validation.json` |
| Python build backend | uv_build 0.13.6 | Exact build requirement and dev dependency in `pyproject.toml` and `uv.lock` |
| MCP SDK | 2.1.1 | Existing `uv.lock`; all Python dependency versions are captured per run |
| Node.js / npm | 22.23.5 / 10.9.9 | Node archive SHA-256 in `environment/validation.json` |
| OpenPLC Editor / CLI | Source version 4.2.12, commit `f0bb418b9c3419e573dece4222558570f6a31378` | Exact Git checkout and upstream `package-lock.json` SHA-256 |
| Electron | 35.0.0, Linux x64 | Upstream npm lock plus a SHA-256-verified Electron archive |
| STruC++ | 0.6.6 | Archive SHA-256 plus `environment/strucpp/package-lock.json` for its transitive npm dependencies |
| Bundled Arduino CLI | 1.4.1 | Binary from the pinned OpenPLC commit; SHA-256 and version checked |
| Validation target | `OpenPLC Runtime v4`, compile-only | Selected in the generated project's device configuration |

The target invokes STruC++ to check ST and generate C++, configuration, and runtime bundle files. Native compilation on a Runtime v4 device and execution of control logic are outside this check. The Editor also probes its bundled Arduino CLI for this target; no Arduino core or physical device is required. Simulator/Arduino targets need their own core, library, and native-toolchain qualification.

## Confirmed upstream capabilities

The pinned [OpenPLC revision](https://github.com/Autonomy-Logic/openplc-editor/tree/f0bb418b9c3419e573dece4222558570f6a31378) contains:

- `src/cli/main.ts` and `src/cli/commands/{create,build}.ts` for headless project creation and compilation;
- `configs/webpack/webpack.config.cli.dev.ts` for the source-checkout CLI build;
- `pou-text-serializer.ts`, `data-type-serializer.ts`, and `resource-globals-serializer.ts` under `src/frontend/utils/PLC/` for current native POU, `.dt`, and resource declaration persistence;
- `src/backend/shared/firmware/hals.json` for the selected target.

Bootstrap verifies the checkout, required files, upstream package lock, and STruC++ version pin. The smoke check then creates a project through the real CLI and exercises native ST, `data.configuration.resource`, and `.dt` inspection through the MCP.

## Host prerequisites

Start with Ubuntu 24.04 x86_64, Git, a system Python 3.12 launcher, HTTPS access, and these Electron shared libraries. OS package installation is the only privileged step:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  ca-certificates git python3 libgtk-3-0t64 libnss3 libasound2t64 \
  libgbm1 libxss1 libxtst6 libatk1.0-0t64 libatk-bridge2.0-0t64 \
  libcups2t64 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
  libxfixes3 libxrandr2 libpango-1.0-0 libcairo2 libatspi2.0-0t64
```

Network sources are GitHub Git/release downloads, nodejs.org, registry.npmjs.org, and Python package/interpreter distribution hosts used by uv. Allow enough disk space for the Editor source, npm dependencies, Electron, Python, and caches; 5 GB of free space is a practical minimum for one attempt.

The recipe uses the committed upstream npm lock with lifecycle scripts disabled. It installs the verified Electron archive explicitly and uses the supplemental STruC++ lock, because upstream's postinstall resolves that package outside its main lock. Public GitHub SSH dependency URLs are resolved over HTTPS through process-local Git settings, so a clean worker needs no SSH key. No upstream source patch or unlocked npm install is part of the recipe.

## Prepare a fresh environment

Obtain this MCP repository at the revision being validated. To reproduce an archived run, check out the `mcp_commit` recorded in its `bootstrap.json`; retain a clean worktree.

From the repository root, choose a new or empty directory outside the checkout:

```bash
python3 scripts/bootstrap-validation.py --root /tmp/openplc-validation-1
```

The script downloads SHA-256-verified Node, uv, and Electron archives; installs managed CPython 3.12.14 and locked Python/npm dependencies; fetches the exact OpenPLC commit; builds its development CLI; and runs verification. Interpreter installs, caches, a dedicated virtual environment, and OpenPLC user data stay under the selected directory. Existing user settings and projects are not used.

For an isolated CI worker that cannot start Chromium's sandbox, opt in explicitly:

```bash
python3 scripts/bootstrap-validation.py \
  --root /tmp/openplc-validation-1 --electron-no-sandbox
```

That flag is recorded in the evidence and applies to the generated local wrapper. A normal desktop installation can retain the sandbox. A non-Ubuntu Linux x86_64 diagnostic run may use `--allow-unqualified-host`; its report sets `qualified_host` to false and cannot establish support for the release baseline.

The command refuses a non-empty destination. To repeat the clean installation, use another empty destination:

```bash
python3 scripts/bootstrap-validation.py \
  --root /tmp/openplc-validation-2 --electron-no-sandbox
```

Each attempt uses separate managed interpreters, virtual environments, npm/uv caches, source checkout, user data, and fixtures. The CI workflow goes further: attempts 1 and 2 execute on separate fresh Ubuntu runners.

## Run the installed components

For an environment prepared in `/tmp/openplc-validation-1`:

```bash
export PATH="/tmp/openplc-validation-1/bin:$PATH"
openplc-cli --version
/tmp/openplc-validation-1/mcp-venv/bin/openplc-engineering-mcp
```

The server's stdio is a protocol connection; connect it to the MCP client rather than entering terminal commands into it. Configure the client's command as the absolute `mcp-venv/bin/openplc-engineering-mcp` path and prepend the environment's `bin` directory to its `PATH`. The CLI wrapper fixes the Editor working directory, headless flags, and isolated user-data path. The MCP executable can be launched from a different working directory.

## Verification and evidence

Bootstrap invokes `scripts/verify-validation-environment.py` with the new virtual environment. It launches an external MCP server over stdio and checks:

1. Python/SDK, CLI, and bundled Arduino CLI versions and relevant hashes;
2. thirteen discoverable tools and a project created by the real CLI;
3. native ST content/hash and current-format type inspection;
4. successful real compilation for the configured Runtime v4 target;
5. exit code 4 for an introduced undeclared identifier, diagnostic retrieval, and successful compilation after restoring valid ST;
6. materialized C++ and bundle artifacts.

Evidence is written under `<root>/evidence/`:

- `validation-pins.json`: the input manifest;
- numbered command metadata and stdout/stderr logs: installation and build steps;
- `cli-version.*` and `cli-create.*`: actual CLI responses;
- `mcp-smoke.json`: exact Python/package versions, tool names, observable calls/results, and generated artifact hashes;
- `os-packages.txt`: installed host package versions;
- `bootstrap.json`: verdict, supported-host check, MCP commit/worktree status, sandbox mode, and lock/manifest hashes.

A qualifying attempt requires `status: pass`, `qualified_host: true`, a clean MCP worktree, and a passing smoke report. Missing evidence or a failed process is a failed attempt. The reproduction goal is the same pinned toolchain and observable compiler outcomes; OS updates and absolute build paths are recorded, so byte-identical whole-machine or generated-binary images are not claimed.

The **Reproducible Environment** workflow performs the recipe twice without dependency caches and uploads separate evidence artifacts. Its automatic checks assert the supported host and clean worktree. Use the run's exact commit and both artifacts when citing environment qualification. Artifacts are retained for 90 days; preserve the reports with the final research release for long-term evidence.

This environment check supplies installation and compiler-integration evidence. The remaining [release completion criteria](release-contract.md#completion-criteria) still require their own validation.
