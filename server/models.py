from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class TranscriptSegment(BaseModel):
    speaker: str
    text: str
    start: float = 0.0
    end: float = 0.0


class MeetingSettingsRequest(BaseModel):
    participants: List[str]


class TranscriptRequest(BaseModel):
    transcript: List[TranscriptSegment]


class Meeting(BaseModel):
    id: str
    title: str
    created_at: str
    participants: List[str]
    transcript: List[TranscriptSegment] = []
    audio_files: List[str] = []


class MeetingListResponse(BaseModel):
    meetings: List[Meeting]


class UploadAudioResponse(BaseModel):
    status: str
    raw_transcript: str
    segments: List[TranscriptSegment]
    meeting_id: str
