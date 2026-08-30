"""외부 서비스(Clova STT)의 비동기 처리 완료 콜백을 수신하는 웹훅."""
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from services.transcription_service import handle_clova_callback

router = APIRouter()


@router.post("/api/webhooks/clova/{meeting_id}")
async def clova_callback(meeting_id: str, request: Request):
    """Clova Speech가 비동기(콜백) 인식 완료 시 알림을 전달하는 엔드포인트.

    인증 없이 외부(네이버)에서 호출되는 공개 엔드포인트이므로, 실제 위조 방지는
    handle_clova_callback에서 제출 시 저장해둔 job token과 대조해 처리한다.

    handle_clova_callback은 결과 조회를 위해 재시도(sleep)할 수 있는 동기 함수라,
    이벤트 루프를 막지 않도록 threadpool에서 실행한다.
    """
    payload = await request.json()
    await run_in_threadpool(handle_clova_callback, meeting_id, payload)
    return {"message": "ok"}
