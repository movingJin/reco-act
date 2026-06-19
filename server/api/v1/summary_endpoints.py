from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from models.summary import (
    SummaryResponse,
    UpdateParagraphRequest,
    UpdateNextStepRequest,
    UpdateMeetingNotesRequest,
)
from utils.auth import get_current_user
from database import get_db, User, Meeting as DBMeeting

from services.summary_service import (
    load_summary,
    create_summary,
    update_paragraph,
    update_next_steps,
    update_meeting_notes,
    regenerate_meeting_notes,
    generate_meeting_notes_docx,
)
from services.meeting_service import load_meeting
from utils.email import send_summary_email

router = APIRouter()


def verify_meeting_ownership(meeting_id: str, user_email: str, db: Session) -> None:
    """미팅의 소유자가 user_email과 일치하는지 확인합니다. 없거나 다른 소유자면 404로 응답."""
    owned = (
        db.query(DBMeeting.id)
        .filter(DBMeeting.id == meeting_id, DBMeeting.user_id == user_email)
        .first()
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Meeting not found")


@router.get("/api/summary/{meeting_id}", response_model=SummaryResponse)
async def get_summary(meeting_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get summary of a meeting."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    summary = load_summary(meeting_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")
    return summary


@router.post("/api/summary/{meeting_id}", response_model=SummaryResponse)
async def create_new_summary(meeting_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a new summary from meeting transcripts."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    summary = create_summary(meeting_id)
    if not summary:
        raise HTTPException(status_code=400, detail="Failed to create summary")
    return summary


@router.put("/api/summary/{meeting_id}/paragraph/{paragraph_id}", response_model=SummaryResponse)
async def update_paragraph_endpoint(meeting_id: str, paragraph_id: int, request: UpdateParagraphRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update a specific paragraph of a meeting."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    paragraph_data = request.dict()
    result = update_paragraph(meeting_id, paragraph_id, paragraph_data)
    if not result:
        raise HTTPException(status_code=404, detail="Paragraph not found")
    return result


@router.put("/api/summary/{meeting_id}/next-steps", response_model=SummaryResponse)
async def update_next_steps_endpoint(meeting_id: str, request: UpdateNextStepRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update next steps of a meeting."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    result = update_next_steps(meeting_id, request.next_steps)
    if not result:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return result


@router.put("/api/summary/{meeting_id}/meeting-notes", response_model=SummaryResponse)
async def update_meeting_notes_endpoint(meeting_id: str, request: UpdateMeetingNotesRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """회의록(meeting_notes) 본문을 저장합니다. 미리보기에서 편집한 내용을 반영하는 데 사용합니다."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    result = update_meeting_notes(meeting_id, request.meeting_notes)
    if not result:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return result


@router.post("/api/summary/{meeting_id}/meeting-notes/regenerate", response_model=SummaryResponse)
async def regenerate_meeting_notes_endpoint(meeting_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """기존 요약은 유지한 채 회의록(meeting_notes)만 다시 생성합니다."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    result = regenerate_meeting_notes(meeting_id)
    if not result:
        raise HTTPException(status_code=400, detail="Failed to regenerate meeting notes")
    return result


@router.get("/api/summary/{meeting_id}/download")
async def download_summary(meeting_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download the meeting notes (회의록) as a Word(.docx) file."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    summary = load_summary(meeting_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")
    if not summary.meeting_notes:
        raise HTTPException(status_code=404, detail="Meeting notes not found")

    meeting = load_meeting(meeting_id)

    docx_bytes = generate_meeting_notes_docx(summary, meeting)

    return StreamingResponse(
        iter([docx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename=meeting-notes-{meeting_id}.docx"
        }
    )


@router.post("/api/summary/{meeting_id}/email")
async def email_summary(meeting_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """현재 로그인된 사용자의 이메일로 회의록(Word 문서)을 전송합니다."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    summary = load_summary(meeting_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")
    if not summary.meeting_notes:
        raise HTTPException(status_code=404, detail="Meeting notes not found")

    meeting = load_meeting(meeting_id)
    meeting_title = meeting.title if meeting and meeting.title else "회의록"

    docx_bytes = generate_meeting_notes_docx(summary, meeting)

    sent = send_summary_email(
        recipient_email=current_user.email,
        meeting_title=meeting_title,
        attachment_bytes=docx_bytes,
        attachment_filename=f"meeting-notes-{meeting_id}.docx",
    )
    if not sent:
        raise HTTPException(status_code=500, detail="Failed to send summary email")

    return {"message": "회의록이 이메일로 전송되었습니다", "email": current_user.email}