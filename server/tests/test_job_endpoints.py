"""업로드·요약 API가 작업 큐를 거치는지 (01-plan 3.5·3.7, PR-5b).

실제 HTTP 경로(httpx ASGITransport)로 엔드포인트를 부르고, 테스트 DB와 워커 스레드(기본 핸들러)로 처리한다.
LLM 호출(SummaryNode, create_meeting_notes)만 대역으로 바꾼다. 응답 형식은 기존(SummaryResponse)과 같아야 한다.
"""
import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest

import main
from api.v1 import meeting_endpoints, summary_endpoints
from database import Meeting as DBMeeting, ProcessingJob, User as DBUser
from models.summary import SummaryResponse
from services import job_service, summary_service
from services.summary_service import load_summary
from utils import config
from utils.auth import get_current_user
from worker import Worker

USER = "tester@example.com"
MEETING_ID = "m1"


class FakeSummaryNode:
    calls = 0

    def run(self, meeting_id):
        FakeSummaryNode.calls += 1
        return SimpleNamespace(
            paragraphs=[{"subject": "검색 개편", "start": 0, "end": 76000, "summary": "일정과 역할을 정함."}],
            next_steps=["1. 테스트 문서 준비."],
            subject="문서 검색 개편",
            meeting_notes="1. 회의 개요",
        )


class FailingSummaryNode:
    def run(self, meeting_id):
        return None  # 실제 SummaryNode는 LLM 오류를 잡고 None을 돌려준다


@pytest.fixture
def app_env(db, monkeypatch):
    with db() as session:
        session.add(DBUser(email=USER, name="tester"))
        session.add(DBMeeting(id=MEETING_ID, user_id=USER, title="회의", participants=["화자1", "화자2"]))
        session.commit()
    FakeSummaryNode.calls = 0
    monkeypatch.setattr(summary_service, "SummaryNode", FakeSummaryNode)
    monkeypatch.setattr(summary_endpoints, "SUMMARY_POLL_INTERVAL", 0.02)
    main.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(email=USER)
    yield
    main.app.dependency_overrides.clear()


@pytest.fixture
def worker(app_env):
    w = Worker(concurrency=2, worker_id="api-test-worker", poll_interval=0.02)
    thread = threading.Thread(target=w.run_forever)
    thread.start()
    yield w
    w.stop()
    thread.join(timeout=10)


def call(*requests):
    """요청들을 동시에 보내고 응답을 순서대로 돌려준다. 각 요청은 (method, url, kwargs)."""
    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
            return await asyncio.gather(*(client.request(method, url, **kwargs) for method, url, kwargs in requests))
    return asyncio.run(run())


def jobs(db, kind=None):
    with db() as session:
        query = session.query(ProcessingJob)
        if kind:
            query = query.filter(ProcessingJob.kind == kind)
        return [(j.kind, j.status) for j in query.order_by(ProcessingJob.id)]


# ==================== 요약 ====================

def test_create_summary_runs_on_worker_and_keeps_response_format(db, worker):
    (response,) = call(("POST", f"/api/summary/{MEETING_ID}", {}))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(SummaryResponse.model_fields)  # 기존 모바일 앱이 쓰는 형식 그대로
    assert body == load_summary(MEETING_ID).model_dump()
    assert (body["subject"], body["next_steps"], body["meeting_notes"]) == ("문서 검색 개편", ["1. 테스트 문서 준비."], "1. 회의 개요")
    assert jobs(db) == [("summarize", "done")]


def test_summary_request_waits_for_active_job_instead_of_creating_one(db, app_env):
    """자동 요약 등으로 이미 대기 중인 요약 작업이 있으면 새로 만들지 않고 그 작업을 기다린다."""
    job_service.enqueue_job(MEETING_ID, "summarize")
    w = Worker(worker_id="late-worker", poll_interval=0.02)

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
            request = asyncio.create_task(client.post(f"/api/summary/{MEETING_ID}"))
            await asyncio.sleep(0.3)
            assert not request.done()
            assert await asyncio.to_thread(jobs, db) == [("summarize", "queued")]
            await asyncio.to_thread(w.run_once)  # 워커가 대기 작업을 처리한다
            return await request

    response = asyncio.run(run())

    assert response.status_code == 200
    assert jobs(db) == [("summarize", "done")]
    assert FakeSummaryNode.calls == 1


def test_summary_failure_returns_500(db, worker, monkeypatch):
    monkeypatch.setattr(summary_service, "SummaryNode", FailingSummaryNode)

    (response,) = call(("POST", f"/api/summary/{MEETING_ID}", {}))

    assert (response.status_code, response.json()["detail"]) == (500, "Failed to create summary")
    assert jobs(db) == [("summarize", "failed")]


def test_summary_wait_timeout_returns_504_and_job_keeps_going(db, app_env, monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_WAIT_TIMEOUT", 0.3)

    (response,) = call(("POST", f"/api/summary/{MEETING_ID}", {}))  # 워커가 없어 끝나지 않는다

    assert response.status_code == 504
    assert jobs(db) == [("summarize", "queued")]  # 작업은 남아 있고, 워커가 뜨면 처리된다
    Worker(worker_id="w", poll_interval=0.02).run_once()
    assert jobs(db) == [("summarize", "done")]
    assert load_summary(MEETING_ID).subject == "문서 검색 개편"


def test_waiting_does_not_block_other_requests(db, app_env, monkeypatch):
    """요약을 기다리는 동안 이벤트 루프가 막히지 않는다(R17). 같은 서버의 다른 요청이 바로 응답한다.

    조회 간격을 길게(0.5초) 두어, 기다리는 동안 루프를 붙잡는 구현이면 다른 요청이 그만큼 늦어지게 한다.
    """
    monkeypatch.setattr(summary_endpoints, "SUMMARY_POLL_INTERVAL", 0.5)
    w = Worker(worker_id="slow-worker", poll_interval=0.02)
    timings = {}

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
            async def post():
                return await client.post(f"/api/summary/{MEETING_ID}")

            async def get():
                await asyncio.sleep(0.1)  # POST가 기다리기 시작한 뒤
                loop = asyncio.get_running_loop()
                started = loop.time()
                response = await client.get(f"/api/summary/{MEETING_ID}")
                timings["get"] = loop.time() - started
                return response

            async def process_later():
                await asyncio.sleep(0.8)
                await asyncio.to_thread(w.run_once)

            return await asyncio.gather(post(), get(), process_later())

    post_response, get_response, _ = asyncio.run(run())

    assert (post_response.status_code, get_response.status_code) == (200, 200)
    assert timings["get"] < 0.25, timings


# ==================== 회의록 재생성 ====================

def test_regenerate_meeting_notes_runs_on_worker(db, worker, monkeypatch):
    call(("POST", f"/api/summary/{MEETING_ID}", {}))
    monkeypatch.setattr(summary_service, "create_meeting_notes", lambda state: {"meeting_notes": "2. 다시 만든 회의록"})

    (response,) = call(("POST", f"/api/summary/{MEETING_ID}/meeting-notes/regenerate", {}))

    assert response.status_code == 200
    assert response.json()["meeting_notes"] == "2. 다시 만든 회의록"
    assert response.json()["subject"] == "문서 검색 개편"  # 요약은 그대로
    assert jobs(db, "regenerate_notes") == [("regenerate_notes", "done")]


def test_regenerate_failure_returns_400(db, worker, monkeypatch):
    def broken(state):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(summary_service, "create_meeting_notes", broken)

    (response,) = call(("POST", f"/api/summary/{MEETING_ID}/meeting-notes/regenerate", {}))

    assert (response.status_code, response.json()["detail"]) == (400, "Failed to regenerate meeting notes")
    assert jobs(db, "regenerate_notes") == [("regenerate_notes", "failed")]


# ==================== 업로드 ====================

def test_upload_enqueues_transcription(db, app_env, tmp_path, monkeypatch):
    monkeypatch.setattr(meeting_endpoints, "UPLOADS_DIR", tmp_path)
    upload = ("POST", f"/api/meetings/{MEETING_ID}/upload-audio", {"files": {"file": ("rec.m4a", b"fake-audio")}})

    (first,) = call(upload)
    (second,) = call(upload)  # 처리 전에 다시 올려도 대기 작업은 하나

    assert first.status_code == second.status_code == 202
    assert first.json() == {"status": "processing", "segments": [], "meeting_id": MEETING_ID}
    assert jobs(db) == [("transcribe", "queued")]
    with db() as session:
        meeting = session.get(DBMeeting, MEETING_ID)
        assert meeting.transcription_status == "processing"
        assert meeting.source_audio_path.startswith(str(tmp_path)) and meeting.source_audio_path.endswith(".m4a")
    assert len(list(tmp_path.glob(f"source_{MEETING_ID}_*.m4a"))) == 2
