from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from models.summary import (
    SummaryResponse,
    UpdateParagraphRequest,
    UpdateNextStepRequest,
)

from services.summary_service import (
    load_summary,
    create_summary,
    update_paragraph,
    update_next_steps,
    generate_summary_text,
)

router = APIRouter()


@router.get("/api/summary/{meeting_id}", response_model=SummaryResponse)
async def get_summary(meeting_id: str):
    """Get summary of a meeting."""
    summary = load_summary(meeting_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")
    return summary


@router.post("/api/summary/{meeting_id}", response_model=SummaryResponse)
async def create_new_summary(meeting_id: str):
    """Create a new summary from meeting transcripts."""
    summary = create_summary(meeting_id)
    if not summary:
        raise HTTPException(status_code=400, detail="Failed to create summary")
    return summary


@router.put("/api/summary/{meeting_id}/paragraph/{paragraph_id}", response_model=SummaryResponse)
async def update_paragraph_endpoint(meeting_id: str, paragraph_id: int, request: UpdateParagraphRequest):
    """Update a specific paragraph of a meeting."""
    paragraph_data = request.dict()
    result = update_paragraph(meeting_id, paragraph_id, paragraph_data)
    if not result:
        raise HTTPException(status_code=404, detail="Paragraph not found")
    return result


@router.put("/api/summary/{meeting_id}/next-steps", response_model=SummaryResponse)
async def update_next_steps_endpoint(meeting_id: str, request: UpdateNextStepRequest):
    """Update next steps of a meeting."""
    result = update_next_steps(meeting_id, request.next_steps)
    if not result:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return result


@router.get("/api/summary/{meeting_id}/download")
async def download_summary(meeting_id: str):
    """Download the summary of a meeting as a text file."""
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