"""자동 요약과 요약 상태 API (00-decisions 2.1, 01-plan 3.7, PR-10).

전사 작업이 끝나면 워커가 같은 회의의 요약 작업을 넣고, 화면은 GET /api/summary/{id}/status로 진행을 본다.
STT 프로바이더와 LLM(SummaryNode)만 대역으로 바꾸고, DB·워커·HTTP 경로는 실제로 쓴다.
"""
import asyncio
import io
import threading
from types import SimpleNamespace

import httpx
import pytest
from pydub import AudioSegment

import main
from api.v1 import meeting_endpoints, summary_endpoints
from database import Meeting as DBMeeting, ProcessingJob, User as DBUser
from models.meeting import TranscriptSegment
from services import job_service, summary_service, transcription_service
from services.meeting_service import load_meeting, set_transcription_status
from services.summary_service import load_summary
from stt.base import SttResult
from utils import config
from utils.auth import get_current_user
from worker import Worker

USER = "tester@example.com"
MEETING_ID = "m1"


class FakeSummaryNode:
    def run(self, meeting_id):
        return SimpleNamespace(
            paragraphs=[{"subject": "검색 개편", "start": 0, "end": 2400, "summary": "일정을 정함."}],
            next_steps=["1. 테스트 문서 준비."],
            subject="문서 검색 개편",
            meeting_notes="1. 회의 개요",
        )


class SegmentsProvider:
    """화자까지 주는 엔진(Clova 같은) 대역. segments를 바꿔 무음 결과도 흉내 낸다."""
    name = "fake-segments"
    segments = [
        TranscriptSegment(speaker_index=0, text="오늘은 검색 개편 일정을 정하겠습니다.", start=0, end=1200),
        TranscriptSegment(speaker_index=1, text="좋습니다.", start=1300, end=2400),
    ]

    def transcribe(self, wav_path, *, language, hints):
        return SttResult(engine=self.name, segments=list(self.segments), speaker_names=["화자A", "화자B"])

    def unload(self):
        pass


def wav_bytes(duration_ms=1500):
    buffer = io.BytesIO()
    AudioSegment.silent(duration=duration_ms, frame_rate=16000).export(buffer, format="wav")
    return buffer.getvalue()


@pytest.fixture
def env(db, tmp_path, monkeypatch):
    with db() as session:
        session.add(DBUser(email=USER, name="tester"))
        session.add(DBUser(email="other@example.com", name="other"))
        session.add(DBMeeting(id=MEETING_ID, user_id=USER, title="회의", participants=["화자1", "화자2"]))
        session.add(DBMeeting(id="other-meeting", user_id="other@example.com", title="남의 회의", participants=[]))
        session.commit()
    monkeypatch.setattr(transcription_service, "get_stt_provider", lambda: SegmentsProvider())
    monkeypatch.setattr(transcription_service, "RECORDS_DIR", tmp_path / "records")
    monkeypatch.setattr(meeting_endpoints, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(summary_service, "SummaryNode", FakeSummaryNode)
    monkeypatch.setattr(summary_endpoints, "SUMMARY_POLL_INTERVAL", 0.02)
    main.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(email=USER)
    yield SimpleNamespace(db=db, tmp_path=tmp_path)
    main.app.dependency_overrides.clear()


def upload_source(tmp_path, meeting_id=MEETING_ID):
    """업로드가 끝난 상태로 만든다(원본 저장 + processing + 전사 작업)."""
    source = tmp_path / f"source_{meeting_id}_1.wav"
    source.write_bytes(wav_bytes())
    set_transcription_status(meeting_id, "processing", source_audio_path=str(source), set_source=True)
    return job_service.enqueue_job(meeting_id, "transcribe")


def jobs(db):
    with db() as session:
        return [(j.kind, j.status) for j in session.query(ProcessingJob).order_by(ProcessingJob.id)]


def run_worker_until_idle():
    worker = Worker(worker_id="w", poll_interval=0.01)
    while worker.run_once():
        pass


def get_status(meeting_id=MEETING_ID):
    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(f"/api/summary/{meeting_id}/status")
    return asyncio.run(run())


# ==================== 자동 요약 연쇄 ====================

def test_transcription_chains_summary(env):
    upload_source(env.tmp_path)

    run_worker_until_idle()

    assert jobs(env.db) == [("transcribe", "done"), ("summarize", "done")]
    assert load_meeting(MEETING_ID).transcription_status == "done"
    summary = load_summary(MEETING_ID)
    assert (summary.subject, summary.next_steps) == ("문서 검색 개편", ["1. 테스트 문서 준비."])
    assert [p.subject for p in summary.paragraphs] == ["검색 개편"]


def test_summary_auto_off_does_not_chain(env, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_AUTO", False)
    upload_source(env.tmp_path)

    run_worker_until_idle()

    assert jobs(env.db) == [("transcribe", "done")]
    assert load_summary(MEETING_ID).subject is None


def test_empty_transcript_does_not_chain(env, monkeypatch):
    monkeypatch.setattr(SegmentsProvider, "segments", [])  # 무음 등으로 인식된 말이 없다
    upload_source(env.tmp_path)

    run_worker_until_idle()

    assert jobs(env.db) == [("transcribe", "done")]


def test_failed_transcription_does_not_chain(env):
    job_id = upload_source(env.tmp_path)
    (env.tmp_path / f"source_{MEETING_ID}_1.wav").unlink()

    run_worker_until_idle()

    assert jobs(env.db) == [("transcribe", "failed")]
    assert job_service.get_job(job_id).error.startswith("SourceAudioMissing")


@pytest.mark.parametrize("value, expected", [(None, True), ("true", True), ("false", False), ("0", False)])
def test_summary_auto_setting(reload_config, value, expected):
    cfg = reload_config(**({"SUMMARY_AUTO": value} if value is not None else {}))
    assert cfg.SUMMARY_AUTO is expected


def test_invalid_summary_auto_stops_import(reload_config):
    with pytest.raises(ValueError, match="Invalid SUMMARY_AUTO"):
        reload_config(SUMMARY_AUTO="yes")


# ==================== 상태 API ====================

def test_status_none_without_summary_or_job(env):
    response = get_status()
    assert (response.status_code, response.json()) == (200, {"status": "none", "error": None})


def test_status_done_for_legacy_summary_without_job(env):
    """이 기능 이전에 버튼으로 만든 요약은 작업 기록이 없다. 저장된 요약이 있으면 done."""
    with env.db() as session:
        session.get(DBMeeting, MEETING_ID).subject = "예전 요약"
        session.commit()

    assert get_status().json() == {"status": "done", "error": None}


def test_status_follows_job_lifecycle(env):
    job_id = job_service.enqueue_job(MEETING_ID, "summarize")
    assert get_status().json()["status"] == "queued"

    job_service.claim_next_job("w")
    assert get_status().json()["status"] == "running"

    job_service.complete_job(job_id, "w")
    assert get_status().json() == {"status": "done", "error": None}


def test_status_failed_shows_error_then_new_request_is_queued(env):
    job_id = job_service.enqueue_job(MEETING_ID, "summarize")
    job_service.claim_next_job("w")
    job_service.fail_job(job_id, "w", "AttributeError: LLM returned nothing", retry=False)

    assert get_status().json() == {"status": "failed", "error": "AttributeError: LLM returned nothing"}

    job_service.enqueue_job(MEETING_ID, "summarize")  # 다시 생성
    assert get_status().json() == {"status": "queued", "error": None}


def test_status_ignores_meeting_notes_regeneration(env):
    job_service.enqueue_job(MEETING_ID, "regenerate_notes")
    assert get_status().json()["status"] == "none"


def test_status_of_someone_elses_meeting_is_404(env):
    assert get_status("other-meeting").status_code == 404


# ==================== 업로드부터 자동 요약까지 (HTTP + 워커) ====================

def test_upload_to_auto_summary_end_to_end(env):
    """업로드 → 워커 전사 → 자동 요약 → 상태 done → 요약 조회. 화면이 하는 폴링을 그대로 흉내 낸다."""
    worker = Worker(concurrency=2, worker_id="e2e-worker", poll_interval=0.02)
    thread = threading.Thread(target=worker.run_forever)
    thread.start()
    seen = []

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
            uploaded = await client.post(
                f"/api/meetings/{MEETING_ID}/upload-audio", files={"file": ("rec.wav", wav_bytes(), "audio/wav")}
            )
            assert uploaded.status_code == 202
            for _ in range(250):
                meeting = (await client.get(f"/api/meetings/{MEETING_ID}")).json()
                status = (await client.get(f"/api/summary/{MEETING_ID}/status")).json()["status"]
                if not seen or seen[-1] != (meeting["transcription_status"], status):
                    seen.append((meeting["transcription_status"], status))
                if status in ("done", "failed"):
                    break
                await asyncio.sleep(0.04)
            return meeting, (await client.get(f"/api/summary/{MEETING_ID}")).json()

    try:
        meeting, summary = asyncio.run(run())
    finally:
        worker.stop()
        thread.join(timeout=10)

    assert seen[0] == ("processing", "none")
    assert seen[-1] == ("done", "done")
    assert meeting["participants"] == ["화자A", "화자B"]
    assert [s["text"] for s in meeting["transcript"]] == ["오늘은 검색 개편 일정을 정하겠습니다.", "좋습니다."]
    assert summary["subject"] == "문서 검색 개편"
    assert jobs(env.db) == [("transcribe", "done"), ("summarize", "done")]
