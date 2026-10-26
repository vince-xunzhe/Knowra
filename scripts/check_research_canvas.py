#!/usr/bin/env python3
"""Exercise import/export with synthetic SQLite, real routers and real React.

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

SEED = r'''
import json
from database import Base, engine, SessionLocal, DB_PATH
from models import Paper, DomainWorkspace, RoadmapWorkspace, RoadmapPaper, RoadmapRun
from routers.domains import WorkspaceState
from services.paper_category_service import set_active_categories
Base.metadata.create_all(bind=engine)
categories = ['LLM', '空图', '大型图', '超大跨度', '其他']
set_active_categories(categories)
def node(key, kind='paper', x=0, y=0, width=280, height=160, paper=None, text='', role=None):
    return dict(id=key, kind=kind, x=x, y=y, width=width, height=height,
                paperId=paper, text=text, groupId=None, locked=False, roadmapRole=role)
def board(key, name, nodes=(), edges=()):
    return dict(id=key, name=name, category=name, nodes=list(nodes), edges=list(edges),
                viewport=dict(x=410,y=130,zoom=.65))
shared_title='同名论文：中文标签与长标题仍应完整显示 / A complete long research title'
papers=[('paper-a',shared_title,True),('paper-b',shared_title,True),
        ('paper-c','尚未导入的合成论文：跨模态研究',True),
        ('paper-d','远处已导入节点：完整图下载必须包含我',True),
        ('paper-pending','没有论文回顾的合成条目',False)]
domain_nodes=[node('domain-a',x=-100,y=100,paper='paper-a',text='本地笔记也需要完整保留'),
              node('domain-d',x=1400,y=650,paper='paper-d'),
              node('domain-note','text',x=560,y=0,width=400,height=120,text='中文研究架构图\n真实后端与当前布局'),
              node('domain-group','group',x=-150,y=-50,width=1900,height=1000,text='合成研究分组')]
domain_edges=[dict(id='domain-edge',source='domain-a',target='domain-d',label='基于已有方法改进：中文关系标签',locked=False)]
large_nodes=[node('large-'+str(i),'text',x=(i%12)*480,y=(i//12)*290,width=320,height=130,
                  text='中文节点 '+str(i)+' / scalable research graph') for i in range(120)]
domain_boards=[board('domain-llm','LLM',domain_nodes,domain_edges),board('domain-empty','空图'),
               board('domain-large','大型图',large_nodes),
               board('domain-extreme','超大跨度',[node('far-left','text',x=-900000,text='左端'),node('far-right','text',x=900000,text='右端')]),
               board('domain-other','其他')]
roadmap_nodes=[node('roadmap-topic','text',x=-160,y=60,width=320,height=130,text='多模态学习技术发展路线',role='topic'),
               node('roadmap-branch','text',x=300,y=400,width=300,height=130,text='视觉语言模型与长中文研究分支',role='branch'),
               node('roadmap-a',x=840,y=80,paper='roadmap-paper-a',role='milestone'),
               node('roadmap-b',x=1450,y=640,paper='roadmap-paper-b')]
roadmap_edges=[dict(id='topic-branch',source='roadmap-topic',target='roadmap-branch',label='研究方向',locked=False,roadmapStructure=True),
               dict(id='branch-paper',source='roadmap-branch',target='roadmap-a',label='代表工作',locked=False,roadmapStructure=True),
               dict(id='evolution',source='roadmap-a',target='roadmap-b',label='改进：从视觉对齐到跨模态理解',locked=False)]
roadmap_boards=[board('roadmap-llm','LLM',roadmap_nodes,roadmap_edges)]+[board('roadmap-'+str(i),name) for i,name in enumerate(categories[1:])]
with SessionLocal() as db:
    for key,title,processed in papers:
        db.add(Paper(id=key,filepath=str(DB_PATH.parent/'papers'/f'{key}.pdf'),filename=f'{key}.pdf',
                     file_hash='synthetic-'+key,title=title,processed=processed,paper_category_override='LLM',
                     raw_llm_response=json.dumps(dict(title=title,year=2026,core_contribution='合成贡献，未调用模型',
                         abstract_summary='合成摘要',problem='合成研究问题',motivation='合成研究动机'),ensure_ascii=False)))
    ds=WorkspaceState.model_validate(dict(activeId='domain-llm',boards=domain_boards)).model_dump()
    rs=WorkspaceState.model_validate(dict(activeId='roadmap-llm',boards=roadmap_boards)).model_dump()
    db.add(DomainWorkspace(id=1,revision=1,state=ds,admitted={'domain-a':'paper-a','domain-d':'paper-d'}))
    db.add(RoadmapWorkspace(id=1,revision=1,state=rs))
    for index in ('a','b'):
        db.add(RoadmapPaper(id='roadmap-paper-'+index,data=dict(id='roadmap-paper-'+index,
            title='识途合成论文 '+index+'：完整中文标签与技术路线研究',year='2026',authors=['Synthetic Author'],
            abstract='仅用于隔离测试',reason='测试架构图导出',branch='LLM',arxivId=None,doi=None,
            sourceUrl='https://example.invalid/synthetic-'+index)))
    db.add(RoadmapRun(id='synthetic-report',board_id='roadmap-llm',status='applied',snapshot={},
        proposal={'report':'# 合成研报\n\n```mermaid\nflowchart LR\n A[中文研究主题] --> B[视觉语言架构]\n B --> C[跨模态理解与长标签]\n```\n'}))
    db.commit()
engine.dispose()
'''

APP = '''from fastapi import FastAPI
from routers import domains, domain_agent, roadmaps
app = FastAPI()
app.include_router(domains.router)
app.include_router(domain_agent.router)
app.include_router(roadmaps.router)
@app.get('/')
def health(): return {'fixture':'research-canvas'}
'''

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
    options=parser.parse_args()
    node=os.environ.get('NODE_BIN') or shutil.which('node')
    if not node or not Path(options.browser).is_file(): parser.error('Installed Node 24 and Chrome required')
    node=str(Path(node).resolve())
    version=subprocess.check_output([node,'-p','process.versions.node'],text=True).strip()
    if tuple(map(int,version.split('.'))) < (24,14,1) or version.split('.')[0]!='24': parser.error('Use Node 24.14.1+ within 24.x')
    cache=ROOT/'.cache'
    if cache.is_symlink() or not cache.resolve().is_relative_to(ROOT): parser.error('Refusing linked/outside cache')
    cache.mkdir(exist_ok=True)
    snapshot=Path(tempfile.mkdtemp(prefix='research-canvas-',dir=cache))
    report={'snapshot':str(snapshot),'node':version,'python':sys.version.split()[0],
            'boundary':'Synthetic SQLite; real production research routers without app lifespan or workers; real React editors; loopback only',
            'exit_code':1,'cleanup':{}}
    runs=[]; logs=[]; owned=set(); api_port=web_port=0
    try:
        site,hashes=native.prepare(snapshot)
        hashes.update(copy_new_sources(snapshot))
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
             'KNOWRA_WORKER_MODE':'external','KNOWRA_DEPLOY_MODE':'local',
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
        browser=subprocess.Popen([node,str(snapshot/'frontend/tests/research-canvas-ui.mjs'),web,api,options.browser,str(snapshot/'.cache')],
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
        (cache/'research-canvas-result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        print('Research canvas evidence: '+str(cache/'research-canvas-result.json'),flush=True)
        if report.get('error'): print(report['error'],file=sys.stderr)
    return report['exit_code']


if __name__=='__main__':
    raise SystemExit(main())
