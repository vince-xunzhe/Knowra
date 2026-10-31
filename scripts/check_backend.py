#!/usr/bin/env python3
"""Run the bounded offline backend suite without opening a working library.

Only source and prompt templates are copied. Every Python worker inherits the
same filesystem/network guard through a private sitecustomize bootstrap.
This is a regression harness, not a sandbox for arbitrary untrusted code.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile


TESTS = (
    "tests.test_main_routes",
    "tests.test_task_queue",
    "tests.test_task_executor",
    "tests.test_paper_background_dispatch",
    "tests.test_postgres_safety",
    "tests.test_cloud_freeze",
    "tests.test_sync_local_router",
    "tests.test_wiki_search_fallback",
    "tests.test_wiki_index_status",
    "tests.test_ask_evidence_gate",
    "tests.test_ask_retrieval_orchestration",
    "tests.test_ask_quality_eval",
    "tests.test_paper_evidence_service",
    "tests.test_wiki_claims",
    "tests.test_hybrid_claim_retrieval",
    "tests.test_claim_support_answer",
    "tests.test_shadow_ask_e2e",
    "tests.test_ask_generation_control",
    "tests.test_ask_shadow_integration",
    "tests.test_shadow_generation",
    "tests.test_two_stage_shadow_retrieval",
)


def validate_workspace(project: Path, prefix: Path) -> Path:
    """Reject linked source/cache/env directories before any generated write."""
    project = project.resolve()
    for name in (".cache", "backend", "model_gateway", str(prefix.relative_to(project))):
        candidate = project / name
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(project):
            raise ValueError(f"{name} must be a physical directory inside this worktree")
        if candidate.exists() and not candidate.is_dir():
            raise ValueError(f"{name} must be a directory")
        if name != ".cache" and not candidate.is_dir():
            raise ValueError(f"{name} is missing; prepare this worktree first")
    if prefix.resolve() not in {(project / "backend" / name).resolve() for name in (".venv", ".venv-native-py312")}:
        raise ValueError("use this checkout's managed native environment from scripts/check.sh")
    return project / ".cache"


def install_guard() -> None:
    """Called before application imports in both the test and worker processes."""
    root = Path(os.environ["KNOWRA_OFFLINE_ROOT"]).resolve()
    scratch = root / "temporary-files"
    tempfile.tempdir = str(scratch)
    Path.home = classmethod(lambda cls: root / "home")
    read_roots = (root, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve())
    system_reads = {
        Path("/dev/null"), Path("/etc/localtime").resolve(),
        Path("/System/Library/CoreServices/SystemVersion.plist"),
    }

    def resolved(value, dir_fd=None):
        p = Path(os.fsdecode(value))
        if not p.is_absolute() and dir_fd not in (None, -1):
            if sys.platform == "darwin":
                import fcntl
                base = fcntl.fcntl(dir_fd, 50, bytes(1024)).split(b"\0", 1)[0]
                p = Path(os.fsdecode(base)) / p
            else:
                p = Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / p
        return p.resolve()

    def within(p):
        return p.is_relative_to(root)

    def reject(kind, target=None):
        with (root / "guard-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "blocked": kind, "target": str(target) if target is not None else None}) + "\n")
        raise PermissionError("Offline backend check blocked: " + kind)

    def audit(event, args):
        if event == "socket.__new__" and args[1] != 1:  # AF_UNIX only; no TCP/UDP.
            reject("IP socket")
        if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
            reject("DNS")
        if event == "sqlite3.connect":
            target = args[0]
            if target != ":memory:" and not within(resolved(target)):
                reject("database outside temporary snapshot")
        if event == "subprocess.Popen":
            executable, argv, cwd, child_env = args
            child_env = os.environ if child_env is None else child_env
            if (Path(executable).resolve() != Path(sys.executable).resolve()
                    or any(flag in argv for flag in ("-I", "-S", "-E"))
                    or child_env.get("KNOWRA_OFFLINE_ROOT") != str(root)
                    or child_env.get("PYTHONPATH") != os.environ["PYTHONPATH"]
                    or (cwd is not None and not within(resolved(cwd)))):
                reject("unguarded subprocess")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            filename, mode, flags = args
            p = resolved(filename)
            if p == Path("/dev/null"):
                return
            writing = ((isinstance(mode, str) and any(c in mode for c in "wax+"))
                       or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
            if writing and not within(p):
                reject("write outside temporary snapshot")
            if p.name.startswith(".env") or (not within(p) and not any(p.is_relative_to(r) for r in read_roots) and p not in system_reads):
                reject("read outside source/runtime allowlist", p)
        if event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename"}:
            if event == "os.rename":
                paths = ((args[0], args[2]), (args[1], args[3]))
            elif event == "os.mkdir":
                paths = ((args[0], args[2]),)
            else:
                paths = ((args[0], args[1]),)
            for value, dir_fd in paths:
                if not within(resolved(value, dir_fd)):
                    reject("mutation outside temporary snapshot")

    sys.addaudithook(audit)
    (root / f"guard-ready-{os.getpid()}").touch()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Keep this run's isolated snapshot for debugging")
    parser.add_argument("--timeout", type=int, default=120, help="Maximum suite runtime in seconds")
    options = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error("use this checkout's managed Python 3.12 environment from scripts/check.sh")
    if options.timeout < 1:
        parser.error("--timeout must be positive")
    project = Path(__file__).resolve().parent.parent
    try:
        cache = validate_workspace(project, Path(sys.prefix))
    except ValueError as error:
        parser.error(str(error))
    cache.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="backend-check-", dir=cache)).resolve()
    try:
        policy = project / "cloud-capabilities.json"
        if policy.is_symlink():
            raise ValueError("cloud-capabilities.json must be a physical source file")
        shutil.copyfile(policy, root / policy.name)
        # Avoid recursing into virtual environments, caches or real runtime data.
        for folder in ("backend", "model_gateway"):
            source_root = project / folder
            for parent, dirs, files in os.walk(source_root):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"__pycache__", "node_modules", "data", "venv"}]
                for filename in files:
                    source = Path(parent) / filename
                    relative = source.relative_to(project)
                    is_source = source.suffix == ".py"
                    is_prompt = "prompt_locales" in relative.parts and source.suffix == ".md"
                    is_test_fixture = relative == Path(
                        "backend/tests/fixtures/ask_quality_eval_v1.json"
                    )
                    if source.is_symlink() or not (is_source or is_prompt or is_test_fixture):
                        continue
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
        for folder in ("temporary-files", "home", "data", "guard"):
            (root / folder).mkdir()
        # Prefer these tests over any unrelated installed package named tests.
        (root / "backend" / "tests" / "__init__.py").touch()
        shutil.copyfile(__file__, root / "guard" / "check_backend.py")
        (root / "guard" / "sitecustomize.py").write_text(
            "import os\ntry:\n from check_backend import install_guard\n install_guard()\n"
            "except BaseException:\n os._exit(97)\n", encoding="utf-8")
        # No inherited provider keys, config paths, proxies, PG URLs or user site.
        environment = {
            "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
            "PYTHONPATH": os.pathsep.join((str(root / "guard"), str(root / "backend"), str(root))),
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(root / "temporary-files"), "PYTHONUNBUFFERED": "1",
            "KNOWRA_OFFLINE_ROOT": str(root), "KNOWRA_DEPLOY_MODE": "local",
            "KNOWRA_WORKER_MODE": "external", "KNOWRA_TASK_DB": str(root / "data" / "tasks.db"),
        }
        print("Offline backend snapshot:", root, flush=True)
        process = subprocess.Popen([sys.executable, "-m", "unittest", "-v", *TESTS],
                                   cwd=root / "backend", env=environment, start_new_session=True)
        try:
            result = process.wait(timeout=options.timeout)
        except subprocess.TimeoutExpired:
            print("Backend checks exceeded the timeout", file=sys.stderr)
            result = 124
        finally:
            # This group belongs only to this invocation and its synthetic workers.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        if not list(root.glob("guard-ready-*")):
            print("Guard bootstrap did not start; checks are invalid", file=sys.stderr)
            return 1
        if (root / "guard-events.jsonl").exists():
            print("Isolation guard blocked an operation; inspect the kept snapshot", file=sys.stderr)
            result = result or 1
        print("Offline backend checks:", "PASS" if result == 0 else "FAIL", flush=True)
        return result
    finally:
        if options.keep:
            print("Kept isolated snapshot:", root, flush=True)
        else:
            shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
