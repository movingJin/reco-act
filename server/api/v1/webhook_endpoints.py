"""외부 서비스(Clova STT)의 비동기 처리 완료 콜백을 수신하는 웹훅."""
from fastapi import APIRouter, BackgroundTasks, Request

from services.transcription_service import handle_clova_callback

router = APIRouter()


@router.post("/api/webhooks/clova/{meeting_id}")
async def clova_callback(meeting_id: str, request: Request, background_tasks: BackgroundTasks):
    """Clova Speech가 비동기(콜백) 인식 완료 시 알림을 전달하는 엔드포인트.

    인증 없이 외부(네이버)에서 호출되는 공개 엔드포인트이므로, 실제 위조 방지는
    handle_clova_callback에서 제출 시 저장해둔 job token과 대조해 처리한다.

    실제 처리(결과 재조회 + 재시도)는 수십 초가 걸릴 수 있어 응답은 즉시 돌려주고
    처리는 백그라운드로 넘긴다. 응답이 느리면 Naver가 같은 콜백을 중복 전송하는
    것으로 관측됐는데, handle_clova_callback은 job token을 원자적으로 선점해
    중복 호출이 와도 딱 한 번만 처리하도록 되어 있다.
    """
    payload = await request.json()
    background_tasks.add_task(handle_clova_callback, meeting_id, payload)
    return {"message": "ok"}
