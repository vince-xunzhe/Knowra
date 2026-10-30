#!/usr/bin/env python3
"""Build base/current React snapshots and capture synthetic index-status UI."""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"
FRONTEND = ROOT / "frontend"


def node_binary() -> Path:
    candidate = shutil.which("node")
    if not candidate:
        raise SystemExit("Node 24 is required")
    path = Path(candidate).resolve()
    version = subprocess.check_output([str(path), "-p", "process.versions.node"], text=True).strip()
    if version.split(".")[0] != "24" or tuple(map(int, version.split("."))) < (24, 14, 1):
        raise SystemExit("Use Node 24.14.1+ within 24.x")
    return path


def browser_binary() -> Path:
    configured = os.environ.get("KNOWRA_BROWSER_EXECUTABLE")
    candidates = [
        configured,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("chromium"),
        shutil.which("google-chrome"),
    ]
    for candidate in candidates:
        if candidate:
            path = Path(candidate).resolve()
            if path.is_file() and os.access(path, os.X_OK):
                return path
    raise SystemExit("An installed Chrome/Chromium is required")


def copy_current_frontend(target: Path) -> None:
    files = subprocess.check_output(
        [
            "git", "--no-optional-locks", "-c", "core.fsmonitor=false",
            "ls-files", "-z", "--cached", "--others", "--exclude-standard",
            "--", "frontend",
        ],
        cwd=ROOT,
        env={"PATH": os.defpath, "GIT_OPTIONAL_LOCKS": "0"},
    ).decode().split("\0")
    target.mkdir()
    for relative in filter(None, files):
        source = ROOT / relative
        parts = Path(relative).parts[1:]
        if any(
            part.startswith(".env")
            or part in {".npmrc", "node_modules", "dist", "lifecycle-dist"}
            for part in parts
        ):
            continue
        if source.is_symlink() or not source.is_file():
            continue
        destination = target.joinpath(*parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def extract_baseline(target: Path, ref: str) -> None:
    archive = subprocess.check_output(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "archive", ref, "frontend"],
        cwd=ROOT,
        env={"PATH": os.defpath, "GIT_OPTIONAL_LOCKS": "0"},
    )
    target.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            parts = Path(member.name).parts
            if not parts or parts[0] != "frontend" or ".." in parts:
                raise SystemExit(f"Unsafe baseline archive member: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise SystemExit(f"Refusing non-file baseline member: {member.name}")
            relative = Path(*parts[1:])
            if any(part.startswith(".env") or part == ".npmrc" for part in relative.parts):
                continue
            source = bundle.extractfile(member)
            if source is None:
                raise SystemExit(f"Cannot read baseline member: {member.name}")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read())
    shutil.copyfile(
        FRONTEND / "tests" / "wiki-index-status-screenshot.mjs",
        target / "tests" / "wiki-index-status-screenshot.mjs",
    )


def build_and_capture(
    snapshot: Path,
    workspace: Path,
    *,
    node: Path,
    browser: Path,
    variant: str,
    output: Path,
) -> dict:
    (snapshot / "node_modules").symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    (snapshot / "tmp").mkdir()
    shutil.copyfile(ROOT / "cloud-capabilities.json", workspace / "cloud-capabilities.json")
    offline = snapshot / "offline.cjs"
    offline.write_text(
        "const net=require('node:net');const dns=require('node:dns');"
        "net.Socket.prototype.connect=function(){throw new Error('offline build')};"
        "dns.lookup=()=>{throw new Error('offline build')};"
    )
    build_env = {
        "PATH": str(node.parent) + os.pathsep + os.defpath,
        "TMPDIR": str(snapshot / "tmp"),
        "NODE_OPTIONS": "--require " + json.dumps(str(offline)),
    }
    subprocess.run(
        [str(node), "tests/lifecycle-build.mjs"],
        cwd=snapshot,
        env=build_env,
        check=True,
        timeout=180,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    run_env = {
        "PATH": str(node.parent) + os.pathsep + os.defpath,
        "TMPDIR": str(snapshot / "tmp"),
        "KNOWRA_BROWSER_EXECUTABLE": str(browser),
        "KNOWRA_WIKI_INDEX_VARIANT": variant,
        "KNOWRA_WIKI_INDEX_SCREENSHOT": str(output),
    }
    subprocess.run(
        [str(node), "tests/wiki-index-status-screenshot.mjs"],
        cwd=snapshot,
        env=run_env,
        check=True,
        timeout=120,
    )
    return json.loads((output.parent / f"{variant}-browser-result.json").read_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="origin/main")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    node = node_binary()
    browser = browser_binary()
    CACHE.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wiki-index-ui-", dir=CACHE) as temporary:
        root = Path(temporary)
        baseline_workspace = root / "baseline"
        current_workspace = root / "current"
        baseline = baseline_workspace / "frontend"
        current = current_workspace / "frontend"
        baseline_workspace.mkdir()
        current_workspace.mkdir()
        extract_baseline(baseline, args.baseline)
        copy_current_frontend(current)
        before = build_and_capture(
            baseline,
            baseline_workspace,
            node=node,
            browser=browser,
            variant="before",
            output=output / "before-index-status.png",
        )
        after = build_and_capture(
            current,
            current_workspace,
            node=node,
            browser=browser,
            variant="after",
            output=output / "after-index-statuses.png",
        )
    result = {
        "baseline": args.baseline,
        "node": subprocess.check_output([str(node), "--version"], text=True).strip(),
        "browser": str(browser),
        "before": before,
        "after": after,
    }
    (output / "browser-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
