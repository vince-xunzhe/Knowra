#!/usr/bin/env python3
"""Run the Web checks from a source snapshot, without local env/data files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"
FRONTEND = ROOT / "frontend"


class CheckInterrupted(Exception):
    def __init__(self, signum: int):
        self.signum = signum
        super().__init__(f"Interrupted by signal {signum}")


def interrupt(signum: int, _frame: object) -> None:
    raise CheckInterrupted(signum)


def group_alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def signal_group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def terminate_group(run: subprocess.Popen[str]) -> tuple[str, str]:
    """Stop/reap the runner group; its signal handler closes the browser.

    Playwright may put Chrome in another process group. If the runner must be
    force-killed, this function alone cannot confirm that Chrome has exited.
    """
    handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        if group_alive(run.pid):
            signal_group(run.pid, signal.SIGTERM)
        try:
            output = run.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            if group_alive(run.pid):
                signal_group(run.pid, signal.SIGKILL)
            output = run.communicate(timeout=10)
        # The direct child can exit before other members of its group finish.
        for _ in range(100):
            if not group_alive(run.pid):
                return output
            time.sleep(0.05)
        signal_group(run.pid, signal.SIGKILL)
        for _ in range(100):
            if not group_alive(run.pid):
                return output
            time.sleep(0.05)
        raise RuntimeError("Check descendants remain after termination; retain the snapshot for inspection")
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def run_check(command: list[str], snapshot: Path, env: dict[str, str]) -> tuple[str, int, bool]:
    run = subprocess.Popen(command, cwd=snapshot, env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = run.communicate(timeout=180)
        if group_alive(run.pid):
            # A successful direct child must not leave a detached test service.
            terminate_group(run)
            return stdout + stderr + "\nCheck left running descendants", 1, False
        return stdout + stderr, run.returncode, False
    except subprocess.TimeoutExpired:
        stdout, stderr = terminate_group(run)
        return stdout + stderr + "\nTimed out after 180 seconds", 124, False
    except CheckInterrupted as error:
        stdout, stderr = terminate_group(run)
        return stdout + stderr + f"\n{error}", 128 + error.signum, True
    except BaseException:
        terminate_group(run)
        raise


def main() -> int:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupt)
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
    (snapshot / "loopback.cjs").write_text('''const net = require('node:net');
const dns = require('node:dns');
const modules = require('node:module');
const originalConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function(...args) {
  const first = Array.isArray(args[0]) ? args[0][0] : args[0];
  const host = typeof first === 'object' && first !== null ? first.host : args[1];
  const pipe = typeof first === 'object' && first !== null && first.path;
  if (!pipe && host !== '127.0.0.1') throw new Error('Lifecycle check: non-loopback connection blocked');
  return originalConnect.apply(this, args);
};
const originalListen = net.Server.prototype.listen;
net.Server.prototype.listen = function(...args) {
  const first = args[0];
  const host = typeof first === 'object' && first !== null ? first.host : args[1];
  if (host !== '127.0.0.1') throw new Error('Lifecycle check: non-loopback listener blocked');
  return originalListen.apply(this, args);
};
const originalLookup = dns.lookup;
dns.lookup = function(hostname, ...args) {
  if (hostname !== '127.0.0.1') throw new Error('Lifecycle check: DNS blocked');
  return originalLookup.call(this, hostname, ...args);
};
globalThis.fetch = () => Promise.reject(new Error('Lifecycle check: fetch disabled'));
modules.syncBuiltinESMExports();
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
        ("lint", [node, "node_modules/eslint/bin/eslint.js", ".", "--max-warnings", "0", "--format", "json", "--output-file", "lint.json"]),
        ("lifecycle-types", [node, "node_modules/typescript/bin/tsc", "-b", "tests/tsconfig.lifecycle.json"]),
        ("lifecycle-build", [node, "tests/lifecycle-build.mjs"]),
        ("lifecycle-ui", [node, "tests/lifecycle-ui.mjs"]),
    ]
    # Only existing browsers are considered. Never use a Playwright download or
    # a real user profile. A missing executable fails rather than skips UI checks.
    configured_browser = os.environ.get("KNOWRA_BROWSER_EXECUTABLE")
    browser_candidates = [configured_browser] if configured_browser else [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("google-chrome"),
    ]
    browser = next((str(Path(candidate).resolve()) for candidate in browser_candidates
                    if candidate and Path(candidate).is_absolute() and Path(candidate).is_file()
                    and os.access(candidate, os.X_OK)), None)
    for name, command in commands:
        command_env = env
        if name == "lifecycle-ui":
            command_env = {**env, "NODE_OPTIONS": "--require " + json.dumps(str(snapshot / "loopback.cjs"), ensure_ascii=False)}
            if browser:
                command_env["KNOWRA_BROWSER_EXECUTABLE"] = browser
        try:
            output, code, interrupted = run_check(command, snapshot, command_env)
        except OSError as error:
            output, code, interrupted = str(error), 1, False
        (snapshot / (name + ".log")).write_text(output)
        result = {"check": name, "exit_code": code}
        if name == "lint" and (snapshot / "lint.json").is_file():
            issues = json.loads((snapshot / "lint.json").read_text())
            result["errors"] = sum(item["errorCount"] for item in issues)
            result["warnings"] = sum(item["warningCount"] for item in issues)
        if name == "lifecycle-ui" and (snapshot / "lifecycle-results.json").is_file():
            lifecycle = json.loads((snapshot / "lifecycle-results.json").read_text())
            result["tests"] = lifecycle["tests"]
            result["violations"] = lifecycle["violations"]
        results.append(result)
        print(json.dumps(result), flush=True)
        if code and name != "lint":
            print(output[-6000:], flush=True)
        if interrupted:
            break
    report = {"node": version, "browser": browser, "snapshot": str(snapshot), "results": results}
    (CACHE / "frontend-check-result.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Frontend logs: " + str(snapshot), flush=True)
    return int(any(item["exit_code"] for item in results))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CheckInterrupted as error:
        print(str(error), file=sys.stderr)
        sys.exit(128 + error.signum)
