#!/usr/bin/env python3
"""Cloud-freeze acceptance using source-only snapshots and synthetic browsers.

There is no backend process, database connection, real config or data copy.
The Node runner owns an ephemeral loopback static listener throughout its life;
every business API is intercepted in the browser, and all external requests fail.
This script never discovers or sends requests to an existing application's port.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import check_native_start as native

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "1b61ecf66634642ee8ad4f800c10313fee74e0c3"
HARNESS_FILES = (
    "frontend/tests/cloud-freeze-harness.html",
    "frontend/tests/cloud-freeze-harness.tsx",
    "frontend/tests/cloud-freeze-build.mjs",
    "frontend/tests/cloud-freeze-ui.mjs",
    "frontend/tests/tsconfig.cloud-freeze.json",
)
EXCLUDED = {"node_modules", "data", ".cache", "dist", "test-results", "playwright-report", ".npmrc"}


def git(*args: str) -> bytes:
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
        cwd=ROOT, env={"PATH": os.defpath, "GIT_OPTIONAL_LOCKS": "0"},
    )


def safe_source(relative: str) -> bool:
    path = Path(relative)
    return (path.parts[0] == "frontend" and not path.is_absolute()
            and not any(part in EXCLUDED or part in {"..", ".git"} or part.startswith(".env") for part in path.parts))


def prepare(snapshot: Path, *, before: bool) -> dict[str, str]:
    """Copy only Git-listed Web source; dependencies are this worktree's own."""
    if before:
        paths = git("ls-tree", "-r", "--name-only", "-z", BASELINE, "--", "frontend").decode().split("\0")
    else:
        paths = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "frontend").decode().split("\0")
    hashes = {}
    for relative in filter(None, paths):
        if not safe_source(relative):
            continue
        source = ROOT / relative
        if source.is_symlink():
            raise RuntimeError("Refusing symlinked source: " + relative)
        data = git("show", f"{BASELINE}:{relative}") if before else source.read_bytes()
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        hashes[relative] = hashlib.sha256(data).hexdigest()
    # Both builds use exactly the same current harness and synthetic values.
    for relative in (*HARNESS_FILES, "cloud-capabilities.json"):
        source = ROOT / relative
        if source.is_symlink():
            raise RuntimeError("Refusing symlinked harness or policy")
        data = source.read_bytes()
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[relative] = hashlib.sha256(data).hexdigest()
    dependencies = ROOT / "frontend/node_modules"
    if dependencies.is_symlink() or not dependencies.resolve().is_relative_to(ROOT) or not dependencies.is_dir():
        raise RuntimeError("Prepare independent worktree frontend dependencies first")
    for parent, directories, files in os.walk(dependencies, followlinks=False):
        for name in (*directories, *files):
            entry = Path(parent) / name
            if entry.is_symlink() and not entry.resolve().is_relative_to(dependencies):
                raise RuntimeError("Refusing dependency link outside this worktree's node_modules")
    # A physical snapshot avoids writing build state into the source environment.
    shutil.copytree(dependencies, snapshot / "frontend/node_modules", symlinks=True,
                    ignore=shutil.ignore_patterns(".cache", ".vite", ".tmp"))
    for name in ("tsconfig.app.json", "tsconfig.node.json"):
        path = snapshot / "frontend" / name
        path.write_text(path.read_text().replace("./node_modules/.tmp/", "./.tsbuildinfo/"))
    for name in (".cache", ".cache/home", ".cache/tmp", "frontend/tmp"):
        (snapshot / name).mkdir(parents=True, exist_ok=True)
    (snapshot / ".cache/loopback.cjs").write_text(native.NODE_GUARD)
    (snapshot / ".cache/offline.cjs").write_text("""const net = require('node:net');
const dns = require('node:dns');
const modules = require('node:module');
const original = net.Socket.prototype.connect;
net.Socket.prototype.connect = function(...args) {
  const first = Array.isArray(args[0]) ? args[0][0] : args[0];
  if (!(typeof first === 'object' && first !== null && first.path)) throw new Error('Cloud freeze build: network blocked');
  return original.apply(this, args);
};
dns.lookup = () => { throw new Error('Cloud freeze build: DNS blocked'); };
globalThis.fetch = () => Promise.reject(new Error('Cloud freeze build: fetch blocked'));
modules.syncBuiltinESMExports();
""")
    return hashes


def run(command: list[str], snapshot: Path, environment: dict[str, str], name: str, timeout: int) -> dict:
    output = snapshot / ".cache" / (name + ".log")
    owned = set()
    forced = False
    interrupted = None
    with output.open("w") as log:
        child = subprocess.Popen(command, cwd=snapshot / "frontend", env=environment,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        owned.add(child.pid)
        deadline = time.monotonic() + timeout
        try:
            while child.poll() is None:
                owned.update(native.descendants(child.pid))
                if time.monotonic() > deadline:
                    raise TimeoutError(name + " exceeded its isolated time limit")
                time.sleep(.1)
        except BaseException as error:
            interrupted = str(error)
            # Only this owned child's process group; never discover/stop users'
            # listeners or workers. Recovery cleanup still makes the run fail.
            forced = True
            child.send_signal(signal.SIGTERM)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
        finally:
            owned.update(native.descendants(child.pid))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(native.alive(pid) for pid in owned):
        time.sleep(.1)
    remaining = [pid for pid in sorted(owned) if native.alive(pid)]
    unverified = []
    if remaining:
        forced = True
        for pid in remaining:
            if native.owned_alive(pid, snapshot):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                unverified.append(pid)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(native.alive(pid) for pid in remaining):
            time.sleep(.1)
        for pid in remaining:
            # Revalidate after waiting: a recorded PID may have been recycled.
            if native.owned_alive(pid, snapshot):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(native.alive(pid) for pid in remaining):
            time.sleep(.1)
        remaining = [pid for pid in remaining if native.alive(pid)]
    result = {"check": name, "exit_code": child.returncode, "recovery_cleanup": forced,
              "remaining_owned_process_count": len(remaining),
              "unverified_process_count": len(unverified)}
    if interrupted:
        result["error"] = interrupted
    if forced or remaining:
        result["exit_code"] = result["exit_code"] or 1
    if result["exit_code"]:
        # Local diagnostics only. Published evidence is the concise JSON report,
        # which deliberately omits environment paths and browser storage values.
        print(output.read_text()[-6000:], file=sys.stderr)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", action="store_true", help="Also capture baseline UI with synthetic responses")
    parser.add_argument("--browser", default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    options = parser.parse_args()
    def interrupted(signum, _frame):
        raise InterruptedError(f"Cloud freeze acceptance interrupted ({signum})")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)
    node = os.environ.get("NODE_BIN") or shutil.which("node")
    if not node or not Path(options.browser).is_file():
        parser.error("Installed Node 24 and Chrome are required; nothing is downloaded")
    node = str(Path(node).resolve())
    version = subprocess.check_output([node, "-p", "process.versions.node"], text=True,
                                     env={"PATH": os.defpath}).strip()
    if version.split(".")[0] != "24" or tuple(map(int, version.split("."))) < (24, 14, 1):
        parser.error("Use Node 24.14.1+ within 24.x")
    cache = ROOT / ".cache"
    for path in (cache, ROOT / "frontend"):
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            parser.error("Refusing linked/outside source or cache")
    cache.mkdir(exist_ok=True)
    task = Path(tempfile.mkdtemp(prefix="cloud-freeze-", dir=cache))
    report = {"node": version, "baseline": BASELINE, "source_head": git("rev-parse", "HEAD").decode().strip(),
              "boundary": "Only source and worktree dependencies copied; no backend, database, .env, research files or existing service contacted; all business APIs mocked; private browser profile and owned ephemeral loopback listener",
              "runs": [], "exit_code": 1}
    try:
        for mode in (["before", "after"] if options.before else ["after"]):
            snapshot = task / mode
            snapshot.mkdir()
            hashes = prepare(snapshot, before=mode == "before")
            entry = {"mode": mode, "source_sha256": hashes, "checks": []}
            report["runs"].append(entry)
            environment = {"PATH": str(Path(node).parent) + os.pathsep + os.defpath,
                           "HOME": str(snapshot / ".cache/home"), "TMPDIR": str(snapshot / ".cache/tmp"),
                           "KNOWRA_SMOKE_ROOT": str(snapshot),
                           "NODE_OPTIONS": "--require " + json.dumps(str(snapshot / ".cache/offline.cjs"))}
            for name, command in (("types", [node, "node_modules/typescript/bin/tsc", "-b", "tests/tsconfig.cloud-freeze.json"]),
                                  ("build", [node, "tests/cloud-freeze-build.mjs"])):
                result = run(command, snapshot, environment, name, 180)
                entry["checks"].append(result)
                if result["exit_code"]:
                    raise RuntimeError(mode + " " + name + " failed")
            browser_env = {**environment, "NODE_OPTIONS": "--require " + json.dumps(str(snapshot / ".cache/loopback.cjs")),
                           "KNOWRA_BROWSER_EXECUTABLE": options.browser}
            result = run([node, "tests/cloud-freeze-ui.mjs", mode], snapshot, browser_env, "browser", 180)
            entry["checks"].append(result)
            evidence = snapshot / "frontend/cloud-freeze-evidence/browser-result.json"
            if evidence.is_file():
                entry["browser"] = json.loads(evidence.read_text())
            violations = native.read_json_lines(snapshot / ".cache/violations.jsonl")
            entry["node_boundary_violation_count"] = len(violations)
            if result["exit_code"] or violations:
                raise RuntimeError(mode + " browser acceptance failed")
        report["exit_code"] = 0
    except BaseException as error:
        report["error"] = str(error)
    report_path = task / "acceptance.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    (cache / "cloud-freeze-result.json").write_text(json.dumps({"evidence_directory": str(task), **report}, indent=2, ensure_ascii=False) + "\n")
    print("Cloud freeze evidence: " + str(report_path), flush=True)
    if report.get("error"):
        print(report["error"], file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
