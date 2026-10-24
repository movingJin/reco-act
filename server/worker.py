"""작업 워커 (01-plan 3.5). `python -m worker`로 실행한다. 두 프로파일이 같이 쓴다.

processing_jobs에서 작업을 꺼내 kind별 핸들러를 실행한다. 한 프로세스 안에서 WORKER_CONCURRENCY개의 자리(스레드)가 돈다.
- public(기본 4): 외부 API(Clova, Azure) 응답을 기다리는 시간이 대부분이라 여러 건을 동시에 처리한다.
  자리가 2개 이상이면 0번 자리는 요약 작업만 꺼낸다. 긴 전사가 모든 자리를 차지해도 요약 요청이 기다리지 않게 하려는 것이다.
- airgap(1): GPU 1장이라 한 번에 하나씩 처리한다.
- 실행 중에는 별도 스레드가 heartbeat를 갱신한다. 워커가 죽으면 heartbeat가 끊긴 작업을 회수해 이어서 처리한다.
- 일시 오류는 max_attempts까지 다시 시도하고, 입력 오류 등은 바로 failed로 둔다. 사유는 작업의 error에 남는다.
- 전사 작업이 최종 실패하면 meetings.transcription_status='failed'로 둔다(기존 클라이언트 계약 유지).
- 전사가 끝나면 SUMMARY_AUTO일 때 같은 회의의 요약 작업을 넣는다(자동 요약).
- SIGTERM을 받으면 새 작업을 꺼내지 않고, 실행 중인 작업을 shutdown_grace초 기다린 뒤 끝나지 않은 것은 큐에 돌려주고 끝난다.
  compose down은 SIGTERM 후 10초 뒤 강제 종료하므로, 돌려준 작업은 새 워커가 바로 이어서 처리한다.
"""
import os
import signal
import socket
import threading
import time
from typing import Callable, Dict, Iterable, Optional

from database import ProcessingJob, init_db
from services import job_service
from services.summary_service import create_summary, regenerate_meeting_notes
from services.transcription_service import enqueue_orphaned_transcriptions, transcribe_meeting
from utils import config
from utils.airgap_check import enforce_airgap_runtime

# 핸들러는 (job, 단계 기록 함수)를 받고, 실패하면 예외를 올린다.
JobHandler = Callable[[ProcessingJob, Callable[[str], None]], None]


def _handle_transcribe(job: ProcessingJob, on_stage: Callable[[str], None]) -> None:
    saved = transcribe_meeting(job.meeting_id, on_stage, job_id=job.id)
    # 전사가 끝나면 요약을 이어서 실행한다(00-decisions 2.1). 같은 회의의 작업은 차례로 돌므로 이 전사 작업이 끝난 뒤에 꺼내진다.
    # 인식된 말이 없으면(무음 등) 요약할 내용이 없어 넣지 않는다.
    if config.SUMMARY_AUTO and saved:
        job_service.enqueue_job(job.meeting_id, "summarize")


def _handle_summarize(job: ProcessingJob, on_stage: Callable[[str], None]) -> None:
    create_summary(job.meeting_id)  # 실패하면 예외를 올린다


def _handle_regenerate_notes(job: ProcessingJob, on_stage: Callable[[str], None]) -> None:
    if regenerate_meeting_notes(job.meeting_id) is None:  # 실패를 None으로 알린다(요약 없음 포함)
        raise RuntimeError("meeting notes regeneration failed")


DEFAULT_HANDLERS: Dict[str, JobHandler] = {
    "transcribe": _handle_transcribe,
    "summarize": _handle_summarize,
    "regenerate_notes": _handle_regenerate_notes,
}


class Worker:
    def __init__(
        self,
        handlers: Optional[Dict[str, JobHandler]] = None,
        *,
        concurrency: int = 1,
        worker_id: Optional[str] = None,
        poll_interval: float = 2.0,
        heartbeat_interval: float = 30.0,
        stale_after: float = 180.0,
        shutdown_grace: float = 5.0,
    ) -> None:
        self.handlers = DEFAULT_HANDLERS if handlers is None else handlers
        self.concurrency = concurrency
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        # heartbeat 간격보다 충분히 길어야 한다. 짧으면 살아 있는 작업을 회수한다.
        self.stale_after = stale_after
        self.shutdown_grace = shutdown_grace
        self._stop = threading.Event()
        self._running: Dict[int, ProcessingJob] = {}
        self._running_lock = threading.Lock()

    def slot_kinds(self, slot: int) -> Optional[tuple]:
        """자리별로 꺼낼 작업 종류. 자리가 2개 이상이면 0번은 요약 전용이고, 나머지는 전부(요약 우선)."""
        return job_service.SUMMARY_KINDS if self.concurrency > 1 and slot == 0 else None

    def stop(self) -> None:
        """새 작업을 꺼내지 않게 한다. 실행 중인 작업 처리는 run_forever의 종료 절차가 맡는다."""
        self._stop.set()

    def run_once(self, kinds: Optional[Iterable[str]] = None) -> bool:
        """회수할 작업을 회수하고, 작업 하나를 꺼내 처리한다. 처리한 작업이 있으면 True."""
        for job in job_service.recover_stale_jobs(self.stale_after):
            print(f"[worker] recovered job {job.id} ({job.kind}, meeting {job.meeting_id}) -> {job.status}")
            if job.status == job_service.FAILED:
                self._on_final_failure(job)
        job = job_service.claim_next_job(self.worker_id, kinds)
        if job is None:
            return False
        self._run(job)
        return True

    def run_forever(self) -> None:
        orphans = enqueue_orphaned_transcriptions()
        if orphans:
            print(f"[worker] enqueued {len(orphans)} orphaned transcription(s): {orphans}")
        reserved = " (slot 0 summary-only)" if self.concurrency > 1 else ""
        print(f"[worker] {self.worker_id} started with {self.concurrency} slot(s){reserved}")

        slots = [threading.Thread(target=self._slot_loop, args=(i,), daemon=True) for i in range(self.concurrency)]
        for slot in slots:
            slot.start()
        while not self._stop.wait(1.0):  # 신호 처리기가 돌 수 있게 짧게 나눠 기다린다
            pass

        deadline = time.monotonic() + self.shutdown_grace
        for slot in slots:
            slot.join(max(0.0, deadline - time.monotonic()))
        with self._running_lock:
            unfinished = list(self._running.values())
        for job in unfinished:
            if job_service.release_job(job.id, self.worker_id):
                print(f"[worker] released job {job.id} ({job.kind}, meeting {job.meeting_id}) back to the queue")
        print(f"[worker] {self.worker_id} stopped")

    def _slot_loop(self, slot: int) -> None:
        kinds = self.slot_kinds(slot)
        while not self._stop.is_set():
            try:
                if not self.run_once(kinds):
                    self._stop.wait(self.poll_interval)
            except Exception as e:  # DB 연결 끊김 등. 자리가 죽지 않고 다시 시도한다.
                print(f"[worker] slot {slot} loop error: {e}")
                self._stop.wait(self.poll_interval)

    def _run(self, job: ProcessingJob) -> None:
        print(f"[worker] job {job.id} {job.kind} meeting={job.meeting_id} attempt {job.attempts}/{job.max_attempts}")
        handler = self.handlers.get(job.kind)
        with self._running_lock:
            self._running[job.id] = job
        stop_heartbeat = threading.Event()
        beat = threading.Thread(target=self._beat, args=(job.id, stop_heartbeat), daemon=True)
        beat.start()
        try:
            if handler is None:
                raise LookupError(f"no handler for job kind {job.kind!r}")
            handler(job, lambda stage: job_service.set_stage(job.id, self.worker_id, stage))
        except Exception as e:
            self._on_error(job, e)
        else:
            if job_service.complete_job(job.id, self.worker_id):
                print(f"[worker] job {job.id} done")
            else:
                print(f"[worker] job {job.id} finished but was no longer owned (recovered or released)")
        finally:
            stop_heartbeat.set()
            beat.join()
            with self._running_lock:
                self._running.pop(job.id, None)

    def _beat(self, job_id: int, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_interval):
            try:
                job_service.heartbeat(job_id, self.worker_id)
            except Exception as e:
                print(f"[worker] heartbeat failed for job {job_id}: {e}")

    def _on_error(self, job: ProcessingJob, exc: Exception) -> None:
        error = f"{type(exc).__name__}: {exc}"
        status = job_service.fail_job(job.id, self.worker_id, error, retry=job_service.is_retryable(exc))
        print(f"[worker] job {job.id} error -> {status or 'not owned'}: {error}")
        if status == job_service.FAILED:
            self._on_final_failure(job)

    @staticmethod
    def _on_final_failure(job: ProcessingJob) -> None:
        if job.kind == "transcribe":
            # source_audio_path는 남겨 둔다. 사용자가 다시 업로드하면 덮어쓴다.
            job_service.mark_transcription_failed(job.meeting_id)


def main() -> None:
    enforce_airgap_runtime("worker")  # airgap 외부 호출 0 조건과 모델 경로. 어기면 기동을 멈춘다(public은 하지 않음)
    init_db()
    worker = Worker(concurrency=config.WORKER_CONCURRENCY)
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())
    signal.signal(signal.SIGINT, lambda *_: worker.stop())
    worker.run_forever()


if __name__ == "__main__":
    main()
