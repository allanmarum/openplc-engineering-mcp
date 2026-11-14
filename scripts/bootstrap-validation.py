#!/usr/bin/env python3
"""Prepare a fresh, pinned OpenPLC/MCP environment without changing user settings."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "environment" / "validation.json"


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def download(spec: dict, destination: Path) -> None:
    print(f"Download {destination.name}", flush=True)
    with urllib.request.urlopen(spec["url"], timeout=180) as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)
    if sha256(destination) != spec["sha256"]:
        raise RuntimeError(f"SHA-256 mismatch: {destination.name}")


def run(args: list[str], cwd: Path, env: dict, evidence: Path, timeout: int = 1200) -> str:
    number = len(list(evidence.glob("*.stdout.txt"))) + 1
    prefix = evidence / f"{number:02d}-{Path(args[0]).name}"
    print("Run: " + shlex.join(args), flush=True)
    started = time.monotonic()
    with (
        prefix.with_suffix(".stdout.txt").open("w") as stdout,
        prefix.with_suffix(".stderr.txt").open("w") as stderr,
    ):
        process = subprocess.Popen(
            args, cwd=cwd, env=env, stdout=stdout, stderr=stderr, start_new_session=True
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise RuntimeError(f"Timed out: {shlex.join(args)}") from None
    prefix.with_suffix(".command.json").write_text(
        json.dumps(
            {
                "args": args,
                "cwd": str(cwd),
                "exit_code": code,
                "seconds": round(time.monotonic() - started, 3),
            },
            indent=2,
        )
        + "\n"
    )
    if code:
        raise RuntimeError(
            prefix.with_suffix(".stderr.txt").read_text()[-6000:]
            + prefix.with_suffix(".stdout.txt").read_text()[-6000:]
        )
    return prefix.with_suffix(".stdout.txt").read_text().strip()


def prepare(root: Path, allow_host: bool, no_sandbox: bool) -> None:
    spec = json.loads(MANIFEST.read_text())
    host = platform.freedesktop_os_release()
    qualified_host = (
        host.get("ID") == spec["platform"]["id"]
        and host.get("VERSION_ID") == spec["platform"]["version_id"]
        and platform.machine() == spec["platform"]["architecture"]
    )
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("This recipe requires Linux x86_64")
    if not qualified_host and not allow_host:
        raise RuntimeError("Ubuntu 24.04 x86_64 required; --allow-unqualified-host is for diagnostics only")
    if root == REPO or root.is_relative_to(REPO) or (root.exists() and any(root.iterdir())):
        raise RuntimeError("--root must be a new or empty directory outside the MCP repository")
    if not shutil.which("git"):
        raise RuntimeError("Install Git before running this recipe")
    if sha256(REPO / "uv.lock") != spec["uv_lock_sha256"]:
        raise RuntimeError("uv.lock differs from the qualified dependency baseline")
    root.mkdir(parents=True, exist_ok=True)
    evidence = root / "evidence"
    evidence.mkdir()
    (evidence / "validation-pins.json").write_text(MANIFEST.read_text())
    env = dict(os.environ)
    env.update(
        {
            "UV_CACHE_DIR": str(root / "uv-cache"),
            "UV_PYTHON_INSTALL_DIR": str(root / "python"),
            "UV_PROJECT_ENVIRONMENT": str(root / "mcp-venv"),
            "UV_LINK_MODE": "copy",
            "GIT_TERMINAL_PROMPT": "0",
            "npm_config_cache": str(root / "npm-cache"),
            "npm_config_userconfig": str(root / "npmrc"),
            "npm_config_registry": "https://registry.npmjs.org/",
            "XDG_CONFIG_HOME": str(root / "xdg-config"),
            "XDG_CACHE_HOME": str(root / "xdg-cache"),
            "XDG_DATA_HOME": str(root / "xdg-data"),
        }
    )
    (root / "npmrc").write_text("")
    # The upstream npm lock includes a public Git dependency with an SSH URL.
    # Resolve it over HTTPS for clean workers, without changing user Git config.
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    for offset, prefix in enumerate(("ssh://git@github.com/", "git@github.com:")):
        env[f"GIT_CONFIG_KEY_{count + offset}"] = "url.https://github.com/.insteadOf"
        env[f"GIT_CONFIG_VALUE_{count + offset}"] = prefix
    env["GIT_CONFIG_COUNT"] = str(count + 2)
    for name in ("node", "uv"):
        archive = root / Path(spec[name]["url"]).name
        download(spec[name], archive)
        with tarfile.open(archive) as bundle:
            bundle.extractall(root, filter="data")
    node_bin = root / f"node-v{spec['node']['version']}-linux-x64" / "bin"
    uv = root / "uv-x86_64-unknown-linux-gnu" / "uv"
    env["PATH"] = str(node_bin) + os.pathsep + env["PATH"]
    if run([str(node_bin / "node"), "--version"], root, env, evidence) != "v" + spec["node"]["version"]:
        raise RuntimeError("Unexpected Node version")
    if run([str(node_bin / "npm"), "--version"], root, env, evidence) != spec["node"]["npm_version"]:
        raise RuntimeError("Unexpected npm version")
    run([str(uv), "--version"], root, env, evidence)
    run([str(uv), "python", "install", "--no-bin", spec["python"]], root, env, evidence)
    python = run([str(uv), "python", "find", "--managed-python", spec["python"]], root, env, evidence)
    run([str(uv), "sync", "--locked", "--project", str(REPO), "--python", python], root, env, evidence)

    upstream = root / "openplc-editor"
    run(["git", "init", str(upstream)], root, env, evidence)
    run(["git", "remote", "add", "origin", spec["openplc"]["repository"]], upstream, env, evidence)
    run(["git", "fetch", "--depth", "1", "origin", spec["openplc"]["commit"]], upstream, env, evidence)
    run(["git", "checkout", "--detach", "FETCH_HEAD"], upstream, env, evidence)
    if run(["git", "rev-parse", "HEAD"], upstream, env, evidence) != spec["openplc"]["commit"]:
        raise RuntimeError("Wrong OpenPLC revision")
    if sha256(upstream / "package-lock.json") != spec["openplc"]["package_lock_sha256"]:
        raise RuntimeError("Wrong OpenPLC dependency lock")
    for path in spec["openplc"]["required_files"]:
        if not (upstream / path).is_file():
            raise RuntimeError(f"Required upstream capability missing: {path}")
    versions = json.loads((upstream / "binary-versions.json").read_text())
    if versions["strucpp"]["version"] != "v" + spec["strucpp"]["version"]:
        raise RuntimeError("Upstream STruC++ pin differs")
    run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], upstream, env, evidence)

    # Upstream postinstall uses an unlocked npm install for STruC++. Install its
    # supplemental dependency lock separately, leaving the upstream lock untouched.
    strucpp = root / "strucpp"
    strucpp.mkdir()
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(REPO / "environment" / "strucpp" / name, strucpp / name)
    archive = root / "strucpp.tgz"
    download(spec["strucpp"], archive)
    run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], strucpp, env, evidence)
    (upstream / "node_modules" / "strucpp").symlink_to(
        strucpp / "node_modules" / "strucpp", target_is_directory=True
    )
    electron = root / "electron.zip"
    download(spec["electron"], electron)
    dist = upstream / "node_modules" / "electron" / "dist"
    dist.mkdir()
    with zipfile.ZipFile(electron) as bundle:
        for member in bundle.infolist():
            target = (dist / member.filename).resolve()
            if not target.is_relative_to(dist.resolve()):
                raise RuntimeError("Electron archive path escapes destination")
            bundle.extract(member, dist)
            if not member.is_dir():
                target.chmod((member.external_attr >> 16) & 0o777 or 0o644)
    executable = dist / "electron"
    executable.chmod(0o755)
    (dist.parent / "path.txt").write_text("electron")
    run(["npm", "run", "build:cli:dev"], upstream, env, evidence)
    if run(["git", "diff", "--name-only"], upstream, env, evidence):
        raise RuntimeError("Setup modified tracked OpenPLC source files")

    binary = root / "bin"
    binary.mkdir(exist_ok=True)
    flags = [str(executable), "--ozone-platform=headless", "--disable-gpu"]
    if no_sandbox:
        flags.append("--no-sandbox")
    flags += [str(upstream / "openplc-cli.dev.js"), "--cli", "--user-data", str(root / "user-data")]
    wrapper = binary / "openplc-cli"
    wrapper.write_text(
        "#!/bin/sh\ncd " + shlex.quote(str(upstream)) + "\nexec " + shlex.join(flags) + ' "$@"\n'
    )
    wrapper.chmod(0o755)
    env["PATH"] = str(binary) + os.pathsep + env["PATH"]
    run(
        [
            str(root / "mcp-venv" / "bin" / "python"),
            str(REPO / "scripts" / "verify-validation-environment.py"),
            "--root",
            str(root),
        ],
        REPO,
        env,
        evidence,
    )
    packages = subprocess.run(["dpkg-query", "-W"], capture_output=True, text=True, check=False)
    (evidence / "os-packages.txt").write_text(packages.stdout)
    (evidence / "bootstrap.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "qualified_host": qualified_host,
                "host": host,
                "architecture": platform.machine(),
                "electron_no_sandbox": no_sandbox,
                "mcp_commit": run(["git", "rev-parse", "HEAD"], REPO, env, evidence),
                "mcp_worktree_dirty": bool(run(["git", "status", "--porcelain"], REPO, env, evidence)),
                "manifest_sha256": sha256(MANIFEST),
                "uv_lock_sha256": sha256(REPO / "uv.lock"),
                "openplc_package_lock_sha256": sha256(upstream / "package-lock.json"),
                "strucpp_package_lock_sha256": sha256(strucpp / "package-lock.json"),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"PASS: evidence in {evidence}; CLI in {binary}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--allow-unqualified-host", action="store_true")
    parser.add_argument(
        "--electron-no-sandbox",
        action="store_true",
        help="Explicit opt-in for isolated workers without Chromium user namespaces",
    )
    options = parser.parse_args()
    try:
        prepare(
            options.root.expanduser().resolve(), options.allow_unqualified_host, options.electron_no_sandbox
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error
