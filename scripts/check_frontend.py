#!/usr/bin/env python3
"""Run the Web checks from a source snapshot, without local env/data files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"
FRONTEND = ROOT / "frontend"


def main() -> int:
    for path in (CACHE, FRONTEND, FRONTEND / "node_modules"):
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise SystemExit("Refusing a linked/outside worktree dependency or cache directory")
    CACHE.mkdir(exist_ok=True)
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node 24 is required")
    version = subprocess.check_output([node, "-p", "process.versions.node"], text=True).strip()
    if not (tuple(map(int, version.split("."))) >= (24, 14, 1) and version.split(".")[0] == "24"):
        raise SystemExit("Use Node 24.14.1+ within 24.x")
    files = subprocess.check_output(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "frontend"],
        cwd=ROOT,
        env={"PATH": os.defpath, "GIT_OPTIONAL_LOCKS": "0"},
    ).decode().split("\0")
    results = []
    # Keep logs only in this ignored worktree cache. No root .env is copied.
    snapshot = Path(tempfile.mkdtemp(prefix="frontend-check-", dir=CACHE))
    for relative in filter(None, files):
        source = ROOT / relative
        parts = Path(relative).parts[1:]
        if any(part.startswith(".env") or part in {".npmrc", "node_modules", "dist", "test-results", "playwright-report"} for part in parts):
            continue
        if source.is_symlink():
            raise SystemExit(f"Refusing source symlink: {relative}")
        if not source.is_file():
            continue
        target = snapshot.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (snapshot / "node_modules").symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    (snapshot / "tmp").mkdir()
    for name in ("tsconfig.app.json", "tsconfig.node.json"):
        path = snapshot / name
        # Cache placement only; retain every checked-in type-checking option.
        path.write_text(path.read_text().replace("./node_modules/.tmp/", "./.tsbuildinfo/"))
    (snapshot / "offline.cjs").write_text('''const net = require('node:net');
const dns = require('node:dns');
const modules = require('node:module');
const original = net.Socket.prototype.connect;
net.Socket.prototype.connect = function(...args) {
  const first = Array.isArray(args[0]) ? args[0][0] : args[0];
  if (!(typeof first === 'object' && first !== null && first.path)) throw new Error('Offline Web check: network disabled');
  return original.apply(this, args);
};
dns.lookup = () => { throw new Error('Offline Web check: DNS disabled'); };
globalThis.fetch = () => Promise.reject(new Error('Offline Web check: fetch disabled'));
modules.syncBuiltinESMExports();
''')
    (snapshot / "build-check.mjs").write_text('''import { build } from './node_modules/vite/dist/node/index.js';
await build({root:process.cwd(),configFile:process.cwd()+'/vite.config.ts',configLoader:'native',envDir:false,cacheDir:process.cwd()+'/.vite-cache',build:{outDir:process.cwd()+'/dist',emptyOutDir:false}});
''')
    env = {
        "PATH": str(Path(node).parent) + os.pathsep + os.defpath,
        "TMPDIR": str(snapshot / "tmp"),
        "NODE_OPTIONS": "--require " + json.dumps(str(snapshot / "offline.cjs"), ensure_ascii=False),
    }
    commands = [
        ("unit", [node, "--test", "tests/polling.test.mjs"]),
        ("types", [node, "node_modules/typescript/bin/tsc", "-b"]),
        ("build", [node, "build-check.mjs"]),
        ("lint", [node, "node_modules/eslint/bin/eslint.js", ".", "--format", "json", "--output-file", "lint.json"]),
    ]
    for name, command in commands:
        try:
            run = subprocess.run(command, cwd=snapshot, env=env, text=True, capture_output=True, timeout=180)
            output = run.stdout + run.stderr
            code = run.returncode
        except subprocess.TimeoutExpired:
            output, code = "Timed out after 180 seconds", 124
        (snapshot / (name + ".log")).write_text(output)
        result = {"check": name, "exit_code": code}
        if name == "lint" and (snapshot / "lint.json").is_file():
            issues = json.loads((snapshot / "lint.json").read_text())
            result["errors"] = sum(item["errorCount"] for item in issues)
            result["warnings"] = sum(item["warningCount"] for item in issues)
        results.append(result)
        print(json.dumps(result), flush=True)
        if code and name != "lint":
            print(output[-6000:], flush=True)
    report = {"node": version, "snapshot": str(snapshot), "results": results}
    (CACHE / "frontend-check-result.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Frontend logs: " + str(snapshot), flush=True)
    return int(any(item["exit_code"] for item in results))


if __name__ == "__main__":
    sys.exit(main())
