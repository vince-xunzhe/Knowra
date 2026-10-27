#!/usr/bin/env python3
"""Exercise framework-assisted agent preview with synthetic SQLite and real React.

Run with this worktree's managed Python and NODE_BIN pointing at Node 24.
Reuses the native smoke snapshot and guards, but deliberately mounts only the
production research routers without main's lifespan: no worker or AI can run.
It neither reads nor starts the user's normal database, configuration or pages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import check_native_start as native

ROOT = Path(__file__).resolve().parents[1]

SEED = 'from feature_app import seed; seed()'
APP = (ROOT/'scripts/domain_framework_fixture.py').read_text()

VITE = '''import { createServer } from './frontend/node_modules/vite/dist/node/index.js';
const server = await createServer({root:process.cwd()+'/frontend',configFile:process.cwd()+'/frontend/vite.config.ts',
 configLoader:'native',envDir:false,cacheDir:process.cwd()+'/.cache/vite',
 server:{host:'127.0.0.1',port:Number(process.env.FRONTEND_PORT),strictPort:true}});
await server.listen();
for (const sig of ['SIGTERM','SIGINT']) process.once(sig,async()=>{await server.close();process.exit(0);});
'''


def copy_new_sources(snapshot: Path) -> dict[str, str]:
    """Include this iteration's untracked source files, never caches or data."""
    paths = subprocess.check_output(['git','--no-optional-locks','-c','core.fsmonitor=false','ls-files',
                                     '-z','--others','--exclude-standard','--','frontend','backend'],
                                    cwd=ROOT,env={'PATH':os.defpath,'GIT_OPTIONAL_LOCKS':'0'}).decode().split('\0')
    hashes = {}
    for name in filter(None, paths):
        source = ROOT / name
        if source.is_symlink() or any(p.startswith('.env') or p in {'node_modules','data','.cache'} for p in source.relative_to(ROOT).parts):
            raise RuntimeError('Refusing unsafe feature source '+name)
        target = snapshot / name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    return hashes


def wait_ready(processes, api, web):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError('Fixture service exited before readiness')
        try:
            with opener.open(api+'/',timeout=1) as response: ready=json.load(response)
            with opener.open(web+'/tests/research-canvas-harness.html',timeout=1) as response: response.read()
            if ready.get('fixture')=='research-canvas': return
        except (OSError,urllib.error.URLError): pass
        time.sleep(.1)
    raise RuntimeError('Fixture services did not become ready within 60 seconds')


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',default='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    parser.add_argument('--baseline', action='store_true', help='Render the fetched main baseline UI in the disposable snapshot only')
    options=parser.parse_args()
    node=os.environ.get('NODE_BIN') or shutil.which('node')
    if not node or not Path(options.browser).is_file(): parser.error('Installed Node 24 and Chrome required')
    node=str(Path(node).resolve())
    version=subprocess.check_output([node,'-p','process.versions.node'],text=True).strip()
    if tuple(map(int,version.split('.'))) < (24,14,1) or version.split('.')[0]!='24': parser.error('Use Node 24.14.1+ within 24.x')
    cache=ROOT/'.cache'
    if cache.is_symlink() or not cache.resolve().is_relative_to(ROOT): parser.error('Refusing linked/outside cache')
    cache.mkdir(exist_ok=True)
    snapshot=Path(tempfile.mkdtemp(prefix='domain-framework-',dir=cache))
    report={'snapshot':str(snapshot),'node':version,'python':sys.version.split()[0],
            'boundary':'Synthetic SQLite; real production research routers without app lifespan or workers; real React editors; loopback only',
            'exit_code':1,'cleanup':{}}
    runs=[]; logs=[]; owned=set(); api_port=web_port=0
    try:
        site,hashes=native.prepare(snapshot)
        hashes.update(copy_new_sources(snapshot))
        if options.baseline:
            baseline = '803461809867c9d78133839b1ac477e1b396b52c'
            names = subprocess.check_output(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', 'ls-tree', '-r', '--name-only', baseline, '--', 'frontend/src'], cwd=ROOT).decode().splitlines()
            for name in names:
                target = snapshot/name
                target.write_bytes(subprocess.check_output(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', 'show', baseline+':'+name], cwd=ROOT))
                hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
            report['baseline'] = baseline
        report['source_sha256']=hashes
        report['harness_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        (snapshot/'backend/feature_app.py').write_text(APP)
        (snapshot/'feature-vite.mjs').write_text(VITE)
        api_port,web_port=native.ports()
        python=snapshot/'backend/.venv/bin/python'
        env={'PATH':str(Path(node).parent)+os.pathsep+os.defpath,'HOME':str(snapshot/'.cache/home'),
             'TMPDIR':str(snapshot/'.cache/tmp'),'PYTHONDONTWRITEBYTECODE':'1',
             'PYTHONPATH':str(snapshot/'.cache/guards'),'KNOWRA_SMOKE_ROOT':str(snapshot),
             'KNOWRA_SMOKE_SITE':str(site),'NODE_OPTIONS':'--require '+json.dumps(str(snapshot/'.cache/guards/loopback.cjs')),
             'KNOWRA_FRAMEWORK_BASELINE':'1' if options.baseline else '0','KNOWRA_WORKER_MODE':'external','KNOWRA_DEPLOY_MODE':'local',
             'KNOWRA_TASK_DB':str(snapshot/'data/tasks.db'),'KNOWRA_REC_DB':str(snapshot/'data/recommendations.db'),
             'BACKEND_PORT':str(api_port),'FRONTEND_PORT':str(web_port),
             'VITE_API_PROXY_TARGET':f'http://127.0.0.1:{api_port}'}
        seed=subprocess.run([str(python),'-c',SEED],cwd=snapshot/'backend',env=env,capture_output=True,text=True,timeout=30)
        (snapshot/'.cache/seed.log').write_text(seed.stdout+seed.stderr)
        if seed.returncode: raise RuntimeError('Fixture seed failed; see seed.log')
        for name,cmd,cwd in [('api',[str(python),'-m','uvicorn','feature_app:app','--host','127.0.0.1','--port',str(api_port)],snapshot/'backend'),
                              ('web',[node,str(snapshot/'feature-vite.mjs')],snapshot)]:
            log=(snapshot/f'.cache/{name}.log').open('w');logs.append(log)
            run=subprocess.Popen(cmd,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            runs.append(run);owned.add(run.pid)
        api=f'http://127.0.0.1:{api_port}';web=f'http://127.0.0.1:{web_port}'
        wait_ready(runs,api,web)
        report['urls']={'api':api,'web':web}
        browser_env={k:v for k,v in env.items() if k!='NODE_OPTIONS'}
        browser_log=(snapshot/'.cache/browser.log').open('w');logs.append(browser_log)
        browser=subprocess.Popen([node,str(snapshot/'frontend/tests/domain-framework-ui.mjs'),web,api,options.browser,str(snapshot/'.cache')],
                                 cwd=snapshot,env=browser_env,stdout=browser_log,stderr=subprocess.STDOUT,start_new_session=True)
        runs.append(browser);owned.add(browser.pid)
        deadline=time.monotonic()+180
        while browser.poll() is None:
            for run in runs: owned.update(native.descendants(run.pid))
            if time.monotonic()>deadline: raise RuntimeError('Browser feature checks timed out after 180 seconds')
            time.sleep(.1)
        result=snapshot/'.cache/browser-result.json'
        if result.exists(): report['browser']=json.loads(result.read_text())
        if browser.returncode: raise RuntimeError('Browser feature checks failed; see browser.log')
        report['exit_code']=0
    except BaseException as error:
        report['error']=str(error)
    finally:
        for run in runs: owned.update(native.descendants(run.pid))
        for run in reversed(runs):
            if run.poll() is None:
                run.send_signal(signal.SIGTERM)
                try: run.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    report['exit_code']=1;report['cleanup']['forced']=True
                    os.killpg(run.pid,signal.SIGKILL);run.wait(timeout=5)
        for log in logs: log.close()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and any(native.alive(pid) for pid in owned): time.sleep(.1)
        report['cleanup']['remaining_pids']=[pid for pid in sorted(owned) if native.alive(pid)]
        report['cleanup']['open_ports']=[p for p in (api_port,web_port) if p and native.listening(p)]
        report['cleanup']['api_shutdown_complete']='Application shutdown complete.' in (snapshot/'.cache/api.log').read_text() if (snapshot/'.cache/api.log').exists() else False
        report['violations']=native.read_json_lines(snapshot/'.cache/violations.jsonl')
        report['workers_started']=[row for row in native.read_json_lines(snapshot/'.cache/processes.jsonl') if any(str(a).endswith(('/task_worker.py','/recommendation_worker.py')) for a in row['argv'])]
        task_db=snapshot/'data/tasks.db'
        if task_db.exists():
            with sqlite3.connect(task_db) as db: report['jobs']=db.execute('SELECT kind,status,attempt FROM jobs').fetchall()
        if report['cleanup']['remaining_pids'] or report['cleanup']['open_ports'] or report['violations'] or report['workers_started'] or report.get('jobs') or not report['cleanup']['api_shutdown_complete']:
            report['exit_code']=1
        (snapshot/'.cache/feature-result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        (cache/('domain-framework-baseline-result.json' if options.baseline else 'domain-framework-result.json')).write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        print('Domain framework evidence: '+str(cache/('domain-framework-baseline-result.json' if options.baseline else 'domain-framework-result.json')),flush=True)
        if report.get('error'): print(report['error'],file=sys.stderr)
    return report['exit_code']


if __name__=='__main__':
    raise SystemExit(main())
