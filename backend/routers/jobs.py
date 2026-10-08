"""Durable tasks: submitting is fast; polling never opens the library DB."""
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services import task_runtime as runtime
from services.task_store import TaskConflict

router = APIRouter(prefix='/api', tags=['jobs'])


def public_job(job):
    if job is None:
        return None
    result = {key: job.get(key) for key in ('job_id', 'kind', 'channel', 'status', 'progress',
            'result', 'error', 'created', 'updated', 'attempt')}
    if job['status'] in ('queued', 'running'):
        result['worker_online'] = runtime.health()['online']
    return result


def submit_job(kind, channel, payload, request_key=None):
    from config import is_cloud_mode
    if is_cloud_mode():
        raise HTTPException(status_code=503, detail='Library workers require local single-library mode; tenant-scoped cloud queues are not configured.')
    try:
        return public_job(runtime.submit(kind, channel, payload, request_key))
    except TaskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get('/jobs/runtime')
def worker_runtime():
    runtime.ensure_worker()
    return dict(runtime.policy(), **runtime.health())


@router.get('/jobs/latest')
def latest_job(channel: str = 'pipeline'):
    runtime.ensure_worker()
    return public_job(runtime.store().latest(channel, summary=True))


@router.get('/jobs')
def list_jobs():
    runtime.ensure_worker()
    return [public_job(job) for job in runtime.store().recent()]


@router.get('/jobs/{job_id}')
def get_job(job_id: str):
    runtime.ensure_worker()
    job = runtime.store().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Task not found')
    return public_job(job)


@router.post('/jobs/{job_id}/resume', status_code=202)
def resume_job(job_id: str):
    try:
        job = runtime.store().resume(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail='Task not found')
    except TaskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    runtime.ensure_worker()
    return public_job(job)


class PipelineRequest(BaseModel):
    use_llm: bool = True


@router.post('/pipeline/run', status_code=202)
def run_pipeline(body: PipelineRequest = PipelineRequest(), idempotency_key: Optional[str] = Header(None)):
    return submit_job('pipeline', 'pipeline', body.dict(), idempotency_key)
