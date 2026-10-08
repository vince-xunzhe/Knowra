"""Job handlers execute only in the isolated worker, never in the HTTP process."""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class Context:
    def __init__(self, queue, job):
        self.queue, self.job = queue, job
        self.checkpoints = job['checkpoints'] or {}
        self.channel = None
        self.source = None
        self.progress = {}

    def step(self, key, run):
        if key in self.checkpoints:
            return self.checkpoints[key]
        result = run()
        self.checkpoints[key] = result
        self.queue.update(self.job['job_id'], checkpoints=self.checkpoints)
        return result

    def watch(self, channel, source):
        self.channel, self.source = channel, source
        self.flush()

    def flush(self):
        if self.source is not None:
            state = copy.deepcopy(self.source)
            self.queue.publish(self.job['job_id'], self.channel, state)
            progress = {key: state[key] for key in ('running', 'total', 'done', 'errors', 'current', 'phase') if key in state}
            self.queue.update(self.job['job_id'], progress=dict(self.progress, channel=self.channel, state=progress))

    def phase(self, label):
        self.progress = {'label': label}
        self.queue.update(self.job['job_id'], progress=self.progress)


def process_papers(ctx, payload):
    from database import SessionLocal, assert_processing_schema
    from models import Paper
    from routers import papers
    ids = payload['paper_ids']
    # Fail before model calls or graph mutations when an older local database
    # is missing a column required by the currently-running code.
    assert_processing_schema()
    papers._mark_processing_started(ids)
    ctx.watch('papers', papers.processing_state)
    if any('paper:' + str(pid) not in ctx.checkpoints for pid in ids):
        from config import load_config, task_model_name
        from model_gateway.runtime import preflight_task_runtime
        from services.pipeline_manifest import extraction_is_reusable
        cfg = load_config()
        model = task_model_name(cfg, 'paper_extract')
        with SessionLocal() as db:
            remaining = db.query(Paper).filter(Paper.id.in_(ids)).all()
            requires_model = any(
                not extraction_is_reusable(paper, cfg, model)
                for paper in remaining
                if 'paper:' + str(paper.id) not in ctx.checkpoints
            )
        if requires_model:
            preflight_task_runtime(cfg, 'paper_extract')
    for pid in ids:
        key = 'paper:' + str(pid)
        if key in ctx.checkpoints:
            papers.processing_state['done'] += 1
            papers.processing_state['succeeded'] += 1
            continue
        def run_one():
            with SessionLocal() as db:
                paper = db.query(Paper).filter(Paper.id == pid).first()
                if not paper:
                    raise ValueError('Paper no longer exists: ' + str(pid))
                if payload.get('force') and 'prepared:' + str(pid) not in ctx.checkpoints:
                    papers._prepare_reprocess(
                        db,
                        paper,
                        reset_extraction=payload.get('reset_extraction', False),
                    )
                    ctx.step('prepared:' + str(pid), lambda: True)
                elif paper.processed:
                    papers.processing_state['done'] += 1
                    papers.processing_state['succeeded'] += 1
                    return True
            before = papers.processing_state['errors']
            papers._process_single(pid)
            if papers.processing_state['errors'] > before:
                raise RuntimeError(papers.processing_state['failed_papers'][-1]['reason'])
            return True
        try:
            ctx.step(key, run_one)
        except Exception as exc:
            if not any(str(x['id']) == str(pid) for x in papers.processing_state['failed_papers']):
                papers._record_unhandled_processing_failure(pid, exc)
        ctx.flush()
    state = papers.processing_state
    state.update(running=False, current='', finished_at=now(),
                 last_message=f"处理完成：{state['succeeded']} 篇成功，{state['errors']} 篇失败")
    ctx.flush()
    if state['errors']:
        raise RuntimeError(state['last_message'])
    return dict(state)


def compile_items(ctx, payload):
    from database import SessionLocal
    from models import Paper, KnowledgeNode
    from config import load_config, task_model_id
    from routers import wiki
    from services.wiki_compiler import list_publishable_concept_nodes
    def freshness():
        with SessionLocal() as db:
            return wiki.compute_freshness_summary(db)
    freshness_before = ctx.step('freshness_before', freshness)
    def targets():
        with SessionLocal() as db:
            if payload.get('all') == 'papers':
                return {'paper_ids': [p[0] for p in db.query(Paper.id).filter(Paper.processed.is_(True)).all()], 'concept_ids': []}
            if payload.get('all') == 'concepts':
                return {'paper_ids': [], 'concept_ids': [n.id for n in list_publishable_concept_nodes(db, include_embeddings=False)]}
            if payload.get('dirty'):
                f = wiki.compute_freshness_summary(db)
                p, c = wiki._dirty_ids_from_freshness(f, include_missing=payload.get('include_missing', True),
                                                    include_stale=payload.get('include_stale', True))
                return {'paper_ids': sorted(set(p + [str(i) for i in payload.get('paper_ids', [])])),
                        'concept_ids': sorted(set(c + [str(i) for i in payload.get('concept_ids', [])]))}
            return {'paper_ids': payload.get('paper_ids', []), 'concept_ids': payload.get('concept_ids', [])}
    selected = ctx.step('targets:' + ctx.progress.get('label', 'compile'), targets)
    items = [('paper', str(pid)) for pid in selected['paper_ids']] + [('concept', str(cid)) for cid in selected['concept_ids']]
    wiki._begin(payload.get('all', 'dirty'), len(items), task_model_id(load_config(), 'wiki_compile'))
    wiki.compile_state['failed_items'] = []
    ctx.watch('compile', wiki.compile_state)
    outputs = {'papers': [], 'concepts': []}
    skipped = []
    for kind, item_id in items:
        wiki._set_current(f'{kind} · {item_id}', item_id=item_id, item_kind=kind)
        ctx.flush()
        def compile_one():
            with SessionLocal() as db:
                cfg = load_config()
                model = task_model_id(cfg, 'wiki_compile')
                if kind == 'paper':
                    paper = db.query(Paper).filter(Paper.id == item_id).first()
                    if not paper or not paper.processed or not paper.raw_llm_response:
                        return {'skip': 'paper_not_processed' if paper else 'paper_not_found'}
                    path = wiki.compile_paper_page(paper, cfg.get('openai_api_key', ''), model)
                else:
                    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == item_id).first()
                    if not node:
                        return {'skip': 'concept_not_found'}
                    path = wiki.compile_concept_page(node, db, cfg.get('openai_api_key', ''), model)
                if path is None:
                    return {'skip': 'nothing_publishable_to_compile'}
                return {'path': str(path), 'filename': path.name}
        try:
            result = ctx.step(f'wiki:{kind}:{item_id}', compile_one)
            if result.get('skip'):
                skipped.append({'kind': kind, 'id': item_id, 'reason': result['skip']})
            else:
                outputs['papers' if kind == 'paper' else 'concepts'].append(dict(result, **{kind + '_id': item_id}))
            wiki._tick(True)
        except Exception as exc:
            wiki._tick(False, exc)
            wiki._record_failure(kind, item_id, item_id, exc)
        ctx.flush()
    cleanup = {}
    with SessionLocal() as db:
        cleanup['papers'] = wiki.reconcile_paper_pages_dir(db, prune_orphans=True)
        cleanup['concepts'] = wiki.reconcile_concept_pages_dir(db, prune_orphans=True)
    wiki.wiki_index.refresh_index()
    wiki.wiki_search_service.rebuild_index()
    result = {'requested': dict(selected, include_missing=payload.get('include_missing', False),
                              include_stale=payload.get('include_stale', False)),
            'compiled': {k: len(v) for k, v in outputs.items()}, 'compiled_items': outputs,
            'failed': {'count': wiki.compile_state['errors'], 'items': wiki.compile_state['failed_items']},
            'skipped': {'count': len(skipped), 'items': skipped}, 'cleanup': cleanup,
            'freshness_before': freshness_before, 'freshness_after': freshness()}
    ctx.queue.update(ctx.job['job_id'], result=result)
    wiki._finish()
    ctx.flush()
    if wiki.compile_state['errors']:
        raise RuntimeError(wiki.compile_state['last_error'])
    if payload.get('single'):
        if skipped:
            raise ValueError(skipped[0]['reason'])
        return next(iter(outputs['papers'] or outputs['concepts']))
    return result


def promote(ctx, payload):
    from routers import promotion
    body = promotion.RunRequest(**payload)
    promotion._try_begin_run(body)
    ctx.watch('promotion', promotion.promotion_run_state)
    promotion._run_promotion_background(body.force_all, body.use_llm)
    ctx.flush()
    state = promotion.promotion_run_state
    error = state.get('error') or ((state.get('result') or {}).get('llm') or {}).get('error')
    if error:
        raise RuntimeError(error)
    return state['result']


def lint(ctx, payload):
    from database import SessionLocal
    from services.wiki_lint_service import run_lint
    state = {'job_id': ctx.job['job_id'], 'status': 'running', 'running': True, 'phase': '准备检查',
             'error': None, 'started_at': now(), 'finished_at': None, 'use_llm': payload.get('use_llm', True)}
    ctx.watch('lint', state)
    with SessionLocal() as db:
        result = run_lint(db, use_llm=state['use_llm'], on_progress=lambda phase: state.update(phase=phase))
    error = (result.get('judgment') or {}).get('error')
    state.update(status='warning' if error else 'completed', running=False, error=error, finished_at=now(), phase='检查完成')
    ctx.flush()
    if error:
        ctx.queue.update(ctx.job['job_id'], result=result)
        raise RuntimeError(error)
    return result


def pipeline(ctx, payload):
    from database import SessionLocal
    from routers import papers, promotion
    from models import Paper
    def scan():
        with SessionLocal() as db:
            return papers.scan_papers(db)
    ctx.phase('扫描论文目录')
    ctx.step('scan', scan)
    def pending():
        with SessionLocal() as db:
            return [r[0] for r in db.query(Paper.id).filter(Paper.processed.is_(False), Paper.error.is_(None)).all()]
    ids = ctx.step('pending', pending)
    ctx.phase('处理论文')
    ctx.step('extract', lambda: process_papers(ctx, {'paper_ids': ids}))
    with SessionLocal() as db:
        if papers.paper_work_counts(db)['failed_count']:
            raise RuntimeError('有失败论文，请先重试失败论文，再恢复全流程。')
    ctx.phase('自动筛选候选概念')
    ctx.step('promote', lambda: promote(ctx, {'use_llm': payload.get('use_llm', True), 'force_all': False}))
    ctx.phase('确认筛选结果')
    def accept():
        with SessionLocal() as db:
            return promotion.accept_llm_proposals(db)
    ctx.step('accept', accept)
    ctx.phase('增量编译 Wiki')
    ctx.step('wiki', lambda: compile_items(ctx, {'dirty': True}))
    ctx.phase('运行健康检查')
    ctx.step('lint', lambda: lint(ctx, {'use_llm': payload.get('use_llm', True)}))
    ctx.phase('本地全流程完成')
    return {'message': '本地全流程完成；云端同步由已登录客户端执行。'}


def scan(ctx, payload):
    from database import SessionLocal
    from routers import papers
    ctx.phase('扫描论文目录')
    with SessionLocal() as db:
        return papers.scan_papers(db)


HANDLERS = {'scan': scan, 'papers': process_papers, 'compile': compile_items, 'promotion': promote, 'lint': lint, 'pipeline': pipeline}


def execute(queue, job):
    ctx = Context(queue, job)
    stopped = threading.Event()
    def progress():
        while not stopped.wait(1):
            try:
                ctx.flush()
            except Exception:
                import traceback
                traceback.print_exc()
    monitor = threading.Thread(target=progress, daemon=True)
    monitor.start()
    try:
        result = HANDLERS[job['kind']](ctx, job['payload'])
        queue.update(job['job_id'], status='completed', result=result, error=None)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        queue.update(job['job_id'], status='failed', error=f'{type(exc).__name__}: {exc}')
    finally:
        stopped.set()
        monitor.join()
        ctx.flush()
