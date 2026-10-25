#!/usr/bin/env python3
"""Exercise the real native shell entry points with disposable local fixtures.

Python/Node runtime checks are real. uv/npm installation and the two application
services are controlled test doubles: no package registry, application import,
research database, real provider or user configuration is involved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
NATIVE = "backend/.venv-native-py312"
CANONICAL = "backend/.venv"

SERVICE = r'''import argparse, json, os, signal, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--host', required=True)
parser.add_argument('--port', required=True, type=int)
options, _ = parser.parse_known_args()
if options.host != '127.0.0.1': raise SystemExit('Fixture refuses non-loopback host')
kind = 'backend' if '-m' not in sys.argv and sys.argv[0].endswith('__main__.py') else 'frontend'
root = Path.cwd().parent
record = {'kind':kind, 'pid':os.getpid(), 'python':sys.executable, 'prefix':sys.prefix,
          'arguments':sys.argv[1:], 'port':options.port,
          'proxy':os.environ.get('VITE_API_PROXY_TARGET'), 'task_db':os.environ.get('KNOWRA_TASK_DB')}
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(json.dumps(record).encode())
    def log_message(self, *_): pass
server = HTTPServer(('127.0.0.1', options.port), Handler)
(root / (kind + '-service.json')).write_text(json.dumps(record))
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: sys.exit(0))
try: server.serve_forever(poll_interval=0.05)
finally: server.server_close()
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
        self.processes: list[subprocess.Popen[str]] = []

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
        self.temporary.cleanup()

    def run_entry(self, entry: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.fixture / entry), *args], cwd=self.fixture,
                              env=self.env if env is None else env, text=True,
                              capture_output=True, timeout=30)

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

    def test_existing_data_requires_explicit_opt_in(self) -> None:
        self.install()
        sentinel = self.fixture / "data/knowledge.db"
        sentinel.write_bytes(b"synthetic sentinel; never opened as SQLite")
        output = self.assert_friendly_failure(self.run_entry("start.sh", "--check"))
        self.assertIn("KNOWRA_ALLOW_EXISTING_DATA", output)
        accepted = self.run_entry("start.sh", "--check", env={**self.env, "KNOWRA_ALLOW_EXISTING_DATA": "1"})
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual(sentinel.read_bytes(), b"synthetic sentinel; never opened as SQLite")

    def test_daily_start_uses_selected_environment_loopback_ports_and_cleans_up(self) -> None:
        before = self.legacy()
        self.install()
        api_port, web_port = unused_ports()
        # A relative NODE_BIN must remain usable after the launcher cd's into
        # backend/frontend. It may not accidentally select a global Node later.
        env = {**self.env, "NODE_BIN": "./bin/node", "BACKEND_PORT": str(api_port), "FRONTEND_PORT": str(web_port)}
        process = subprocess.Popen([str(self.fixture / "start.sh")], cwd=self.fixture, env=env,
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0, stdout + stderr)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
