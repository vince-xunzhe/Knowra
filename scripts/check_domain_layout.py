#!/usr/bin/env python3
"""Suoji layout acceptance: isolated source snapshot, mock APIs, no database/backend."""
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

from check_frontend import CheckInterrupted, interrupt, run_check

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> bytes:
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args], cwd=ROOT,
        env={"PATH": os.defpath, "GIT_OPTIONAL_LOCKS": "0"},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", help="Read-only Git revision for baseline frontend source and screenshot")
    args = parser.parse_args()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupt)
    for relative in (".cache", "frontend", "frontend/node_modules"):
        path = ROOT / relative
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise SystemExit(f"Refusing linked/outside worktree path: {relative}")
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node 24 is required")
    version = subprocess.check_output([node, "-p", "process.versions.node"], text=True).strip()
    if not ((24, 14, 1) <= tuple(map(int, version.split("."))) < (25, 0, 0)):
        raise SystemExit("Use Node 24.14.1+ within 24.x")
    candidates = [os.environ.get("KNOWRA_BROWSER_EXECUTABLE"),
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  shutil.which("chromium"), shutil.which("google-chrome")]
    browser = next((str(Path(value).resolve()) for value in candidates if value
                    and Path(value).is_absolute() and Path(value).is_file() and os.access(value, os.X_OK)), None)
    if not browser:
        raise SystemExit("Existing Chrome/Chromium is required; this check never downloads a browser")
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="domain-layout-baseline-" if args.baseline else "domain-layout-check-", dir=cache))
    # Preserve repository-relative imports such as src/lib/cloudPolicy.ts ->
    # ../../../cloud-capabilities.json, entirely inside this task's snapshot.
    snapshot = workspace / "frontend"
    snapshot.mkdir()
    files = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "frontend").decode().split("\0")
    for relative in filter(None, files):
        parts = Path(relative).parts[1:]
        if any(part.startswith(".env") or part in {".npmrc", "node_modules", "dist", "test-results", "playwright-report"} for part in parts):
            continue
        source = ROOT / relative
        if source.is_symlink():
            raise SystemExit(f"Refusing source symlink: {relative}")
        if source.is_file():
            target = snapshot.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    baseline_sha = None
    policy_name = "cloud-capabilities.json"
    policy_bytes = None
    if args.baseline:
        baseline_sha = git("rev-parse", "--verify", args.baseline + "^{commit}").decode().strip()
        # Only tracked frontend source is replaced. The latest synthetic harness stays.
        shutil.rmtree(snapshot / "src")
        baseline_files = git("ls-tree", "-rz", "--name-only", baseline_sha, "--", "frontend/src").decode().split("\0")
        for relative in filter(None, baseline_files):
            if any(part.startswith(".env") for part in Path(relative).parts):
                raise SystemExit("Refusing env-like baseline source")
            target = snapshot.joinpath(*Path(relative).parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git("show", f"{baseline_sha}:{relative}"))
        policy_entry = git("ls-tree", "-z", baseline_sha, "--", policy_name)
        if policy_entry:
            if policy_entry.split(b"\t", 1)[0].split()[0] not in {b"100644", b"100755"}:
                raise SystemExit("Refusing non-file baseline cloud capability policy")
            policy_bytes = git("show", f"{baseline_sha}:{policy_name}")
        # Old baselines predate this policy and do not import it. Never substitute
        # the current policy for an absent/different baseline version.
    else:
        policy = ROOT / policy_name
        if policy.is_symlink() or not policy.resolve().is_relative_to(ROOT):
            raise SystemExit("Refusing linked/outside worktree cloud capability policy")
        policy_bytes = policy.read_bytes()
    if policy_bytes is not None:
        (workspace / policy_name).write_bytes(policy_bytes)
    (snapshot / "node_modules").symlink_to(ROOT / "frontend/node_modules", target_is_directory=True)
    (snapshot / "tmp").mkdir()
    (snapshot / "evidence").mkdir()
    (snapshot / "build-layout.mjs").write_text('''import { build } from 'vite';
await build({root:process.cwd(),configFile:process.cwd()+'/vite.config.ts',configLoader:'native',envDir:false,
cacheDir:process.cwd()+'/.vite-cache',build:{outDir:process.cwd()+'/layout-dist',emptyOutDir:false,
rollupOptions:{input:process.cwd()+'/tests/domain-layout-harness.html'}}});
''')
    (snapshot / "network-guard.cjs").write_text('''const net = require('node:net');
const dns = require('node:dns');
const modules = require('node:module');
const allowed = process.env.KNOWRA_LAYOUT_LOOPBACK === '1';
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function(...args) {
  const first = Array.isArray(args[0]) ? args[0][0] : args[0];
  const host = typeof first === 'object' && first !== null ? first.host : args[1];
  const pipe = typeof first === 'object' && first !== null && first.path;
  if (!pipe && !(allowed && host === '127.0.0.1')) throw new Error('Layout check: network blocked');
  return connect.apply(this, args);
};
const listen = net.Server.prototype.listen;
net.Server.prototype.listen = function(...args) {
  const host = typeof args[0] === 'object' && args[0] !== null ? args[0].host : args[1];
  if (!(allowed && host === '127.0.0.1')) throw new Error('Layout check: listener blocked');
  return listen.apply(this, args);
};
const lookup = dns.lookup;
dns.lookup = function(hostname, ...args) {
  if (!(allowed && hostname === '127.0.0.1')) throw new Error('Layout check: DNS blocked');
  return lookup.call(this, hostname, ...args);
};
globalThis.fetch = () => Promise.reject(new Error('Layout check: fetch disabled'));
modules.syncBuiltinESMExports();
''')
    env = {"PATH": str(Path(node).parent) + os.pathsep + os.defpath,
           "HOME": str(snapshot / "tmp"), "TMPDIR": str(snapshot / "tmp"),
           "NODE_OPTIONS": "--require " + json.dumps(str(snapshot / "network-guard.cjs"))}
    results = []
    commands = [("build", [node, "build-layout.mjs"]), ("browser", [node, "tests/domain-layout-ui.mjs"])]
    for name, command in commands:
        command_env = dict(env)
        if name == "browser":
            command_env.update(KNOWRA_LAYOUT_LOOPBACK="1", KNOWRA_BROWSER_EXECUTABLE=browser,
                               KNOWRA_LAYOUT_BASELINE="1" if args.baseline else "0")
        output, code, interrupted = run_check(command, snapshot, command_env)
        (snapshot / (name + ".log")).write_text(output)
        results.append({"check": name, "exit_code": code})
        print(json.dumps(results[-1]), flush=True)
        if code:
            print(output[-8000:], flush=True)
            break
        if interrupted:
            break
    report = {"workspace": str(workspace), "snapshot": str(snapshot), "node": version, "python": sys.version.split()[0],
              "browser": browser, "source_head": git("rev-parse", "HEAD").decode().strip(),
              "baseline_sha": baseline_sha, "source_has_changes": bool(git("status", "--porcelain")),
              "cloud_policy": {"source": baseline_sha or "working_tree", "relative_path": policy_name,
                               "sha256": hashlib.sha256(policy_bytes).hexdigest()} if policy_bytes is not None else None,
              "isolation": {"backend_started": False, "databases_opened": False,
                            "api_responses": "in-memory synthetic mocks only", "external_requests": "blocked",
                            "source_env_files": "excluded", "browser_profile": str(snapshot / "tmp/layout-profile")},
              "results": results}
    output_path = cache / ("domain-layout-baseline-result.json" if args.baseline else "domain-layout-check-result.json")
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    print("Layout evidence: " + str(snapshot / "evidence"), flush=True)
    return int(any(item["exit_code"] for item in results))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CheckInterrupted as error:
        print(str(error), file=sys.stderr)
        sys.exit(128 + error.signum)
