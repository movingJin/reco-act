from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from models.summary import (
    SummaryResponse,
    UpdateParagraphRequest,
    UpdateNextStepRequest,
)
from utils.auth import get_current_user
from database import get_db, User, Meeting as DBMeeting

from services.summary_service import (
    load_summary,
    create_summary,
    update_paragraph,
    update_next_steps,
    generate_summary_text,
)

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


@router.get("/api/summary/{meeting_id}/download")
async def download_summary(meeting_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download the summary of a meeting as a text file."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    summary = load_summary(meeting_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")
    
    # Generate summary text
    summary_text = generate_summary_text(summary)
    
    # Convert text to bytes
    summary_bytes = summary_text.encode('utf-8')
    
    return StreamingResponse(
        iter([summary_bytes]),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=summary-{meeting_id}.txt"
        }
    )