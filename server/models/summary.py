from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime


class Paragraph(BaseModel):
    """Internal model for paragraph segment (uses meeting id)."""
    id: int
    subject: str
    summary: str
    start: int = 0
    end: int = 0


class UpdateParagraphRequest(BaseModel):
    """Request model for updating a paragraph."""
    subject: str
    summary: str
    start: int = 0
    end: int = 0


class UpdateNextStepRequest(BaseModel):
    """Request model for updating next steps."""
    next_steps: List[str]


class SummaryResponse(BaseModel):
    """API response model for meeting segment (uses speaker name)."""
    meeting_id: str
    paragraphs: List[Paragraph]
    next_steps: List[str]
    subject: Optional[str] = None
