"""작업 큐와 워커 (01-plan 3.5, PR-5·5b).

server/.env의 PostgreSQL 서버에 만든 테스트 전용 DB(conftest의 pg_engine)에서 큐 동작을 확인한다.
우선순위, 동시 꺼내기, 회의별 직렬 실행, 요약 전용 자리, 재시도, heartbeat 회수, 종료 시 반납, 고아 전사 재등록.
전사 핸들러는 STT 프로바이더만 대역으로 바꾸고 meeting_service의 DB 저장은 실제로 쓴다.
"""
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from pydub import AudioSegment
from sqlalchemy import update

from database import Meeting as DBMeeting, ProcessingJob, User as DBUser
from models.meeting import TranscriptSegment
from services import job_service, transcription_service
from services.job_service import RetryableError
from services.meeting_service import create_meeting, load_meeting, set_transcription_status
from stt.base import SttResult
from utils.config import parse_worker_concurrency
from worker import Worker

SEGMENTS = [
    TranscriptSegment(speaker_index=0, text="안녕하세요.", start=0, end=900),
    TranscriptSegment(speaker_index=1, text="네, 시작하죠.", start=1200, end=2400),
]


def add_meetings(db, count):
    with db() as session:
        if session.get(DBUser, "tester@example.com") is None:
            session.add(DBUser(email="tester@example.com", name="tester"))
        ids = [f"m{i}" for i in range(1, count + 1)]
        for meeting_id in ids:
            session.add(DBMeeting(id=meeting_id, user_id="tester@example.com", title=meeting_id, participants=["화자1", "화자2"]))
        session.commit()
    return ids


@pytest.fixture
def meeting_ids(db):
    """FK 때문에 작업보다 먼저 회의가 있어야 한다."""
    return add_meetings(db, 3)


def job_row(job_id):
    job = job_service.get_job(job_id)
    assert job is not None
    return job


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def make_worker(handlers, **kwargs):
    kwargs.setdefault("worker_id", "test-worker")
    return Worker(handlers, poll_interval=0.02, **kwargs)


def start(worker):
    thread = threading.Thread(target=worker.run_forever)
    thread.start()
    return thread


def stop(worker, thread):
    worker.stop()
    thread.join(timeout=10)
    assert not thread.is_alive()


# ==================== 큐 ====================

def test_claim_order_priority_then_fifo(meeting_ids):
    t1 = job_service.enqueue_job("m1", "transcribe")
    t2 = job_service.enqueue_job("m2", "transcribe")
    s1 = job_service.enqueue_job("m3", "summarize")  # 늦게 들어왔지만 요약이 먼저

    assert [job_service.claim_next_job("w").id for _ in range(3)] == [s1, t1, t2]
    assert job_service.claim_next_job("w") is None


def test_claim_marks_job_running(meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe", params={"a": 1})
    assert (job_row(job_id).status, job_row(job_id).attempts) == ("queued", 0)

    job = job_service.claim_next_job("host:1")

    assert (job.id, job.status, job.attempts, job.locked_by) == (job_id, "running", 1, "host:1")
    assert job.started_at is not None and job.heartbeat_at is not None
    assert job.params == {"a": 1}


def test_unknown_kind_is_rejected(meeting_ids):
    with pytest.raises(ValueError, match="Unknown job kind"):
        job_service.enqueue_job("m1", "bogus")


def test_enqueue_reuses_queued_job_of_same_kind(meeting_ids):
    first = job_service.enqueue_job("m1", "transcribe")
    assert job_service.enqueue_job("m1", "transcribe") == first  # 다시 업로드해도 대기 작업은 하나
    assert job_service.enqueue_job("m1", "summarize") != first  # 종류가 다르면 따로

    job_service.claim_next_job("w", ["transcribe"])  # 실행 중이 되면
    assert job_service.enqueue_job("m1", "transcribe") != first  # 새 대기 작업을 만든다(최신 원본을 다시 처리)


def test_retry_with_another_queued_job_of_same_kind(meeting_ids):
    """실행 중 작업이 재시도로 queued가 될 때 같은 종류의 대기 작업이 이미 있어도 오류 없이 둘 다 남는다."""
    running = job_service.enqueue_job("m1", "transcribe")
    job_service.claim_next_job("w")
    queued = job_service.enqueue_job("m1", "transcribe")

    assert job_service.fail_job(running, "w", "ConnectionError", retry=True) == "queued"
    assert (job_row(running).status, job_row(queued).status) == ("queued", "queued")


def test_find_active_job_prefers_running(meeting_ids):
    assert job_service.find_active_job("m1", "summarize") is None
    running = job_service.enqueue_job("m1", "summarize")
    job_service.claim_next_job("w")
    queued = job_service.enqueue_job("m1", "summarize")

    assert queued != running
    assert job_service.find_active_job("m1", "summarize") == running


def test_jobs_of_the_same_meeting_never_run_together(meeting_ids):
    transcribe = job_service.enqueue_job("m1", "transcribe")
    summarize = job_service.enqueue_job("m1", "summarize")
    other = job_service.enqueue_job("m2", "transcribe")

    first = job_service.claim_next_job("w1")
    assert first.id == summarize  # 우선순위가 높은 요약부터
    # m1은 실행 중이라 m1의 전사는 건너뛰고 m2를 꺼낸다
    assert job_service.claim_next_job("w2").id == other
    assert job_service.claim_next_job("w3") is None

    job_service.complete_job(summarize, "w1")
    assert job_service.claim_next_job("w3").id == transcribe


def test_concurrent_claims_never_take_the_same_job(db):
    ids = add_meetings(db, 20)
    for meeting_id in ids:
        job_service.enqueue_job(meeting_id, "transcribe")

    claimed, lock = [], threading.Lock()

    def drain(name):
        while (job := job_service.claim_next_job(name)) is not None:
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=drain, args=(f"w{i}",)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert len(claimed) == 20 and len(set(claimed)) == 20


def test_concurrent_enqueue_creates_one_queued_job(meeting_ids):
    """업로드와 고아 재등록이 동시에 같은 회의 작업을 넣어도 대기 작업은 하나다."""
    ids, lock = [], threading.Lock()

    def enqueue():
        job_id = job_service.enqueue_job("m1", "transcribe")
        with lock:
            ids.append(job_id)

    threads = [threading.Thread(target=enqueue) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert len(set(ids)) == 1


def test_only_owner_can_finish_a_job(meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe")
    job_service.claim_next_job("owner")

    assert job_service.complete_job(job_id, "someone-else") is False
    assert job_service.fail_job(job_id, "someone-else", "x", retry=False) is None
    assert job_row(job_id).status == "running"
    assert job_service.complete_job(job_id, "owner") is True


def test_deleting_meeting_deletes_its_jobs(db, meeting_ids):
    job_service.enqueue_job("m1", "transcribe")
    with db() as session:
        session.query(DBMeeting).filter(DBMeeting.id == "m1").delete()
        session.commit()
        assert session.query(ProcessingJob).count() == 0


# ==================== 워커 한 사이클 ====================

def test_worker_runs_job_and_records_stage(meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe")
    seen = {}

    def handler(job, on_stage):
        on_stage("stt")
        seen["stage"] = job_row(job.id).stage
        seen["status"] = job_row(job.id).status

    assert make_worker({"transcribe": handler}).run_once() is True

    assert seen == {"stage": "stt", "status": "running"}
    job = job_row(job_id)
    assert (job.status, job.stage, job.attempts, job.error) == ("done", None, 1, None)
    assert job.finished_at is not None


def test_worker_returns_false_when_queue_is_empty(meeting_ids):
    assert make_worker({}).run_once() is False


def test_retryable_error_requeues_until_max_attempts(meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe", max_attempts=3)
    set_transcription_status("m1", "processing")
    worker = make_worker({"transcribe": lambda job, on_stage: (_ for _ in ()).throw(RetryableError("연결 실패"))})

    worker.run_once()
    job = job_row(job_id)
    assert (job.status, job.attempts, job.finished_at) == ("queued", 1, None)
    assert "RetryableError: 연결 실패" in job.error
    assert load_meeting("m1").transcription_status == "processing"  # 재시도 중에는 처리 중으로 보인다

    worker.run_once()
    worker.run_once()

    job = job_row(job_id)
    assert (job.status, job.attempts) == ("failed", 3)
    assert load_meeting("m1").transcription_status == "failed"
    assert worker.run_once() is False


def test_retry_then_success(meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe")
    calls = []

    def flaky(job, on_stage):
        calls.append(job.attempts)
        if len(calls) == 1:
            raise ConnectionError("refused")

    worker = make_worker({"transcribe": flaky})
    worker.run_once()
    worker.run_once()

    job = job_row(job_id)
    assert (calls, job.status, job.attempts, job.error) == ([1, 2], "done", 2, None)


def test_non_retryable_error_fails_immediately(meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe")
    set_transcription_status("m1", "processing")

    def handler(job, on_stage):
        on_stage("normalize")
        raise ValueError("디코딩 안 되는 오디오")

    make_worker({"transcribe": handler}).run_once()

    job = job_row(job_id)
    assert (job.status, job.attempts, job.stage) == ("failed", 1, "normalize")  # 실패한 단계가 남는다
    assert job.error == "ValueError: 디코딩 안 되는 오디오"
    assert load_meeting("m1").transcription_status == "failed"


def test_failed_transcription_does_not_override_done(meeting_ids):
    """같은 회의의 다른 작업이 이미 전사를 끝냈으면(done) 뒤늦은 실패가 상태를 덮지 않는다."""
    set_transcription_status("m1", "done")
    job_service.enqueue_job("m1", "transcribe")

    make_worker({"transcribe": lambda job, on_stage: (_ for _ in ()).throw(ValueError("no source"))}).run_once()

    assert load_meeting("m1").transcription_status == "done"


def test_missing_handler_fails_job(meeting_ids):
    job_id = job_service.enqueue_job("m1", "summarize")

    make_worker({}).run_once()

    job = job_row(job_id)
    assert job.status == "failed" and "no handler" in job.error


def test_failed_summary_does_not_touch_transcription_status(meeting_ids):
    set_transcription_status("m1", "processing")
    job_service.enqueue_job("m1", "summarize")

    make_worker({}).run_once()

    assert load_meeting("m1").transcription_status == "processing"


@pytest.mark.parametrize("exc, expected", [
    (RetryableError("x"), True),
    (ConnectionError("x"), True),
    (TimeoutError("x"), True),
    (type("OutOfMemoryError", (RuntimeError,), {})("CUDA out of memory"), True),
    (type("APIConnectionError", (Exception,), {})("x"), True),
    (ValueError("x"), False),
    (RuntimeError("x"), False),
    (FileNotFoundError("x"), False),
])
def test_is_retryable(exc, expected):
    assert job_service.is_retryable(exc) is expected


# ==================== heartbeat ====================

def test_heartbeat_is_refreshed_while_handler_runs(meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe")
    beats = []

    def slow_handler(job, on_stage):
        beats.append(job_row(job.id).heartbeat_at)
        time.sleep(0.4)  # 단계 기록 없이 오래 걸리는 STT를 흉내 낸다
        beats.append(job_row(job.id).heartbeat_at)

    make_worker({"transcribe": slow_handler}, heartbeat_interval=0.05).run_once()

    assert beats[1] - beats[0] > timedelta(seconds=0.2)
    assert job_row(job_id).status == "done"


def age_heartbeat(db, job_id, seconds):
    with db() as session:
        session.execute(
            update(ProcessingJob).where(ProcessingJob.id == job_id)
            .values(heartbeat_at=datetime.utcnow() - timedelta(seconds=seconds))
        )
        session.commit()


def test_stale_running_job_is_requeued(db, meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe", max_attempts=3)
    job_service.claim_next_job("dead-worker")  # 워커가 죽었다고 보고 heartbeat만 오래된 상태로 둔다
    age_heartbeat(db, job_id, 600)

    handled = []
    worker = make_worker({"transcribe": lambda job, on_stage: handled.append((job.id, job.attempts))}, stale_after=180)
    worker.run_once()

    assert handled == [(job_id, 2)]  # 회수 후 같은 사이클에 이어서 처리
    assert job_row(job_id).status == "done"
    assert job_service.complete_job(job_id, "dead-worker") is False  # 죽은 줄 알았던 워커가 늦게 끝나도 덮지 않는다


def test_fresh_running_job_is_left_alone(db, meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe")
    job_service.claim_next_job("other-worker")
    age_heartbeat(db, job_id, 10)

    assert make_worker({}, stale_after=180).run_once() is False

    job = job_row(job_id)
    assert (job.status, job.locked_by) == ("running", "other-worker")


def test_stale_job_out_of_attempts_fails_and_marks_meeting(db, meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe", max_attempts=1)
    set_transcription_status("m1", "processing")
    job_service.claim_next_job("dead-worker")
    age_heartbeat(db, job_id, 600)

    assert make_worker({}, stale_after=180).run_once() is False

    job = job_row(job_id)
    assert job.status == "failed" and "heartbeat lost" in job.error
    assert load_meeting("m1").transcription_status == "failed"


# ==================== 동시 처리 (run_forever) ====================

@pytest.mark.parametrize("profile, expected", [("public", 4), ("airgap", 1)])
def test_worker_concurrency_default(profile, expected):
    assert parse_worker_concurrency(None, profile) == expected


@pytest.mark.parametrize("profile, value, expected", [("public", "1", 1), ("public", "8", 8), ("airgap", "1", 1)])
def test_valid_worker_concurrency(profile, value, expected):
    assert parse_worker_concurrency(value, profile) == expected


@pytest.mark.parametrize("value", ["0", "-1", "", "four", "2.5"])
def test_invalid_worker_concurrency_is_rejected(value):
    with pytest.raises(ValueError, match="Invalid WORKER_CONCURRENCY"):
        parse_worker_concurrency(value, "public")


def test_airgap_concurrency_above_one_stops_import(reload_config):
    with pytest.raises(ValueError, match="single GPU"):
        reload_config(APP_PROFILE="airgap", WORKER_CONCURRENCY="2")


def test_slot_zero_is_summary_only_when_concurrent():
    assert make_worker({}, concurrency=1).slot_kinds(0) is None
    worker = make_worker({}, concurrency=4)
    assert worker.slot_kinds(0) == job_service.SUMMARY_KINDS
    assert [worker.slot_kinds(i) for i in (1, 2, 3)] == [None, None, None]


def blocking_handler(events, release):
    """작업마다 시작을 기록하고 release가 set될 때까지 붙잡는 핸들러."""
    def handler(job, on_stage):
        events.append((job.kind, job.meeting_id))
        release.wait(5)
    return handler


def record_handler(events):
    return lambda job, on_stage: events.append((job.kind, job.meeting_id))


def test_summary_runs_while_transcriptions_fill_other_slots(meeting_ids):
    """자리 2개: 1번이 긴 전사를 잡고 있어도 0번(요약 전용)이 다른 회의의 요약을 바로 처리한다."""
    events, release = [], threading.Event()
    worker = make_worker({"transcribe": blocking_handler(events, release), "summarize": record_handler(events)}, concurrency=2)
    job_service.enqueue_job("m1", "transcribe")
    job_service.enqueue_job("m2", "transcribe")
    thread = start(worker)
    try:
        assert wait_until(lambda: ("transcribe", "m1") in events)
        time.sleep(0.2)
        assert ("transcribe", "m2") not in events  # 0번 자리는 전사를 꺼내지 않는다

        summary = job_service.enqueue_job("m3", "summarize")
        assert wait_until(lambda: job_row(summary).status == "done")  # 전사가 끝나기 전에 요약이 끝난다
        assert job_row(job_service.find_active_job("m1", "transcribe")).status == "running"
    finally:
        release.set()
        stop(worker, thread)


def test_transcriptions_run_in_parallel_up_to_general_slots(meeting_ids):
    """자리 3개면 전사는 최대 2건이 동시에 돈다."""
    events, release = [], threading.Event()
    worker = make_worker({"transcribe": blocking_handler(events, release)}, concurrency=3)
    for meeting_id in meeting_ids:
        job_service.enqueue_job(meeting_id, "transcribe")
    thread = start(worker)
    try:
        assert wait_until(lambda: len(events) == 2)
        time.sleep(0.2)
        assert len(events) == 2
    finally:
        release.set()
        assert wait_until(lambda: len(events) == 3)
        stop(worker, thread)


def test_summary_of_meeting_waits_for_its_transcription(meeting_ids):
    """요약 전용 자리가 비어 있어도 같은 회의의 전사가 끝나기 전에는 그 회의의 요약을 꺼내지 않는다."""
    events, release = [], threading.Event()
    worker = make_worker({"transcribe": blocking_handler(events, release), "summarize": record_handler(events)}, concurrency=2)
    job_service.enqueue_job("m1", "transcribe")
    thread = start(worker)
    try:
        assert wait_until(lambda: events == [("transcribe", "m1")])
        summary = job_service.enqueue_job("m1", "summarize")
        time.sleep(0.3)
        assert job_row(summary).status == "queued"

        release.set()
        assert wait_until(lambda: job_row(summary).status == "done")
        assert events == [("transcribe", "m1"), ("summarize", "m1")]
    finally:
        release.set()
        stop(worker, thread)


# ==================== 종료 ====================

def test_stop_lets_short_job_finish(meeting_ids):
    job_id = job_service.enqueue_job("m1", "transcribe")
    started = threading.Event()

    def handler(job, on_stage):
        started.set()
        time.sleep(0.2)

    worker = make_worker({"transcribe": handler}, shutdown_grace=3)
    thread = start(worker)
    assert started.wait(5)
    stop(worker, thread)

    assert job_row(job_id).status == "done"


def test_stop_releases_long_job_back_to_queue(meeting_ids):
    """유예 시간 안에 끝나지 않은 작업은 시도 횟수를 세지 않고 queued로 돌려주고, 새 워커가 이어서 처리한다."""
    job_id = job_service.enqueue_job("m1", "transcribe")
    started, release = threading.Event(), threading.Event()

    def long_handler(job, on_stage):
        started.set()
        release.wait(5)

    worker = make_worker({"transcribe": long_handler}, worker_id="old", shutdown_grace=0.2)
    thread = start(worker)
    assert started.wait(5)
    stop(worker, thread)

    job = job_row(job_id)
    assert (job.status, job.attempts, job.locked_by) == ("queued", 0, None)
    assert job.error == "released on worker shutdown"

    release.set()  # 옛 워커의 실행이 늦게 끝나도 반납된 작업을 done으로 바꾸지 않는다
    time.sleep(0.2)
    assert job_row(job_id).status == "queued"

    handled = []
    make_worker({"transcribe": lambda job, on_stage: handled.append(job.attempts)}, worker_id="new").run_once()
    assert (handled, job_row(job_id).status) == ([1], "done")


# ==================== 고아 전사 재등록 ====================

def test_worker_start_enqueues_orphaned_transcriptions(meeting_ids):
    """processing인데 작업이 없는 회의(배포 때 끊긴 BackgroundTasks 등)는 워커가 기동할 때 전사 작업을 넣는다."""
    set_transcription_status("m1", "processing", source_audio_path="/tmp/x.wav", set_source=True)
    set_transcription_status("m2", "processing", source_audio_path="/tmp/y.wav", set_source=True)
    existing = job_service.enqueue_job("m2", "transcribe")  # m2는 이미 대기 작업이 있다
    set_transcription_status("m3", "done")

    assert transcription_service.enqueue_orphaned_transcriptions() == ["m1"]
    assert job_service.find_active_job("m1", "transcribe") is not None
    assert job_service.find_active_job("m2", "transcribe") == existing
    assert job_service.find_active_job("m3", "transcribe") is None


def test_run_forever_processes_orphans(meeting_ids):
    set_transcription_status("m1", "processing", source_audio_path="/tmp/x.wav", set_source=True)
    events = []
    worker = make_worker({"transcribe": record_handler(events)})
    thread = start(worker)
    try:
        assert wait_until(lambda: events == [("transcribe", "m1")])
    finally:
        stop(worker, thread)


# ==================== 전사 핸들러 (실제 DB 저장) ====================

class SegmentsProvider:
    """화자까지 주는 엔진(Clova 같은) 대역."""
    name = "fake-segments"

    def transcribe(self, wav_path, *, language, hints):
        return SttResult(engine=self.name, segments=SEGMENTS, speaker_names=["화자A", "화자B"])

    def unload(self):
        pass


@pytest.fixture
def uploaded_meeting(db, tmp_path, monkeypatch):
    """업로드가 끝난 상태: 회의, 원본 파일, processing 상태."""
    with db() as session:
        session.add(DBUser(email="tester@example.com", name="tester"))
        session.commit()
    meeting = create_meeting("업로드 회의", None, "tester@example.com")
    source = tmp_path / f"source_{meeting.id}_1.wav"
    AudioSegment.silent(duration=1500, frame_rate=16000).export(str(source), format="wav")
    set_transcription_status(meeting.id, "processing", source_audio_path=str(source), set_source=True)
    monkeypatch.setattr(transcription_service, "RECORDS_DIR", tmp_path / "records")
    return SimpleNamespace(id=meeting.id, source=source)


def test_worker_transcribes_and_saves(uploaded_meeting, monkeypatch):
    monkeypatch.setattr(transcription_service, "get_stt_provider", lambda: SegmentsProvider())
    job_id = job_service.enqueue_job(uploaded_meeting.id, "transcribe")
    stages = []
    original = job_service.set_stage
    monkeypatch.setattr(
        job_service, "set_stage", lambda job_id, worker_id, stage: (stages.append(stage), original(job_id, worker_id, stage))
    )

    assert Worker(worker_id="w", poll_interval=0.01).run_once() is True

    assert stages == ["normalize", "stt", "save"]
    meeting = load_meeting(uploaded_meeting.id)
    assert meeting.transcription_status == "done"
    assert meeting.participants == ["화자A", "화자B"]
    assert [(s.speaker_index, s.text, s.start, s.end) for s in meeting.transcript] == [
        (0, "안녕하세요.", 0, 900), (1, "네, 시작하죠.", 1200, 2400),
    ]
    assert meeting.duration_ms == 1500 and len(meeting.audio_files) == 1
    assert not uploaded_meeting.source.exists()
    assert job_row(job_id).status == "done"


def test_public_default_provider_without_clova_credentials_fails_job(uploaded_meeting):
    """public 기본(clova)인데 접속 정보가 없으면(테스트 env) 재시도 없이 실패하고, 회의는 failed, 원본은 남는다."""
    job_id = job_service.enqueue_job(uploaded_meeting.id, "transcribe")

    Worker(worker_id="w", poll_interval=0.01).run_once()

    job = job_row(job_id)
    assert (job.status, job.attempts, job.stage) == ("failed", 1, "stt")
    assert "CLOVA" in job.error
    assert load_meeting(uploaded_meeting.id).transcription_status == "failed"
    assert uploaded_meeting.source.exists()


def test_words_only_engine_fails_until_diarization_exists(uploaded_meeting, monkeypatch):
    """mock처럼 단어만 주는 엔진은 화자분리·병합(PR-6)이 붙기 전까지 완료할 수 없다. 재시도하지 않고 실패로 둔다."""
    from utils import config
    monkeypatch.setattr(config, "STT_PROVIDER", "mock")
    job_id = job_service.enqueue_job(uploaded_meeting.id, "transcribe")

    Worker(worker_id="w", poll_interval=0.01).run_once()

    job = job_row(job_id)
    assert (job.status, job.attempts, job.stage) == ("failed", 1, "stt")
    assert "returned no speaker segments" in job.error
    assert load_meeting(uploaded_meeting.id).transcription_status == "failed"
    assert uploaded_meeting.source.exists()


def test_missing_source_fails_job_without_retry(uploaded_meeting):
    uploaded_meeting.source.unlink()
    job_id = job_service.enqueue_job(uploaded_meeting.id, "transcribe")

    Worker(worker_id="w", poll_interval=0.01).run_once()

    job = job_row(job_id)
    assert (job.status, job.attempts) == ("failed", 1)
    assert "SourceAudioMissing" in job.error
    assert load_meeting(uploaded_meeting.id).transcription_status == "failed"
