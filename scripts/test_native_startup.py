#!/usr/bin/env python3
"""Exercise the real native shell entry points with disposable local fixtures.

Python/Node runtime checks are real. uv/npm installation and the two application
services are controlled test doubles: no package registry, application import,
research database, real provider or user configuration is involved.
"""
from __future__ import annotations

import errno
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
NATIVE = "backend/.venv-native-py312"
CANONICAL = "backend/.venv"

SERVICE = r'''import argparse, json, os, signal, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
if os.environ.get('KNOWRA_TEST_PREFLIGHT') == '1':
    (Path.cwd().parent / 'unexpected-service-start').touch()
    raise SystemExit('Preflight must not start a service')
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--host', required=True)
parser.add_argument('--port', required=True, type=int)
options, _ = parser.parse_known_args()
if options.host != '127.0.0.1': raise SystemExit('Fixture refuses non-loopback host')
kind = 'backend' if '-m' not in sys.argv and sys.argv[0].endswith('__main__.py') else 'frontend'
root = Path.cwd().parent
record = {'kind':kind, 'pid':os.getpid(), 'python':sys.executable, 'prefix':sys.prefix,
          'arguments':sys.argv[1:], 'port':options.port,
          'proxy':os.environ.get('VITE_API_PROXY_TARGET'), 'task_db':os.environ.get('KNOWRA_TASK_DB'),
          'existing_data_permission':os.environ.get('KNOWRA_ALLOW_EXISTING_DATA')}
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(json.dumps(record).encode())
    def log_message(self, *_): pass
server = HTTPServer(('127.0.0.1', options.port), Handler)
(root / (kind + '-service.json')).write_text(json.dumps(record))
def shutdown(*_):
    # Model an application's asynchronous shutdown hooks. The launcher must
    # remain alive until this completes, not merely send TERM and return.
    time.sleep(0.2)
    raise SystemExit(0)
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, shutdown)
try: server.serve_forever(poll_interval=0.05)
finally:
    server.server_close()
    (root / (kind + '-shutdown-complete')).write_text(str(os.getpid()))
'''

UV = r'''import json, subprocess, sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent
config = json.loads((root/'.mock-config.json').read_text())
args = sys.argv[1:]
with (root/'tool-calls.jsonl').open('a') as log: log.write(json.dumps(['uv',*args])+'\n')
if 'python' in args and 'find' in args:
    print(config['python']); raise SystemExit(0)
if 'venv' in args:
    target = Path(args[-1]); target.resolve().relative_to(root)
    if (root/'.fail-venv').exists():
        target.mkdir(exist_ok=True)
        (target/'partial-creation-sentinel').write_text('keep partial managed environment')
        raise SystemExit(43)
    subprocess.run([config['python'],'-m','venv','--without-pip',str(target)],check=True)
    raise SystemExit(0)
if 'pip' in args and 'sync' in args:
    if (root/'.fail-sync').exists(): raise SystemExit(41)
    python = args[args.index('--python')+1]
    site = Path(subprocess.check_output([python,'-I','-c','import sysconfig; print(sysconfig.get_path("purelib"))'],text=True).strip())
    site.resolve().relative_to(root)
    package = site/'uvicorn'; package.mkdir(exist_ok=True)
    (package/'__init__.py').write_text('')
    (package/'__main__.py').write_text((root/'mock-service.py').read_text())
    raise SystemExit(0)
if 'pip' in args and 'check' in args: raise SystemExit(0)
raise SystemExit('Unexpected mock uv call: '+repr(args))
'''

NPM = r'''import json, os, shutil, sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent
config = json.loads((root/'.mock-config.json').read_text())
args = sys.argv[1:]
with (root/'tool-calls.jsonl').open('a') as log: log.write(json.dumps(['npm',*args])+'\n')
if args == ['--version']: print('11.11.1'); raise SystemExit(0)
if 'ci' in args:
    if (root/'.fail-npm').exists(): raise SystemExit(42)
    prefix = Path(args[args.index('--prefix')+1]); prefix.resolve().relative_to(root)
    modules = prefix/'node_modules'; modules.mkdir(exist_ok=True)
    shutil.copyfile(prefix/'package-lock.json', modules/'.package-lock.json')
    raise SystemExit(0)
if args[:2] == ['run','dev']:
    service_args = args[3:] if args[2:3] == ['--'] else args[2:]
    os.execv(config['python'],[config['python'],str(root/'mock-service.py'),*service_args])
raise SystemExit('Unexpected mock npm call: '+repr(args))
'''

LEGACY_PYTHON = r'''import sys
from pathlib import Path
args = [arg for arg in sys.argv[1:] if arg not in ('-I','-S','-s','-E')]
if len(args) < 2 or args[0] != '-c':
    raise SystemExit('Old fixture interpreter must only be inspected, never used')
sys.version_info = (3,9,6,'final',0)
sys.version = '3.9.6 (legacy fixture)'
sys.prefix = str(Path(__file__).resolve().parent.parent)
sys.executable = str(Path(__file__).resolve())
exec(compile(args[1], '<runtime-version-probe>', 'exec'))
'''

UNSUPPORTED_NODE = r'''import json, os, sys
from pathlib import Path
root = Path(__file__).resolve().parents[2]
node = json.loads((root/'.mock-config.json').read_text())['node']
if sys.argv[1:] in (['--version'], ['-v']): print('v26.0.0'); raise SystemExit(0)
if sys.argv[1:2] != ['-e']: raise SystemExit('Unexpected Node version probe')
script = "Object.defineProperty(process.versions, 'node', {value:'26.0.0'});" + sys.argv[2]
os.execv(node,[node,'-e',script])
'''


def executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable} -I\n" + content)
    path.chmod(0o755)


def unused_ports() -> tuple[int, int]:
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


def signal_group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


class NativeStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sys.version_info[:2] != (3, 12) or sys.version_info < (3, 12, 13):
            raise RuntimeError("Run startup regression tests with Python 3.12.13+ within 3.12.x")
        cls.node = os.environ.get("NODE_BIN") or shutil.which("node")
        if not cls.node:
            raise RuntimeError("Node 24 is required for the real runtime checks; no tests were skipped")
        cls.node = str(Path(cls.node).resolve())
        version = subprocess.check_output([cls.node, "-p", "process.versions.node"],
                                          env={"PATH": os.defpath}, text=True).strip()
        major, minor, patch = map(int, version.split("."))
        if major != 24 or (minor, patch) < (14, 1):
            raise RuntimeError(f"Use Node 24.14.1+ within 24.x, found {version}")
        cache = ROOT / ".cache"
        if cache.is_symlink():
            raise RuntimeError("Refusing a linked fixture cache")
        cache.mkdir(exist_ok=True)
        cls.cache = cache

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="startup-test-", dir=self.cache)
        self.fixture = Path(self.temporary.name)
        for directory in ("scripts", "backend", "frontend", "bin", "data"):
            (self.fixture / directory).mkdir()
        for source in (ROOT / "scripts").glob("native*"):
            if source.is_file():
                shutil.copyfile(source, self.fixture / "scripts" / source.name)
        for relative in ("start.sh", "scripts/install-native.sh", ".python-version", ".node-version",
                         "backend/requirements-dev.lock", "frontend/package-lock.json", "frontend/package.json"):
            shutil.copyfile(ROOT / relative, self.fixture / relative)
        for script in (self.fixture / "start.sh", self.fixture / "scripts/install-native.sh"):
            script.chmod(0o755)
        (self.fixture / "bin/node").symlink_to(self.node)
        (self.fixture / "bin/python3").symlink_to(sys.executable)
        (self.fixture / "mock-service.py").write_text(SERVICE)
        (self.fixture / ".mock-config.json").write_text(json.dumps({"python": sys.executable, "node": self.node}))
        executable(self.fixture / "bin/uv", UV)
        executable(self.fixture / "bin/npm", NPM)
        self.env = {
            "PATH": str(self.fixture / "bin") + os.pathsep + "/usr/bin:/bin:/usr/sbin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHON_BIN": sys.executable,
            "TMPDIR": str(self.fixture),
        }
        api_port, web_port = unused_ports()
        self.env.update(BACKEND_PORT=str(api_port), FRONTEND_PORT=str(web_port))
        self.processes: list[subprocess.Popen[str]] = []
        self.terminals: dict[int, str] = {}

    def tearDown(self) -> None:
        # These PIDs and process groups were created only by this test. Never
        # search for or terminate an unrelated service using the same port.
        for process in self.processes:
            signal_group(process.pid, signal.SIGTERM)
            if process.poll() is None:
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    signal_group(process.pid, signal.SIGKILL)
                    process.communicate(timeout=5)
            # A failed launcher may already have exited while a fixture child
            # is still alive; clean only the process group we created above.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                continue
            signal_group(process.pid, signal.SIGKILL)
        for descriptor in self.terminals:
            os.close(descriptor)
        self.temporary.cleanup()

    def run_entry(self, entry: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.fixture / entry), *args], cwd=self.fixture,
                              env=self.env if env is None else env, text=True,
                              capture_output=True, timeout=30)

    def start_terminal(self, *args: str, env: dict[str, str] | None = None) -> tuple[subprocess.Popen[str], int]:
        """Give the real launcher terminal input/output without a user's TTY."""
        master, slave = pty.openpty()
        self.terminals[master] = ""
        try:
            process = subprocess.Popen([str(self.fixture / "start.sh"), *args], cwd=self.fixture,
                                       env=self.env if env is None else env,
                                       stdin=slave, stdout=slave, stderr=slave,
                                       text=True, start_new_session=True)
        finally:
            os.close(slave)
        self.processes.append(process)
        return process, master

    def read_terminal(self, descriptor: int, timeout: float = 0.05) -> bool:
        if not select.select([descriptor], [], [], timeout)[0]:
            return False
        try:
            block = os.read(descriptor, 8192)
        except OSError as error:
            if error.errno == errno.EIO:  # macOS/Linux PTY closed by the child.
                return False
            raise
        self.terminals[descriptor] += block.decode(errors="replace")
        return bool(block)

    def terminal_finish(self, process: subprocess.Popen[str], descriptor: int) -> subprocess.CompletedProcess[str]:
        deadline = time.monotonic() + 10
        while process.poll() is None and time.monotonic() < deadline:
            self.read_terminal(descriptor)
        self.assertIsNotNone(process.poll(), "Terminal launcher failed to exit: " + self.terminals[descriptor])
        while self.read_terminal(descriptor, 0):
            pass
        return subprocess.CompletedProcess(process.args, process.returncode, self.terminals[descriptor], "")

    def assert_ports_closed(self, ports: tuple[int, int]) -> None:
        for port in ports:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with socket.socket() as connection:
                    if connection.connect_ex(("127.0.0.1", port)) != 0:
                        break
                time.sleep(0.05)
            else:
                self.fail(f"Launcher left its mock service listening on {port}")

    def assert_services_shutdown(self, records: list[dict]) -> None:
        for record in records:
            marker = self.fixture / (record["kind"] + "-shutdown-complete")
            self.assertTrue(marker.is_file(), "Launcher returned before " + record["kind"] + " shutdown completed")
            self.assertEqual(marker.read_text(), str(record["pid"]))

    def legacy(self) -> bytes:
        executable(self.fixture / CANONICAL / "bin/python", LEGACY_PYTHON)
        sentinel = self.fixture / CANONICAL / "keep-existing-environment"
        sentinel.write_bytes(b"old environment must stay unchanged\n")
        return (self.fixture / CANONICAL / "bin/python").read_bytes()

    def install(self) -> dict:
        run = self.run_entry("scripts/install-native.sh")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        marker = self.marker()
        self.assertEqual(marker["status"], "ready")
        return marker

    def marker(self) -> dict:
        return json.loads((self.fixture / ".cache/native-install.json").read_text())

    def assert_friendly_failure(self, run: subprocess.CompletedProcess[str]) -> str:
        output = run.stdout + run.stderr
        self.assertNotEqual(run.returncode, 0, output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("AssertionError", output)
        return output

    def audit_preflight(self) -> dict[str, str]:
        """Audit fixture Python processes without importing an application.

        The launcher/runtime use normal Python startup and inherit this hook.
        Their isolated -I/-S version probes intentionally only import sys.
        Violations are recorded even if application code catches the exception.
        """
        audit = self.fixture / "audit"
        audit.mkdir()
        (audit / "sitecustomize.py").write_text(
            "from pathlib import Path\nimport os, sys\n"
            f"root = Path({str(self.fixture)!r})\n" + r'''
with (root / "audit-loaded").open("a") as log:
    log.write("loaded\n")
def reject(event):
    with (root / "audit-violations").open("a") as log:
        log.write(event + "\n")
    raise RuntimeError("Preflight attempted forbidden operation: " + event)
def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        target = Path(os.fsdecode(args[0])).absolute()
        if target.is_relative_to(root / "data") or target == root / ".env":
            reject(event + ": data/config file")
    if event == "sqlite3.connect":
        reject(event)
    if event == "import" and (args[0] in {"main", "task_worker", "services.task_runtime"}
                              or args[0].startswith("uvicorn")):
        reject(event + ": application/worker")
    if event == "subprocess.Popen":
        command = args[1]
        if not (isinstance(command, (tuple, list)) and command[1:] == [
                "-I", "-S", "-c", "import sys; print('.'.join(map(str,sys.version_info[:3])))"]):
            reject(event + ": unexpected child")
sys.addaudithook(audit)
''')
        return {**self.env, "PYTHONPATH": str(audit), "KNOWRA_TEST_PREFLIGHT": "1"}

    def assert_no_preflight_side_effects(self, calls_before: bytes) -> None:
        self.assertTrue((self.fixture / "audit-loaded").is_file(), "Python audit must be active")
        self.assertFalse((self.fixture / "audit-violations").exists(), "No data open, SQLite connection or app/worker import")
        self.assertEqual((self.fixture / "tool-calls.jsonl").read_bytes(), calls_before,
                         "Preflight must not invoke npm or uv")
        self.assertFalse((self.fixture / "backend-service.json").exists())
        self.assertFalse((self.fixture / "frontend-service.json").exists())
        self.assertFalse((self.fixture / "unexpected-service-start").exists())

    def test_legacy_environment_error_is_actionable_and_does_not_mutate(self) -> None:
        before = self.legacy()
        output = self.assert_friendly_failure(self.run_entry("start.sh", "--check"))
        self.assertIn("3.9.6", output)
        self.assertIn("backend/.venv", output)
        self.assertIn("install-native.sh", output)
        self.assertEqual((self.fixture / CANONICAL / "bin/python").read_bytes(), before)
        self.assertFalse((self.fixture / NATIVE).exists())
        self.assertFalse((self.fixture / "tool-calls.jsonl").exists())

    def test_installer_preserves_old_environment_and_binds_new_environment(self) -> None:
        before = self.legacy()
        marker = self.install()
        self.assertEqual(marker["venv"], NATIVE)
        self.assertEqual((self.fixture / CANONICAL / "bin/python").read_bytes(), before)
        self.assertTrue((self.fixture / CANONICAL / "keep-existing-environment").is_file())
        self.assertFalse((self.fixture / "backend/.venv-py312").exists())
        self.assertEqual(self.run_entry("start.sh", "--check").returncode, 0)

    def test_fresh_checkout_installs_canonical_environment(self) -> None:
        self.assertEqual(self.install()["venv"], CANONICAL)
        self.assertEqual(self.run_entry("start.sh", "--check").returncode, 0)

    def test_installer_finds_existing_python_when_python_bin_is_unset(self) -> None:
        env = {key: value for key, value in self.env.items() if key != "PYTHON_BIN"}
        run = self.run_entry("scripts/install-native.sh", env=env)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.marker()["status"], "ready")

    def test_invalid_explicit_python_has_actionable_error(self) -> None:
        env = {**self.env, "PYTHON_BIN": str(self.fixture / "missing-python")}
        output = self.assert_friendly_failure(self.run_entry("scripts/install-native.sh", env=env))
        self.assertIn("Python", output)
        self.assertIn("missing-python", output)
        self.assertFalse((self.fixture / ".cache/native-install.json").exists())

    def test_unsupported_node_reports_version_and_local_selection(self) -> None:
        fake_node = self.fixture / "bad-node/bin/node"
        executable(fake_node, UNSUPPORTED_NODE)
        run = self.run_entry("scripts/install-native.sh", env={**self.env, "NODE_BIN": str(fake_node)})
        output = self.assert_friendly_failure(run)
        self.assertIn("26.0.0", output)
        self.assertIn("NODE_BIN", output)
        self.assertFalse((self.fixture / ".cache/native-install.json").exists())

    def test_node_alias_is_rejected_instead_of_using_another_path_node(self) -> None:
        alias = self.fixture / "bin/node24"
        alias.symlink_to(self.node)
        output = self.assert_friendly_failure(self.run_entry(
            "scripts/install-native.sh", env={**self.env, "NODE_BIN": str(alias)}))
        self.assertIn("NODE_BIN", output)
        self.assertFalse((self.fixture / ".cache/native-install.json").exists())

    def test_unmanaged_native_target_is_never_overwritten(self) -> None:
        self.legacy()
        target = self.fixture / NATIVE
        target.mkdir()
        sentinel = target / "unmanaged-environment"
        sentinel.write_bytes(b"preserve this environment")
        output = self.assert_friendly_failure(self.run_entry("scripts/install-native.sh"))
        self.assertIn("venv-native-py312", output)
        self.assertEqual(sentinel.read_bytes(), b"preserve this environment")
        self.assertFalse((target / "bin/python").exists())

    def test_failed_install_never_becomes_ready(self) -> None:
        (self.fixture / ".fail-sync").touch()
        run = self.run_entry("scripts/install-native.sh")
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self.marker()["status"], "installing")
        self.assert_friendly_failure(self.run_entry("start.sh", "--check"))

    def test_failed_install_can_resume_its_managed_target(self) -> None:
        self.legacy()
        failure = self.fixture / ".fail-npm"
        failure.touch()
        run = self.run_entry("scripts/install-native.sh")
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self.marker()["status"], "installing")
        self.assertEqual(self.marker()["venv"], NATIVE)
        failure.unlink()
        self.assertEqual(self.install()["venv"], NATIVE)
        self.assertEqual(self.run_entry("start.sh", "--check").returncode, 0)

    def test_interrupted_environment_creation_resumes_the_recorded_target(self) -> None:
        before = self.legacy()
        failure = self.fixture / ".fail-venv"
        failure.touch()
        run = self.run_entry("scripts/install-native.sh")
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(self.marker()["status"], "installing")
        self.assertEqual(self.marker()["venv"], NATIVE)
        self.assertFalse((self.fixture / NATIVE / "bin/python").exists())
        failure.unlink()
        self.assertEqual(self.install()["venv"], NATIVE)
        self.assertTrue((self.fixture / NATIVE / "partial-creation-sentinel").is_file())
        self.assertEqual((self.fixture / CANONICAL / "bin/python").read_bytes(), before)
        calls = [json.loads(line) for line in (self.fixture / "tool-calls.jsonl").read_text().splitlines()]
        creation_calls = [call for call in calls if call[0] == "uv" and "venv" in call]
        self.assertEqual(len(creation_calls), 2)
        self.assertIn("--allow-existing", creation_calls[-1])
        self.assertEqual(self.run_entry("start.sh", "--check").returncode, 0)

    def test_marker_cannot_claim_a_different_environment(self) -> None:
        self.legacy()
        marker = self.install()
        marker["venv"] = CANONICAL
        (self.fixture / ".cache/native-install.json").write_text(json.dumps(marker))
        self.assert_friendly_failure(self.run_entry("start.sh", "--check"))

    def test_installer_rejects_an_interpreter_outside_selected_environment_before_sync(self) -> None:
        marker = self.install()
        target = self.fixture / marker["venv"] / "bin/python"
        target.unlink()  # Remove only this fixture's interpreter link, not its target.
        executable(target, f"import os,sys\nos.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n")
        before = (self.fixture / "tool-calls.jsonl").read_text().splitlines()
        self.assert_friendly_failure(self.run_entry("scripts/install-native.sh"))
        later = (self.fixture / "tool-calls.jsonl").read_text().splitlines()[len(before):]
        self.assertFalse(any('"sync"' in call for call in later), "No pip sync may run against a different interpreter")

    def test_changed_lock_requires_reinstallation(self) -> None:
        self.install()
        with (self.fixture / "backend/requirements-dev.lock").open("a") as stream:
            stream.write("\n# synthetic lock edit\n")
        output = self.assert_friendly_failure(self.run_entry("start.sh", "--check"))
        self.assertIn("install-native.sh", output)

    def test_symlinked_environment_is_rejected(self) -> None:
        outside = self.fixture / "outside"
        outside.mkdir()
        (self.fixture / CANONICAL).symlink_to(outside, target_is_directory=True)
        self.assert_friendly_failure(self.run_entry("scripts/install-native.sh"))
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlinked_installation_record_is_rejected_before_write(self) -> None:
        cache = self.fixture / ".cache"
        cache.mkdir()
        outside = self.fixture / "external-record.json"
        original = b'{"synthetic":"must remain unchanged"}\n'
        outside.write_bytes(original)
        (cache / "native-install.json").symlink_to(outside)
        self.assert_friendly_failure(self.run_entry("scripts/install-native.sh"))
        self.assertEqual(outside.read_bytes(), original)

    def test_preflight_with_existing_database_is_read_only(self) -> None:
        self.install()
        env = self.audit_preflight()
        calls_before = (self.fixture / "tool-calls.jsonl").read_bytes()
        # Synthetic invalid SQLite bytes make accidental DB use observable;
        # the audit hook also catches reads that would not change those bytes.
        for name in ("knowledge.db", "search.sqlite", "backup.sqlite3"):
            with self.subTest(name=name):
                sentinel = self.fixture / "data" / name
                original = b"synthetic sentinel; never opened as SQLite"
                sentinel.write_bytes(original)
                run = self.run_entry("start.sh", "--check", env=env)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn("Existing local data detected", run.stdout + run.stderr)
                self.assertIn("Preflight passed", run.stdout)
                self.assertEqual(sentinel.read_bytes(), original)
                self.assertEqual(list((self.fixture / "data").iterdir()), [sentinel])
                self.assert_no_preflight_side_effects(calls_before)
                sentinel.unlink()

    def test_preflight_without_database_does_not_load_config_or_start_services(self) -> None:
        self.install()
        env = self.audit_preflight()
        (self.fixture / ".env").write_text("SYNTHETIC_ONLY=not-a-real-credential\n")
        calls_before = (self.fixture / "tool-calls.jsonl").read_bytes()
        run = self.run_entry("start.sh", "--check", env={**env, "KNOWRA_LOAD_ENV": "1"})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Preflight passed", run.stdout)
        self.assertNotIn("Existing local data detected", run.stdout + run.stderr)
        self.assertEqual(list((self.fixture / "data").iterdir()), [])
        self.assert_no_preflight_side_effects(calls_before)

    def test_preflight_still_rejects_occupied_ports(self) -> None:
        self.install()
        self.assertIsNotNone(shutil.which("lsof", path=self.env["PATH"]), "Port validation requires lsof")
        env = self.audit_preflight()
        calls_before = (self.fixture / "tool-calls.jsonl").read_bytes()
        for variable in ("BACKEND_PORT", "FRONTEND_PORT"):
            with self.subTest(variable=variable), socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                port = listener.getsockname()[1]
                run = self.run_entry("start.sh", "--check", env={**env, variable: str(port)})
                output = self.assert_friendly_failure(run)
                self.assertIn(f"Port {port} is occupied", output)
                self.assertNotIn("Preflight passed", output)
                self.assert_no_preflight_side_effects(calls_before)

    def test_preflight_still_rejects_linked_data(self) -> None:
        self.install()
        outside = self.fixture / "other-data"
        outside.mkdir()
        (self.fixture / "data").rmdir()
        (self.fixture / "data").symlink_to(outside, target_is_directory=True)
        output = self.assert_friendly_failure(self.run_entry("start.sh", "--check"))
        self.assertIn("Do not share/link", output)
        self.assertEqual(list(outside.iterdir()), [])

    def test_terminal_start_uses_existing_data_without_prompt_or_permission(self) -> None:
        self.install()
        sentinel = self.fixture / "data/knowledge.db"
        original = b"synthetic existing data; mock services never open it"
        sentinel.write_bytes(original)
        marker_before = (self.fixture / ".cache/native-install.json").read_bytes()
        self.assertNotIn("KNOWRA_ALLOW_EXISTING_DATA", self.env)
        process, terminal = self.start_terminal()
        # Leave the terminal input completely untouched. Existing local data
        # must not make the ordinary entry point wait for START or a switch.
        ports = (int(self.env["BACKEND_PORT"]), int(self.env["FRONTEND_PORT"]))
        http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        records = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.read_terminal(terminal, 0)
            self.assertIsNone(process.poll(), self.terminals[terminal])
            try:
                records = []
                for port in ports:
                    with http.open(f"http://127.0.0.1:{port}/health", timeout=0.3) as response:
                        records.append(json.load(response))
                break
            except OSError:
                time.sleep(0.05)
        self.assertEqual(len(records), 2, self.terminals[terminal])
        for record in records:
            self.assertIsNone(record["existing_data_permission"], "Startup must not inject an environment bypass")
        process.terminate()
        process.wait(timeout=5)
        self.assert_services_shutdown(records)
        run = self.terminal_finish(process, terminal)
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn("Type START", run.stdout)
        self.assertNotIn("KNOWRA_ALLOW_EXISTING_DATA", run.stdout)
        self.assert_ports_closed(ports)
        self.assertEqual(sentinel.read_bytes(), original)
        self.assertEqual((self.fixture / ".cache/native-install.json").read_bytes(), marker_before)

    def test_terminal_preflight_and_occupied_start_preserve_read_only_boundaries(self) -> None:
        self.install()
        (self.fixture / "data/knowledge.db").write_bytes(b"synthetic sentinel")
        env = self.audit_preflight()
        calls_before = (self.fixture / "tool-calls.jsonl").read_bytes()
        process, terminal = self.start_terminal("--check", env=env)
        run = self.terminal_finish(process, terminal)
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertNotIn("Type START to continue", run.stdout)
        self.assert_no_preflight_side_effects(calls_before)
        for variable in ("BACKEND_PORT", "FRONTEND_PORT"):
            with self.subTest(variable=variable), socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                port = listener.getsockname()[1]
                process, terminal = self.start_terminal(env={**env, variable: str(port)})
                output = self.assert_friendly_failure(self.terminal_finish(process, terminal))
                self.assertIn(f"Port {port} is occupied", output)
                self.assertNotIn("Type START to continue", output)
                self.assert_no_preflight_side_effects(calls_before)

    def test_noninteractive_start_uses_existing_data_and_selected_environment_without_permission(self) -> None:
        before = self.legacy()
        self.install()
        api_port, web_port = unused_ports()
        # A relative NODE_BIN must remain usable after the launcher cd's into
        # backend/frontend. It may not accidentally select a global Node later.
        env = {**self.env, "NODE_BIN": "./bin/node", "BACKEND_PORT": str(api_port), "FRONTEND_PORT": str(web_port)}
        sentinel = self.fixture / "data/knowledge.db"
        sentinel.write_bytes(b"synthetic existing data")
        marker_before = (self.fixture / ".cache/native-install.json").read_bytes()
        self.assertNotIn("KNOWRA_ALLOW_EXISTING_DATA", env)
        process = subprocess.Popen([str(self.fixture / "start.sh")], cwd=self.fixture, env=env,
                                   stdin=subprocess.DEVNULL, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        self.processes.append(process)
        records = []
        http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail("Launcher exited before both mock services: " + stdout + stderr)
            try:
                records = []
                for port in (api_port, web_port):
                    with http.open(f"http://127.0.0.1:{port}/health", timeout=0.3) as response:
                        records.append(json.load(response))
                break
            except OSError:
                time.sleep(0.05)
        self.assertEqual(len(records), 2, "Both real launcher child commands must serve loopback HTTP")
        api, web = records
        self.assertEqual(api["prefix"], str(self.fixture / NATIVE))
        self.assertEqual(api["task_db"], str(self.fixture / "data/tasks.db"))
        self.assertEqual(web["proxy"], f"http://127.0.0.1:{api_port}")
        self.assertIn("--strictPort", web["arguments"])
        self.assertIn("main:app", api["arguments"])
        self.assertIsNone(api["existing_data_permission"])
        self.assertIsNone(web["existing_data_permission"])
        process.terminate()
        # Wait for the launcher PID, not communicate(): inherited output pipes
        # could otherwise hide children still shutting down after it exits.
        process.wait(timeout=5)
        self.assert_services_shutdown(records)
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0, stdout + stderr)
        self.assertNotIn("Type START", stdout + stderr)
        self.assertNotIn("KNOWRA_ALLOW_EXISTING_DATA", stdout + stderr)
        self.assertEqual((self.fixture / ".cache/native-install.json").read_bytes(), marker_before)
        for port in (api_port, web_port):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with socket.socket() as connection:
                    if connection.connect_ex(("127.0.0.1", port)) != 0:
                        break
                time.sleep(0.05)
            else:
                self.fail(f"Launcher left its mock service listening on {port}")
        self.assertEqual((self.fixture / CANONICAL / "bin/python").read_bytes(), before)
        self.assertFalse((self.fixture / "data/tasks.db").exists())
        self.assertEqual(sentinel.read_bytes(), b"synthetic existing data")


class FixtureListenerOwnershipTests(unittest.TestCase):
    """Socket-table decisions only: no real listeners, apps or HTTP calls."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "native_start_ownership_fixture", ROOT / "scripts/check_native_start.py")
        cls.smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.smoke)

    def setUp(self):
        self.ports = (48100, 48101)
        self.snapshot = Path("/synthetic-fixture")

    def records(self, port):
        return [{"pid": 101 if port == self.ports[0] else 102,
                 "address": f"127.0.0.1:{port}"}]

    def test_unbound_ports_wait_without_probing(self):
        with patch.object(self.smoke, "descendants", return_value={101, 102}), \
                patch.object(self.smoke, "listener_records", return_value=[]), \
                patch.object(self.smoke, "owned_alive") as ownership:
            self.assertEqual(self.smoke.fixture_listeners(self.ports, 100, self.snapshot), {})
        ownership.assert_not_called()

    def test_fixture_descendants_with_snapshot_cwd_are_accepted(self):
        with patch.object(self.smoke, "descendants", return_value={101, 102}), \
                patch.object(self.smoke, "listener_records", side_effect=self.records), \
                patch.object(self.smoke, "owned_alive", return_value=True) as ownership:
            result = self.smoke.fixture_listeners(self.ports, 100, self.snapshot)
        self.assertEqual(result, {str(port): self.records(port) for port in self.ports})
        self.assertEqual(ownership.call_count, 2)

    def test_unrelated_listener_is_rejected_without_claiming_ownership(self):
        with patch.object(self.smoke, "descendants", return_value={999}), \
                patch.object(self.smoke, "listener_records", side_effect=self.records), \
                patch.object(self.smoke, "owned_alive") as ownership:
            with self.assertRaisesRegex(RuntimeError, "another process or worktree"):
                self.smoke.fixture_listeners(self.ports, 100, self.snapshot)
        ownership.assert_not_called()

    def test_wrong_worktree_cwd_is_rejected(self):
        with patch.object(self.smoke, "descendants", return_value={101, 102}), \
                patch.object(self.smoke, "listener_records", side_effect=self.records), \
                patch.object(self.smoke, "owned_alive", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "another process or worktree"):
                self.smoke.fixture_listeners(self.ports, 100, self.snapshot)

    def test_wildcard_listener_is_rejected(self):
        with patch.object(self.smoke, "descendants", return_value={101}), \
                patch.object(self.smoke, "listener_records", return_value=[
                    {"pid": 101, "address": "*:48100"}]), \
                patch.object(self.smoke, "owned_alive") as ownership:
            with self.assertRaisesRegex(RuntimeError, "exclusively IPv4 loopback"):
                self.smoke.fixture_listeners(self.ports, 100, self.snapshot)
        ownership.assert_not_called()

    def test_lsof_failure_or_warning_cannot_authorize_http(self):
        for returncode, stderr in ((2, "synthetic failure"), (0, "synthetic warning")):
            with self.subTest(returncode=returncode), \
                    patch.object(self.smoke, "descendants", return_value={101, 102}), \
                    patch.object(self.smoke.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], returncode, stdout="p101\nn127.0.0.1:48100\n", stderr=stderr)):
                with self.assertRaisesRegex(RuntimeError, "Cannot verify"):
                    self.smoke.fixture_listeners(self.ports, 100, self.snapshot)


if __name__ == "__main__":
    unittest.main(verbosity=2)
