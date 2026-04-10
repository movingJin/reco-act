from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class TranscriptSegment(BaseModel):
    """Internal model for transcript segment (uses speaker index)."""
    speaker_index: int
    text: str
    start: float = 0.0
    end: float = 0.0


class TranscriptSegmentResponse(BaseModel):
    """API response model for transcript segment (uses speaker name)."""
    speaker_index: int
    speaker_name: str
    text: str
    start: float = 0.0
    end: float = 0.0


class MeetingSettingsRequest(BaseModel):
    participants: List[str]


class UpdateSubjectRequest(BaseModel):
    subject: str


class UpdateTitleRequest(BaseModel):
    title: str


class TranscriptRequest(BaseModel):
    transcript: List[TranscriptSegmentResponse]


class Meeting(BaseModel):
    id: str
    title: str
    created_at: str
    participants: List[str]
    transcript: List[TranscriptSegmentResponse] = []
    audio_files: List[str] = []
    subject: Optional[str] = None


class MeetingListResponse(BaseModel):
    meetings: List[Meeting]


class UploadAudioResponse(BaseModel):
    status: str
    segments: List[TranscriptSegmentResponse]
    meeting_id: str
