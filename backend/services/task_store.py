"""Short, durable queue transactions, separate from the library's write locks."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class TaskConflict(ValueError):
    pass


class TaskStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, channel TEXT NOT NULL,
                    payload TEXT NOT NULL, fingerprint TEXT NOT NULL, request_key TEXT UNIQUE,
                    status TEXT NOT NULL, progress TEXT NOT NULL DEFAULT '{}',
                    checkpoints TEXT NOT NULL DEFAULT '{}', result TEXT, error TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL, attempt INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_channel ON jobs(channel)
                    WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS channels (
                    channel TEXT PRIMARY KEY, job_id TEXT NOT NULL, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, job_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker (
                    id INTEGER PRIMARY KEY CHECK(id=1), pid INTEGER NOT NULL, heartbeat REAL NOT NULL
                );
            ''')

    @staticmethod
    def decode(row):
        if row is None:
            return None
        job = dict(row)
        job['job_id'] = job.pop('id')
        for key in ('payload', 'progress', 'checkpoints', 'result'):
            job[key] = json.loads(job[key]) if job.get(key) else None
        return job

    def submit(self, kind, channel, payload, request_key=None):
        encoded = json.dumps(payload, sort_keys=True)
        fingerprint = hashlib.sha256((kind + encoded).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if request_key:
                old = db.execute('SELECT j.* FROM jobs j JOIN requests r ON r.job_id=j.id WHERE r.key=?',
                                 (request_key,)).fetchone()
                if old:
                    if old['fingerprint'] != fingerprint:
                        raise TaskConflict('Idempotency key was already used for a different request')
                    return self.decode(old)
            active = db.execute("SELECT * FROM jobs WHERE status IN ('queued','running')").fetchall()
            for row in active:
                if row['channel'] == channel:
                    if row['fingerprint'] == fingerprint:
                        if request_key:
                            db.execute('INSERT INTO requests VALUES (?,?,?)', (request_key, fingerprint, row['id']))
                        return self.decode(row)
                    raise TaskConflict('该模块已有不同任务，请等待完成后再提交。')
                if channel == 'pipeline' or row['channel'] == 'pipeline':
                    raise TaskConflict('已有任务执行中，全流程与单独任务不能同时提交。')
            job_id, now = str(uuid4()), time.time()
            db.execute('''INSERT INTO jobs
                (id,kind,channel,payload,fingerprint,request_key,status,created,updated)
                VALUES (?,?,?,?,?,?,'queued',?,?)''',
                (job_id, kind, channel, encoded, fingerprint, request_key, now, now))
            if request_key:
                db.execute('INSERT INTO requests VALUES (?,?,?)', (request_key, fingerprint, job_id))
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def get(self, job_id):
        with self.connect() as db:
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def latest(self, channel, summary=False):
        columns = 'id,kind,channel,payload,status,progress,error,created,updated,attempt' if summary else '*'
        with self.connect() as db:
            return self.decode(db.execute(f'SELECT {columns} FROM jobs WHERE channel=? ORDER BY created DESC LIMIT 1',
                                          (channel,)).fetchone())

    def recent(self):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute('''SELECT id,kind,channel,status,progress,error,
                created,updated,attempt FROM jobs ORDER BY created DESC LIMIT 20''')]

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
                return None
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running', attempt=attempt+1, updated=? WHERE id=?",
                       (time.time(), row['id']))
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())

    def active(self):
        with self.connect() as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM jobs WHERE status IN ('queued','running')")]

    def update(self, job_id, **changes):
        allowed = {'status', 'progress', 'checkpoints', 'result', 'error'}
        if not changes or not set(changes).issubset(allowed):
            raise ValueError('Invalid job update')
        values = [json.dumps(v, ensure_ascii=False) if k in {'progress', 'checkpoints', 'result'} else v
                  for k, v in changes.items()]
        with self.connect() as db:
            db.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in changes) + ', updated=? WHERE id=?',
                       (*values, time.time(), job_id))

    def publish(self, job_id, channel, state):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO channels VALUES (?,?,?)',
                       (channel, job_id, json.dumps(state, ensure_ascii=False)))

    def snapshot(self, channel):
        with self.connect() as db:
            row = db.execute('''SELECT c.state,j.id,j.status,j.error,j.progress FROM channels c
                JOIN jobs j ON j.id=c.job_id WHERE c.channel=?''', (channel,)).fetchone()
            if not row:
                return None
            state = json.loads(row['state'])
            state.update(job_id=row['id'], job_status=row['status'])
            if row['status'] in ('interrupted', 'failed') and (
                state.get('running') or json.loads(row['progress']).get('channel') == channel
            ):
                state.update(running=False, error=row['error'], batch_error=row['error'], last_error=row['error'])
            return state

    def heartbeat(self, pid):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO worker VALUES (1,?,?)', (pid, time.time()))

    def worker(self):
        with self.connect() as db:
            row = db.execute('SELECT * FROM worker WHERE id=1').fetchone()
            return dict(row) if row else None

    def recover(self):
        # Call ONLY after acquiring the process-lifetime worker lock. A stale
        # heartbeat alone must never cause a second executor to steal live work.
        with self.connect() as db:
            db.execute("""UPDATE jobs SET status='interrupted', error=?, updated=?
                WHERE status='running'""", ('Worker 已退出；已保存检查点，可手动恢复未完成任务。', time.time()))

    def resume(self, job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            if row['status'] not in ('failed', 'interrupted'):
                raise TaskConflict('只有失败或中断的任务可以恢复。')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                raise TaskConflict('请等待当前任务完成后再恢复。')
            db.execute("UPDATE jobs SET status='queued', error=NULL, updated=? WHERE id=?", (time.time(), job_id))
        return self.get(job_id)
