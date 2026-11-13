"""Durable tasks: submitting is fast; polling never opens the library DB."""
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services import task_runtime as runtime
from services.paper_extract_route import (
    JobResumeRequest,
    PaperExtractRouteError,
    PipelineRunRequest,
    confirmation_as_dict,
    require_paper_extract_route,
)
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


def _bind_extract_confirmation(job, confirmation) -> None:
    if not job or job.get("kind") not in {"papers", "pipeline"}:
        return
    payload = dict(job.get("payload") or {})
    incoming = confirmation_as_dict(confirmation)
    if incoming is not None:
        payload["file_search_confirmation"] = incoming
    try:
        require_paper_extract_route(None, payload.get("file_search_confirmation"), stage="submit")
    except PaperExtractRouteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.http_detail()) from exc
    if payload != dict(job.get("payload") or {}):
        runtime.store().update(job["job_id"], payload=payload)
        job["payload"] = payload


@router.post('/jobs/{job_id}/resume', status_code=202)
def resume_job(job_id: str, body: JobResumeRequest = JobResumeRequest()):
    job = runtime.store().get(job_id)
    if job and job["kind"] == "domain":
        raise HTTPException(409, "请在索骥重新生成，以使用当前画布和锁定状态。")
    if job and job["kind"] == "roadmap":
        raise HTTPException(409, "请在识途重新调研，以使用当前路线图和锁定状态。")
    _bind_extract_confirmation(job, body.file_search_confirmation)
    try:
        job = runtime.store().resume(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail='Task not found')
    except TaskConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    runtime.ensure_worker()
    return public_job(job)


@router.post('/pipeline/run', status_code=202)
def run_pipeline(body: PipelineRunRequest = PipelineRunRequest(), idempotency_key: Optional[str] = Header(None)):
    try:
        require_paper_extract_route(None, body.file_search_confirmation, stage="submit")
    except PaperExtractRouteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.http_detail()) from exc
    payload = body.model_dump()
    confirmation = confirmation_as_dict(body.file_search_confirmation)
    if confirmation is not None:
        payload["file_search_confirmation"] = confirmation
    elif "file_search_confirmation" in payload and payload["file_search_confirmation"] is None:
        payload.pop("file_search_confirmation")
    return submit_job('pipeline', 'pipeline', payload, idempotency_key)
