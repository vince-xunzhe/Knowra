#!/usr/bin/env python3
"""Native preflight and explicit install records; stdlib, bootstrap Python 3.9+."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

CANONICAL = "backend/.venv"
NATIVE = "backend/.venv-native-py312"
LOCKS = ("backend/requirements-dev.lock", "frontend/package-lock.json")
INSTALL = "Run ./scripts/install-native.sh to prepare this checkout without replacing legacy environments."


def fail(message):
    raise ValueError(message)


def paths(root):
    for relative in ("backend", CANONICAL, NATIVE, "frontend", "frontend/node_modules", ".cache", ".cache/uv", ".cache/npm", ".cache/npm-user.npmrc", ".cache/npm-global.npmrc", ".cache/native-install.json"):
        p = root / relative
        if p.is_symlink() or not p.resolve().is_relative_to(root):
            fail("Refusing linked/outside checkout path: " + relative)
        if p.is_file() and p.stat().st_nlink != 1:
            fail("Refusing shared file: " + relative)
    for directory in (CANONICAL, NATIVE):
        for suffix in ("bin", "pyvenv.cfg"):
            p = root / directory / suffix
            if p.is_symlink() or (p.is_file() and p.stat().st_nlink != 1):
                fail("Refusing linked/shared environment layout: " + str(p.relative_to(root)))


def record(root):
    marker = root / ".cache/native-install.json"
    if not marker.exists():
        return None
    value = json.loads(marker.read_text())
    if not isinstance(value, dict) or value.get("venv", CANONICAL) not in (CANONICAL, NATIVE) or value.get("status", "ready") not in ("ready", "installing"):
        fail("Invalid installation record; inspect .cache/native-install.json without deleting existing environments.")
    return value


def python_version(executable):
    try:
        result = subprocess.run([str(executable), "-I", "-S", "-c", "import sys; print('.'.join(map(str,sys.version_info[:3])))"], capture_output=True, text=True, timeout=10)
        version = tuple(map(int, result.stdout.strip().split(".")))
        if result.returncode or len(version) != 3:
            raise ValueError()
        return version
    except (OSError, ValueError, subprocess.TimeoutExpired):
        fail("Cannot inspect Python at {}. Set PYTHON_BIN to an installed Python 3.12.13+ executable (within 3.12.x).".format(executable))


def supported(version):
    return version[:2] == (3, 12) and version >= (3, 12, 13)


def require_python(executable):
    version = python_version(executable)
    if not supported(version):
        fail("Found Python {} at {}; requires Python 3.12.13+ within 3.12.x. {}".format(".".join(map(str, version)), executable, INSTALL))
    return version


def select(root):
    paths(root)
    saved = record(root)
    if saved:
        candidate = root / saved.get("venv", CANONICAL)
        exe = candidate / "bin/python"
        if saved.get("status") == "installing" and not exe.is_file():
            return candidate
        if not candidate.exists() or (exe.is_file() and supported(python_version(exe))):
            return candidate
        if candidate == root / NATIVE:
            fail("Recorded native environment is incompatible: {}. Preserve it and use a fresh worktree.".format(candidate))
    if not (root / CANONICAL).exists():
        return root / CANONICAL
    target = root / NATIVE
    if target.exists() and (not saved or saved.get("venv") != NATIVE):
        fail("Unmanaged environment already exists at {}. It will not be overwritten; inspect it or use a fresh worktree.".format(target))
    return target


def installed(root):
    target = select(root)
    saved = record(root)
    if not (target / "bin/python").is_file():
        legacy = root / CANONICAL / "bin/python"
        detail = " Existing {} uses Python {} and will be preserved.".format(legacy, ".".join(map(str, python_version(legacy)))) if legacy.is_file() else ""
        fail("No managed native environment at {}.{} {}".format(target, detail, INSTALL))
    require_python(target / "bin/python")
    if not saved or saved.get("venv", CANONICAL) != str(target.relative_to(root)) or saved.get("status", "ready") != "ready":
        fail("Installation is missing or incomplete for {}. {}".format(target, INSTALL))
    if Path(sys.prefix).resolve() != target.resolve():
        fail("Selected interpreter does not belong to this checkout: " + str(target))
    for relative in LOCKS:
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != saved.get(relative):
            fail(relative + " changed. " + INSTALL)
    if not (root / "frontend/node_modules/.package-lock.json").is_file():
        fail("Frontend dependencies are missing. " + INSTALL)


def write_record(root, target, status, version):
    paths(root)
    if target != select(root):
        fail("Environment selection changed during installation; inspect the checkout.")
    values = {"status": status, "venv": str(target.relative_to(root)), "python_version": version}
    if status == "ready":
        require_python(target / "bin/python")
        if Path(sys.prefix).resolve() != target.resolve():
            fail("Cannot mark a different environment as installed")
        values.update({p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in LOCKS})
    marker = root / ".cache/native-install.json"
    marker.parent.mkdir(exist_ok=True)
    marker.write_text(json.dumps(values, indent=2) + "\n")


def owner(root, target):
    if target not in (root / CANONICAL, root / NATIVE) or Path(sys.prefix).resolve() != target.resolve():
        fail("Interpreter belongs to a different environment; no dependencies were modified: " + str(target))


def confirm_existing_data(root):
    if os.environ.get("KNOWRA_ALLOW_EXISTING_DATA") == "1":
        return
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        fail("Existing data detected. No database was opened. Run ./start.sh in an interactive terminal to review and confirm this launch. For an explicitly approved unattended launch only, use KNOWRA_ALLOW_EXISTING_DATA=1 ./start.sh.")
    print(
        "\nKnowra: Start with existing data? / 使用已有数据启动\n"
        + "Data directory: " + str(root / "data") + "\n"
        + "应用会读取本地配置；启动可能更新数据库 schema、回填数据，清理历史 finding 节点，\n"
        + "合并／修复记录和边、更新论文记录，并重建 wiki 索引。\n"
        + "独立 worker 可能恢复旧队列／定时任务并调用外部服务；关闭启动器不保证停止它们。\n"
        + "目前尚未打开数据库；脚本不会自动备份。需要先备份时请取消，参阅 INSTALL.zh.md。\n"
        + "本次确认只适用于当前启动，不会保存为永久许可。\n"
        + "Type START to continue, or press Enter to cancel / 输入 START 启动，回车取消: ",
        end="", file=sys.stderr, flush=True,
    )
    try:
        answer = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip() != "START":
        fail("Startup cancelled. No database was opened and no services were started.")
    print("Existing-data permission confirmed for this launch only.", file=sys.stderr)


def startup(root, values, *, check_only=False):
    try:
        ports = [int(value) for value in values]
    except ValueError:
        fail("BACKEND_PORT and FRONTEND_PORT must be integer ports.")
    if len(ports) != 2 or len(set(ports)) != 2 or not all(1024 <= p <= 65535 for p in ports):
        fail("Choose two distinct ports between 1024 and 65535.")
    for relative in ("data", "data/papers", "data/artifacts", "data/wiki", "data/config.json", "data/knowledge.db", "data/tasks.db", "data/knowledge.db-wal", "data/knowledge.db-shm", "data/tasks.db-wal", "data/tasks.db-shm"):
        p = root / relative
        if p.is_symlink() or not p.resolve().is_relative_to(root):
            fail("Do not share/link another checkout's data: " + relative)
    queue = os.environ.get("KNOWRA_TASK_DB")
    if queue and not Path(queue).resolve().is_relative_to(root / "data"):
        fail("KNOWRA_TASK_DB must stay in this checkout's data directory.")
    if os.environ.get("KNOWRA_DEPLOY_MODE", "local") != "local":
        fail("This launcher is for local development, not cloud deployment.")
    existing = any((root / "data").glob("*.db")) or any((root / "data").glob("*.sqlite*"))
    if existing and check_only:
        print("Knowra: Existing data detected. Preflight only: no database was opened and no services were started. Starting services still requires explicit existing-data permission after reviewing backup/migration implications.")
        return
    if existing:
        confirm_existing_data(root)


def main():
    if sys.version_info < (3, 9):
        fail("Bootstrap Python 3.9+ is required. Set PYTHON_BIN=/path/to/python3.12 and run ./scripts/install-native.sh.")
    mode = sys.argv[1]
    if mode == "python":
        print(".".join(map(str, require_python(sys.argv[2]))))
        return
    root = Path(sys.argv[2]).resolve()
    paths(root)
    if mode == "select":
        print(select(root))
    elif mode == "paths":
        return
    elif mode == "installed":
        installed(root)
    elif mode == "owner":
        owner(root, Path(sys.argv[3]))
    elif mode == "begin-install":
        write_record(root, Path(sys.argv[3]), "installing", sys.argv[4])
    elif mode == "finish-install":
        write_record(root, Path(sys.argv[3]), "ready", ".".join(map(str, sys.version_info[:3])))
    elif mode in ("startup", "preflight"):
        startup(root, sys.argv[3:], check_only=mode == "preflight")
    else:
        fail("Unknown native runtime operation")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, IndexError) as error:
        print("Knowra: " + str(error), file=sys.stderr)
        sys.exit(1)
