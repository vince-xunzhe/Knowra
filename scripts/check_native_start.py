#!/usr/bin/env python3
"""Smoke-test real API/Vite startup in a disposable, offline source snapshot.

No application API is mocked. The snapshot has fresh SQLite files, a fresh HOME,
and a physical venv whose .pth reads this worktree's installed dependencies.
node_modules is copied physically. Python writes outside the snapshot, external
network connections, and model CLI execution are blocked. The task worker uses
the supported external mode; the real recommendation worker starts with an empty
queue. This is not a migration test against an existing user's database.
"""
from __future__ import annotations

import argparse
import hashlib
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
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

PYTHON_GUARD = r'''import json, os, pathlib, socket, sys
ROOT = pathlib.Path(os.environ['KNOWRA_SMOKE_ROOT']).resolve()
RECORD = ROOT / '.cache/processes.jsonl'
VIOLATIONS = ROOT / '.cache/violations.jsonl'
READ_ROOTS = [ROOT, pathlib.Path(sys.base_prefix).resolve(),
              pathlib.Path(os.environ['KNOWRA_SMOKE_SITE']).resolve(),
              pathlib.Path('/System/Library'), pathlib.Path('/usr/lib')]
def log(path, value):
    with path.open('a') as f: f.write(json.dumps(value) + '\n')
def deny(kind, value):
    log(VIOLATIONS, {'kind':kind, 'target':str(value), 'pid':os.getpid()})
    raise PermissionError('Real startup smoke blocked ' + kind + ': ' + str(value))
def inside(path, roots):
    return any(path == parent or path.is_relative_to(parent) for parent in roots)
def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = pathlib.Path(os.fsdecode(args[0])).resolve()
        if str(path) in ('/dev/null', '/dev/urandom', '/dev/random'): return
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        if writing and not inside(path, [ROOT]): deny('outside write', path)
        if not writing and not inside(path, READ_ROOTS): deny('outside read', path)
    elif event in ('socket.connect', 'socket.bind'):
        address = args[1]
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1'):
            deny('non-loopback socket', address)
    elif event == 'socket.getaddrinfo' and args[0] not in ('127.0.0.1', '::1', 'localhost', None):
        deny('external DNS', args[0])
    elif event == 'sqlite3.connect':
        path = pathlib.Path(args[0]).resolve()
        if not path.is_relative_to(ROOT / 'data'): deny('outside SQLite', path)
    elif event == 'subprocess.Popen':
        executable, command = args[:2]
        # Only the app's own Python workers/version probes are allowed. This
        # blocks Codex/curl/provider CLIs even if a future code path selects one.
        if pathlib.Path(executable).resolve() != pathlib.Path(sys.executable).resolve():
            deny('external executable', executable)
        for value in command[1:]:
            if str(value).endswith('.py') and not pathlib.Path(value).resolve().is_relative_to(ROOT):
                deny('outside Python script', value)
log(RECORD, {'pid':os.getpid(), 'ppid':os.getppid(), 'kind':'python', 'argv':sys.argv})
sys.addaudithook(audit)
'''

NODE_GUARD = r'''const fs = require('node:fs');
const net = require('node:net');
const dns = require('node:dns');
const modules = require('node:module');
const path = require('node:path');
const root = process.env.KNOWRA_SMOKE_ROOT;
fs.appendFileSync(path.join(root,'.cache/processes.jsonl'), JSON.stringify({pid:process.pid,ppid:process.ppid,kind:'node',argv:process.argv})+'\n');
function deny(kind, value) {
  fs.appendFileSync(path.join(root,'.cache/violations.jsonl'),JSON.stringify({kind,target:String(value),pid:process.pid})+'\n');
  throw new Error('Real startup smoke blocked '+kind+': '+value);
}
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function(...args) {
  const first = Array.isArray(args[0]) ? args[0][0] : args[0];
  const pipe = typeof first === 'object' && first !== null && first.path;
  const host = typeof first === 'object' && first !== null ? first.host : args[1];
  if (!pipe && !['127.0.0.1','::1','localhost'].includes(host)) deny('non-loopback connection',host);
  return connect.apply(this,args);
};
const listen = net.Server.prototype.listen;
net.Server.prototype.listen = function(...args) {
  const first = args[0];
  const host = typeof first === 'object' && first !== null ? first.host : args[1];
  const port = typeof first === 'object' && first !== null ? first.port : first;
  // Vite probes wildcard bindings while checking this exact port. Refuse the
  // bind before it happens; actual API/Vite listeners must remain loopback.
  if (Number(port)===Number(process.env.FRONTEND_PORT) && ['0.0.0.0','::','0000:0000:0000:0000:0000:0000:0000:0000'].includes(host)) {
    fs.appendFileSync(path.join(root,'.cache/blocked-probes.jsonl'),JSON.stringify({kind:'Vite wildcard availability probe',host,port,pid:process.pid})+'\n');
    throw new Error('Smoke refuses wildcard port probe');
  }
  if (!['127.0.0.1','::1','localhost'].includes(host)) deny('non-loopback listener',host);
  return listen.apply(this,args);
};
const lookup = dns.lookup;
dns.lookup = function(host,...args) {
  if (!['127.0.0.1','::1','localhost'].includes(host)) deny('external DNS',host);
  return lookup.call(this,host,...args);
};
modules.syncBuiltinESMExports();
'''

BROWSER = r'''import fs from 'node:fs';
import { chromium } from './frontend/node_modules/playwright-core/index.mjs';
const [front, api, executable, output] = process.argv.slice(2);
const result = {steps:[], pageErrors:[], failedResponses:[], blockedRequests:[], allowedWrites:[]};
const browser = await chromium.launch({executablePath:executable,headless:true,args:['--disable-background-networking','--no-first-run']});
for (const sig of ['SIGTERM','SIGINT']) process.once(sig,async()=>{await browser.close();process.exit(128+(sig==='SIGTERM'?15:2));});
try {
  const context = await browser.newContext({viewport:{width:1440,height:960},locale:'zh-CN'});
  await context.route('**/*', async route => {
    const request=route.request(), url=new URL(request.url());
    if ((url.origin===front || url.origin===api) && ['GET','HEAD'].includes(request.method())) return route.continue();
    // The graph's existing mount effect submits an automatic scan. Keep the
    // real endpoint, but only for this empty fixture with external task mode.
    if ((url.origin===front || url.origin===api) && request.method()==='POST' && url.pathname==='/api/scan' && !url.search) {
      result.allowedWrites.push({url:request.url(),method:request.method()});
      return route.continue();
    }
    result.blockedRequests.push({url:request.url(),method:request.method()});
    return route.abort();
  });
  const page = await context.newPage();
  page.on('pageerror',e=>result.pageErrors.push(e.message));
  page.on('response',r=>{if(r.status()>=400)result.failedResponses.push({url:r.url(),status:r.status()});});
  await page.goto(front,{waitUntil:'domcontentloaded',timeout:45000});
  await page.getByRole('navigation').getByRole('button',{name:'资料',exact:true}).waitFor({timeout:45000});
  result.steps.push('Real React application rendered');
  const papers=page.waitForResponse(r=>r.url().includes('/api/papers') && r.status()===200);
  await page.getByRole('navigation').getByRole('button',{name:'资料',exact:true}).click();
  await papers;
  result.steps.push('Library navigation loaded real API response');
  const dashboard=page.waitForResponse(r=>r.url().includes('/api/dashboard/summary') && r.status()===200);
  await page.getByRole('navigation').getByRole('button',{name:'看板',exact:true}).click();
  await dashboard;
  await page.getByRole('heading',{name:'数据看板',exact:true}).waitFor();
  result.steps.push('Dashboard rendered from real SQLite-backed API');
  const refresh=page.waitForResponse(r=>r.url().includes('/api/dashboard/summary') && r.status()===200);
  await page.getByRole('button',{name:'刷新数据',exact:true}).click();
  await refresh;
  result.steps.push('Dashboard refresh completed against real API');
  await page.screenshot({path:output.replace(/\.json$/,'.png'),fullPage:true});
  if (result.pageErrors.length || result.failedResponses.length || result.blockedRequests.length) throw new Error('Browser smoke reported errors; inspect result');
} finally {
  await browser.close();
  fs.writeFileSync(output,JSON.stringify(result,null,2)+'\n');
}
'''


def read_json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def ports() -> tuple[int, int]:
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0)); second.bind(('127.0.0.1', 0))
        return first.getsockname()[1], second.getsockname()[1]


def listening(port: int) -> bool:
    with socket.socket() as client:
        client.settimeout(0.15)
        return client.connect_ex(('127.0.0.1', port)) == 0


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def descendants(pid: int) -> set[int]:
    result = set()
    pending = [pid]
    while pending:
        parent = pending.pop()
        found = subprocess.run(['/usr/bin/pgrep', '-P', str(parent)], text=True, capture_output=True)
        for raw in found.stdout.split():
            child = int(raw)
            if child not in result:
                result.add(child); pending.append(child)
    return result


def owned_alive(pid: int, snapshot: Path) -> bool:
    """Confirm this recorded PID still belongs to the fixture before signaling."""
    if not alive(pid):
        return False
    found = subprocess.run(['/usr/sbin/lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'],
                           capture_output=True, text=True)
    return any(line.startswith('n') and Path(line[1:]).is_relative_to(snapshot)
               for line in found.stdout.splitlines())


def listener_addresses(port: int) -> list[str]:
    found = subprocess.run(['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-Fn'],
                           capture_output=True,text=True)
    return [line[1:] for line in found.stdout.splitlines() if line.startswith('n')]


def prepare(snapshot: Path) -> tuple[Path, dict[str, str]]:
    prefix = Path(sys.prefix).resolve()
    if prefix not in {(ROOT / 'backend' / name).resolve() for name in ('.venv', '.venv-native-py312')}:
        raise RuntimeError('Run with this worktree\'s managed Python environment')
    site = prefix / 'lib/python3.12/site-packages'
    modules = ROOT / 'frontend/node_modules'
    if not site.is_dir() or modules.is_symlink() or not modules.is_dir():
        raise RuntimeError('Install this worktree before real startup verification')
    tracked = subprocess.check_output(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', 'ls-files', '-z'],
                                     cwd=ROOT, env={'PATH':os.defpath,'GIT_OPTIONAL_LOCKS':'0'}).decode().split('\0')
    selected = {'start.sh', 'scripts/native-common.sh', 'scripts/native_runtime.py', '.python-version', '.node-version'}
    hashes = {}
    for name in filter(None, tracked):
        parts = Path(name).parts
        if name not in selected and parts[0] not in {'backend', 'model_gateway', 'frontend'}:
            continue
        if any(part.startswith('.env') or part in {'.npmrc', '.venv', '.venv-py312', '.venv-native-py312', 'node_modules', 'dist', '__pycache__'} for part in parts):
            continue
        source = ROOT / name
        if source.is_symlink() or not source.is_file():
            raise RuntimeError('Refusing linked/missing source: ' + name)
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    (snapshot / 'start.sh').chmod(0o755)
    for name in ('data/papers', 'data/artifacts', 'data/uploads', 'data/wiki', '.cache/home', '.cache/tmp', '.cache/guards'):
        (snapshot / name).mkdir(parents=True, exist_ok=True)
    envpath = snapshot / 'backend/.venv'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(envpath)], check=True,
                   env={'PATH':os.defpath, 'HOME':str(snapshot / '.cache/home'), 'PYTHONDONTWRITEBYTECODE':'1'})
    newsite = envpath / 'lib/python3.12/site-packages'
    (newsite / 'worktree-dependencies.pth').write_text(str(site) + '\n')
    shutil.copytree(modules, snapshot / 'frontend/node_modules', symlinks=True)
    marker = {'status':'ready','venv':'backend/.venv','python_version':'.'.join(map(str,sys.version_info[:3]))}
    marker.update({name:hashlib.sha256((snapshot / name).read_bytes()).hexdigest()
                   for name in ('backend/requirements-dev.lock','frontend/package-lock.json')})
    (snapshot / '.cache/native-install.json').write_text(json.dumps(marker)+'\n')
    (snapshot / '.cache/guards/sitecustomize.py').write_text(PYTHON_GUARD)
    (snapshot / '.cache/guards/loopback.cjs').write_text(NODE_GUARD)
    (snapshot / '.cache/npm-user.npmrc').write_text('')
    (snapshot / '.cache/npm-global.npmrc').write_text('')
    (snapshot / 'browser-smoke.mjs').write_text(BROWSER)
    return site, hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    options = parser.parse_args()
    if sys.version_info[:2] != (3,12) or sys.version_info < (3,12,13):
        parser.error('Python 3.12.13+ within 3.12.x is required')
    node = os.environ.get('NODE_BIN') or shutil.which('node')
    if not node or not Path(options.browser).is_file():
        parser.error('Installed Node 24 and Chrome/Chromium are required; no download is performed')
    node = str(Path(node).resolve())
    cache = ROOT / '.cache'
    if cache.is_symlink():
        parser.error('Refusing linked cache')
    cache.mkdir(exist_ok=True)
    snapshot = Path(tempfile.mkdtemp(prefix='real-start-', dir=cache))
    report = {'snapshot':str(snapshot),'checks':{},'cleanup':{},'boundaries':{
        'real_services':['Uvicorn/FastAPI','Vite','empty local recommendation worker'],
        'task_worker':'supported external mode; no task worker or queued job execution',
        'dependencies':'new physical fixture venv reads this worktree site-packages; physical node_modules copy',
        'data':'new synthetic SQLite files only; original main/worktree data not copied or opened'}}
    run = browser_run = None
    api_port = web_port = 0
    owned = set()
    try:
        site, hashes = prepare(snapshot)
        report['source_sha256'] = hashes
        api_port, web_port = ports()
        python = snapshot / 'backend/.venv/bin/python'
        env = {'PATH':str(Path(node).parent)+os.pathsep+os.defpath+':/usr/sbin',
               'HOME':str(snapshot/'.cache/home'),'TMPDIR':str(snapshot/'.cache/tmp'),
               'PYTHON_BIN':str(python),'NODE_BIN':node,'PYTHONDONTWRITEBYTECODE':'1',
               'npm_config_userconfig':str(snapshot/'.cache/npm-user.npmrc'),
               'npm_config_globalconfig':str(snapshot/'.cache/npm-global.npmrc'),
               'npm_config_cache':str(snapshot/'.cache/npm'),
               'npm_config_update_notifier':'false','npm_config_audit':'false','npm_config_fund':'false',
               'PYTHONPATH':str(snapshot/'.cache/guards'),'NODE_OPTIONS':'--require '+json.dumps(str(snapshot/'.cache/guards/loopback.cjs')),
               'KNOWRA_SMOKE_ROOT':str(snapshot),'KNOWRA_SMOKE_SITE':str(site),
               'KNOWRA_WORKER_MODE':'external','KNOWRA_DEPLOY_MODE':'local',
               'KNOWRA_TASK_DB':str(snapshot/'data/tasks.db'),'KNOWRA_REC_DB':str(snapshot/'data/recommendations.db'),
               'BACKEND_PORT':str(api_port),'FRONTEND_PORT':str(web_port)}
        # An empty, synthetic SQLite header is sufficient to exercise existing-
        # data consent. App schema initialization then operates on this new DB.
        import sqlite3
        with sqlite3.connect(snapshot/'data/knowledge.db') as db:
            db.execute('PRAGMA user_version=0')
        before = hashlib.sha256((snapshot/'data/knowledge.db').read_bytes()).hexdigest()
        for label, args, wanted in [('preflight',['--check'],0),('startup_guard',[],1)]:
            result = subprocess.run([str(snapshot/'start.sh'),*args],cwd=snapshot,env=env,capture_output=True,text=True,timeout=30)
            (snapshot/'.cache'/f'{label}.log').write_text(result.stdout+result.stderr)
            report['checks'][label] = result.returncode
            if (result.returncode==0) != (wanted==0):
                raise RuntimeError(label+' returned an unexpected result; inspect its log')
            if hashlib.sha256((snapshot/'data/knowledge.db').read_bytes()).hexdigest()!=before:
                raise RuntimeError(label+' modified the synthetic database')
        report['checks']['existing_db_unchanged_before_launch'] = True
        env['KNOWRA_ALLOW_EXISTING_DATA']='1'  # Applies only to this synthetic DB.
        with (snapshot/'.cache/start.log').open('w') as log:
            run = subprocess.Popen([str(snapshot/'start.sh')],cwd=snapshot,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        owned.add(run.pid)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        api=f'http://127.0.0.1:{api_port}'; web=f'http://127.0.0.1:{web_port}'
        deadline=time.monotonic()+60
        while True:
            owned.update(descendants(run.pid))
            if run.poll() is not None: raise RuntimeError('Real launcher exited before readiness')
            try:
                with opener.open(api+'/',timeout=1) as response: health=json.load(response)
                with opener.open(web+'/',timeout=1) as response: html=response.read().decode()
                if health.get('message')=='Knowra API is running' and '<div id="root"></div>' in html: break
            except (OSError,urllib.error.URLError): pass
            if time.monotonic()>deadline: raise RuntimeError('Real services did not become ready in 60 seconds')
            time.sleep(.2)
        report['checks']['api_health']=health
        report['checks']['vite_http']=True
        report['urls']={'api':api,'web':web}
        report['listeners']={str(port):listener_addresses(port) for port in (api_port,web_port)}
        if any(addresses!=[f'127.0.0.1:{port}'] for port,addresses in report['listeners'].items()):
            raise RuntimeError('Actual API/Vite listener is not exclusively IPv4 loopback')
        print('Real API and Vite ready: '+json.dumps(report['urls']),flush=True)
        browser_output=snapshot/'.cache/browser-result.json'
        # Playwright's own pipe-based browser transport uses this process only;
        # browser context routing restricts every page request to our endpoints.
        browser_env={key:value for key,value in env.items() if key!='NODE_OPTIONS'}
        with (snapshot/'.cache/browser.log').open('w') as log:
            browser_run=subprocess.Popen([node,str(snapshot/'browser-smoke.mjs'),web,api,options.browser,str(browser_output)],
                                         cwd=snapshot,env=browser_env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        owned.add(browser_run.pid)
        deadline=time.monotonic()+120
        while browser_run.poll() is None:
            owned.update(descendants(browser_run.pid))
            if time.monotonic()>deadline:
                browser_run.terminate()
                browser_run.wait(timeout=10)
                raise RuntimeError('Real browser interaction timed out after 120 seconds')
            time.sleep(.1)
        report['checks']['browser_exit']=browser_run.returncode
        report['cleanup']['browser_runner_exited']=True
        report['browser']=json.loads(browser_output.read_text()) if browser_output.exists() else None
        if browser_run.returncode: raise RuntimeError('Real browser interaction failed; inspect browser.log')
        owned.update(descendants(run.pid))
        owned.update(row['pid'] for row in read_json_lines(snapshot/'.cache/processes.jsonl') if alive(row['pid']))
        if run.poll() is not None:
            raise RuntimeError('Launcher exited unexpectedly before the requested shutdown')
        report['cleanup']['observed_pids']=sorted(owned)
        run.send_signal(signal.SIGTERM)
        run.wait(timeout=20)
        if run.returncode not in (0,130,143,-signal.SIGTERM):
            raise RuntimeError('Launcher returned an unexpected shutdown code: '+str(run.returncode))
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and (any(alive(pid) for pid in owned) or listening(api_port) or listening(web_port)):
            time.sleep(.1)
        report['cleanup']['launcher_returncode']=run.returncode
        report['cleanup']['remaining_pids']=[pid for pid in sorted(owned) if alive(pid)]
        report['cleanup']['open_ports']=[port for port in (api_port,web_port) if listening(port)]
        if report['cleanup']['remaining_pids'] or report['cleanup']['open_ports']:
            raise RuntimeError('Normal launcher exit left descendants or open ports')
        with sqlite3.connect(snapshot/'data/tasks.db') as db:
            jobs=db.execute('SELECT kind,status,attempt FROM jobs').fetchall()
        with sqlite3.connect(snapshot/'data/recommendations.db') as db:
            recommendation_batches=db.execute('SELECT COUNT(*) FROM rec_batches').fetchone()[0]
            recommendation_usage=db.execute('SELECT COUNT(*) FROM rec_usage').fetchone()[0]
        report['checks']['synthetic_queue']={'jobs':jobs,'recommendation_batches':recommendation_batches,'recommendation_usage':recommendation_usage}
        if not jobs or any(tuple(row)!=('scan','queued',0) for row in jobs) or recommendation_batches or recommendation_usage:
            raise RuntimeError('Synthetic queue state exceeded an unexecuted scan submission')
        process_rows=read_json_lines(snapshot/'.cache/processes.jsonl')
        if any(any(str(arg).endswith('/task_worker.py') for arg in row['argv']) for row in process_rows):
            raise RuntimeError('Task worker started despite external mode')
        report['checks']['task_worker_started']=False
        report['checks']['recommendation_worker_started']=any(any(str(arg).endswith('/recommendation_worker.py') for arg in row['argv']) for row in process_rows)
        if not report['checks']['recommendation_worker_started']:
            raise RuntimeError('Real recommendation worker was not observed')
        violations=read_json_lines(snapshot/'.cache/violations.jsonl')
        report['violations']=violations
        if violations: raise RuntimeError('Isolation guard recorded blocked operations')
        report['exit_code']=0
    except BaseException as error:
        report['exit_code']=1
        report['error']=str(error)
        print('Real startup smoke failed: '+str(error),file=sys.stderr,flush=True)
    finally:
        if run is not None:
            owned.update(descendants(run.pid))
        if browser_run is not None:
            owned.update(descendants(browser_run.pid))
        owned.update(row['pid'] for row in read_json_lines(snapshot/'.cache/processes.jsonl') if alive(row['pid']))
        # Recovery touches only PIDs registered/descended from this fixture.
        survivors=[pid for pid in owned if owned_alive(pid,snapshot)]
        if survivors:
            report['cleanup']['recovery_needed']=survivors
            for pid in survivors:
                try: os.kill(pid,signal.SIGTERM)
                except ProcessLookupError: pass
            time.sleep(.5)
            for pid in survivors:
                if owned_alive(pid,snapshot):
                    try: os.kill(pid,signal.SIGKILL)
                    except ProcessLookupError: pass
        if run is not None:
            try: run.wait(timeout=5)
            except subprocess.TimeoutExpired: pass
        if browser_run is not None:
            try: browser_run.wait(timeout=5)
            except subprocess.TimeoutExpired: pass
        report['cleanup']['final_open_ports']=[port for port in (api_port,web_port) if port and listening(port)]
        report['cleanup']['final_remaining_pids']=[pid for pid in sorted(owned) if alive(pid)]
        report['violations']=read_json_lines(snapshot/'.cache/violations.jsonl')
        report['blocked_probes']=read_json_lines(snapshot/'.cache/blocked-probes.jsonl')
        if report['cleanup']['final_open_ports'] or report['cleanup']['final_remaining_pids'] or report['violations']:
            report['exit_code']=1
        (snapshot/'.cache/smoke-result.json').write_text(json.dumps(report,indent=2)+'\n')
        (cache/'native-start-result.json').write_text(json.dumps(report,indent=2)+'\n')
        print('Real startup evidence: '+str(cache/'native-start-result.json'),flush=True)
    return report['exit_code']


if __name__=='__main__':
    raise SystemExit(main())
