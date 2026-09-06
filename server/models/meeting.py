from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class TranscriptSegment(BaseModel):
    """Internal model for transcript segment (uses speaker index)."""
    speaker_index: int
    text: str
    start: int = 0  # milliseconds
    end: int = 0    # milliseconds


class TranscriptSegmentResponse(BaseModel):
    """API response model for transcript segment (uses speaker name)."""
    speaker_index: int
    speaker_name: str
    text: str
    start: int = 0  # milliseconds
    end: int = 0    # milliseconds


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
    domain_id: Optional[int] = None  # References DomainKeywords.id
    # STT 변환 상태: 'processing' | 'done' | 'failed' | None(미처리)
    transcription_status: Optional[str] = None
    # 녹음 파일의 실제 길이(밀리초). STT 변환 완료 전에는 None.
    duration_ms: Optional[int] = None


class MeetingListResponse(BaseModel):
    meetings: List[Meeting]


class UploadAudioResponse(BaseModel):
    status: str
    segments: List[TranscriptSegmentResponse]
    meeting_id: str
