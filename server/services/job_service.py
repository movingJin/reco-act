"""처리 작업 큐 (01-plan 3.5).

processing_jobs 테이블을 PostgreSQL 큐로 쓴다. 새 인프라나 의존성이 없고 상태·재시도·실패 사유가 DB에 남는다.
이 모듈은 큐 연산만 한다. 작업을 실행하는 쪽은 worker.py다.

- 같은 회의·종류의 대기(queued) 작업이 있으면 새로 넣지 않고 그 id를 돌려준다. 재시도·반납으로 다시 queued가 되는 작업과
  부딪치지 않도록 DB 제약이 아니라 넣을 때만 확인한다(꺼내기와 같은 advisory lock 아래에서).
- 같은 회의의 작업은 한 번에 하나만 실행한다. 전사가 끝나기 전에 그 회의의 요약이 돌지 않게 하려는 것이다.
- 작업 상태를 바꾸는 연산은 그 작업을 잡은 워커(locked_by)만 할 수 있다. 회수·반납된 뒤 늦게 끝난 실행이 결과를 덮어쓰지 않게 한다.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import select, text, update

from database import Meeting as DBMeeting, ProcessingJob, SessionLocal

# 큐 상태
QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
ACTIVE_STATUSES = (QUEUED, RUNNING)

SUMMARY_KINDS = ("summarize", "regenerate_notes")
# 요약 작업(자동 요약, 사용자 요청)을 새 전사 작업보다 먼저 꺼낸다. 이미 실행 중인 작업은 끊지 않는다(01-plan 3.5).
JOB_PRIORITY = {"transcribe": 0, "summarize": 10, "regenerate_notes": 10}
DEFAULT_MAX_ATTEMPTS = 3

# 작업 넣기·꺼내기를 DB 전체에서 한 번에 하나씩 하도록 거는 advisory lock 키. 둘 다 짧아서 직렬화해도 부담이 없고,
# 그래야 "대기 작업은 하나만", "같은 회의에 실행 중인 작업이 없을 때만 꺼낸다"는 조건이 워커·API 여럿 사이에서도 지켜진다.
_CLAIM_LOCK_KEY = 0x7265636F  # "reco"


class RetryableError(Exception):
    """일시 오류(LLM 연결 실패, CUDA OOM 등). 작업을 다시 시도한다."""


# 라이브러리를 import하지 않고 이름으로 판별한다(torch 등은 airgap 워커에만 있다).
_RETRYABLE_TYPE_NAMES = {"OutOfMemoryError", "APIConnectionError", "APITimeoutError"}


def is_retryable(exc: BaseException) -> bool:
    """다시 시도할 만한 오류인지. 입력 오류(디코딩 안 되는 오디오 등)와 알 수 없는 오류는 바로 실패로 둔다(01-plan 3.5)."""
    if isinstance(exc, (RetryableError, ConnectionError, TimeoutError)):
        return True
    return any(cls.__name__ in _RETRYABLE_TYPE_NAMES for cls in type(exc).__mro__)


def enqueue_job(
    meeting_id: str,
    kind: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    """작업을 큐에 넣고 id를 돌려준다. 같은 회의·종류의 대기 작업이 이미 있으면 그 id를 돌려준다."""
    if kind not in JOB_PRIORITY:
        raise ValueError(f"Unknown job kind: {kind!r}")
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CLAIM_LOCK_KEY})
        existing = db.execute(
            select(ProcessingJob.id)
            .where(ProcessingJob.meeting_id == meeting_id, ProcessingJob.kind == kind, ProcessingJob.status == QUEUED)
            .order_by(ProcessingJob.id)
            .limit(1)
        ).scalar()
        if existing is not None:
            db.commit()  # advisory lock 해제
            return existing
        job = ProcessingJob(
            meeting_id=meeting_id,
            kind=kind,
            status=QUEUED,
            priority=JOB_PRIORITY[kind],
            attempts=0,
            max_attempts=max_attempts,
            params=params,
            created_at=datetime.utcnow(),
        )
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def find_active_job(meeting_id: str, kind: str) -> Optional[int]:
    """같은 회의·종류의 queued 또는 running 작업 id. 실행 중인 것을 먼저 고른다. 없으면 None."""
    db = SessionLocal()
    try:
        return db.execute(
            select(ProcessingJob.id)
            .where(
                ProcessingJob.meeting_id == meeting_id,
                ProcessingJob.kind == kind,
                ProcessingJob.status.in_(ACTIVE_STATUSES),
            )
            .order_by((ProcessingJob.status == RUNNING).desc(), ProcessingJob.id)
            .limit(1)
        ).scalar()
    finally:
        db.close()


def claim_next_job(worker_id: str, kinds: Optional[Iterable[str]] = None) -> Optional[ProcessingJob]:
    """queued 작업 하나를 잡아 running으로 바꾼다. 없으면 None.

    우선순위가 높은 순, 같으면 등록순이다. kinds를 주면 그 종류만 꺼낸다(요약 전용 자리).
    같은 회의에 이미 실행 중인 작업이 있으면 그 회의의 작업은 건너뛴다.
    돌려주는 객체는 세션에서 분리돼 있어 속성만 읽을 수 있다.
    """
    db = SessionLocal()
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CLAIM_LOCK_KEY})
        busy_meetings = select(ProcessingJob.meeting_id).where(ProcessingJob.status == RUNNING)
        query = db.query(ProcessingJob).filter(
            ProcessingJob.status == QUEUED, ProcessingJob.meeting_id.not_in(busy_meetings)
        )
        if kinds is not None:
            query = query.filter(ProcessingJob.kind.in_(list(kinds)))
        job = (
            query.order_by(ProcessingJob.priority.desc(), ProcessingJob.id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if job is None:
            db.commit()  # advisory lock 해제
            return None
        now = datetime.utcnow()
        job.status = RUNNING
        job.attempts += 1
        job.stage = None
        job.error = None
        job.started_at = now
        job.heartbeat_at = now
        job.finished_at = None
        job.locked_by = worker_id
        db.commit()
        db.refresh(job)
        db.expunge(job)
        return job
    finally:
        db.close()


def _owned(job_id: int, worker_id: str):
    return (ProcessingJob.id == job_id, ProcessingJob.status == RUNNING, ProcessingJob.locked_by == worker_id)


def _update_owned(job_id: int, worker_id: str, **values) -> bool:
    """이 워커가 잡고 있는 running 작업만 바꾼다. 바뀌었으면 True."""
    db = SessionLocal()
    try:
        result = db.execute(update(ProcessingJob).where(*_owned(job_id, worker_id)).values(**values))
        db.commit()
        return result.rowcount == 1
    finally:
        db.close()


def set_stage(job_id: int, worker_id: str, stage: str) -> None:
    """실행 중인 단계를 기록한다. heartbeat도 함께 갱신한다."""
    _update_owned(job_id, worker_id, stage=stage, heartbeat_at=datetime.utcnow())


def heartbeat(job_id: int, worker_id: str) -> None:
    """실행 중인 작업이 살아 있음을 기록한다."""
    _update_owned(job_id, worker_id, heartbeat_at=datetime.utcnow())


def complete_job(job_id: int, worker_id: str) -> bool:
    return _update_owned(job_id, worker_id, status=DONE, stage=None, finished_at=datetime.utcnow())


def fail_job(job_id: int, worker_id: str, error: str, *, retry: bool) -> Optional[str]:
    """작업 실패를 기록하고 결과 상태(queued 또는 failed)를 돌려준다. 이 워커의 작업이 아니면(회수·반납됨) None.

    retry=True이고 시도 횟수가 max_attempts 미만이면 다시 queued로 돌린다. 사유는 어느 쪽이든 error에 남긴다.
    """
    db = SessionLocal()
    try:
        job = db.query(ProcessingJob).filter(*_owned(job_id, worker_id)).with_for_update().first()
        if job is None:
            return None
        requeue = retry and job.attempts < job.max_attempts
        job.status = QUEUED if requeue else FAILED
        job.error = error
        if requeue:
            job.stage = None
            job.locked_by = None
        else:
            job.finished_at = datetime.utcnow()  # 실패한 단계(stage)는 남겨 둔다
        db.commit()
        return job.status
    finally:
        db.close()


def release_job(job_id: int, worker_id: str) -> bool:
    """워커가 종료하면서 끝내지 못한 작업을 queued로 돌려준다. 시도 횟수는 세지 않는다(배포·재시작 때문에 실패하지 않게)."""
    return _update_owned(
        job_id,
        worker_id,
        status=QUEUED,
        attempts=ProcessingJob.attempts - 1,
        stage=None,
        locked_by=None,
        error="released on worker shutdown",
    )


def recover_stale_jobs(stale_after_seconds: float) -> list[ProcessingJob]:
    """heartbeat가 끊긴 running 작업을 회수한다(워커가 죽은 경우).

    시도 횟수가 남았으면 queued로 돌려 다시 처리하고, 다 썼으면 failed로 둔다. 회수한 작업을 돌려준다.
    """
    cutoff = datetime.utcnow() - timedelta(seconds=stale_after_seconds)
    db = SessionLocal()
    try:
        stale = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.status == RUNNING, ProcessingJob.heartbeat_at < cutoff)
            .with_for_update(skip_locked=True)
            .all()
        )
        for job in stale:
            job.error = f"worker heartbeat lost (stage={job.stage}, locked_by={job.locked_by})"
            if job.attempts < job.max_attempts:
                job.status = QUEUED
                job.stage = None
                job.locked_by = None
            else:
                job.status = FAILED
                job.finished_at = datetime.utcnow()
        db.commit()
        for job in stale:
            db.refresh(job)
            db.expunge(job)
        return stale
    finally:
        db.close()


def mark_transcription_failed(meeting_id: str) -> None:
    """전사 작업이 최종 실패하면 회의를 failed로 둔다. 아직 processing일 때만 바꾼다(이미 done인 전사를 덮지 않게)."""
    db = SessionLocal()
    try:
        db.execute(
            update(DBMeeting)
            .where(DBMeeting.id == meeting_id, DBMeeting.transcription_status == "processing")
            .values(transcription_status="failed")
        )
        db.commit()
    finally:
        db.close()


def get_job(job_id: int) -> Optional[ProcessingJob]:
    db = SessionLocal()
    try:
        job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
        if job is not None:
            db.expunge(job)
        return job
    finally:
        db.close()
