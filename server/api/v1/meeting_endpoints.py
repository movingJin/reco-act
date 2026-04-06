from fastapi import APIRouter, HTTPException, UploadFile, File
from pathlib import Path
import os
from datetime import datetime

from models import (
    Meeting,
    MeetingListResponse,
    MeetingSettingsRequest,
    TranscriptRequest,
    UploadAudioResponse,
    TranscriptSegment,
    TranscriptSegmentResponse,
)
from meeting_service import (
    list_all_meetings,
    load_meeting,
    create_meeting,
    update_meeting_settings,
    update_transcript,
    add_audio_file,
)
from database import init_db

router = APIRouter()

# 애플리케이션 시작 시 데이터베이스 테이블 초기화
@router.on_event("startup")
def startup_event():
    init_db()

UPLOADS_DIR = Path(__file__).parent.parent.parent / "records"
UPLOADS_DIR.mkdir(exist_ok=True)


def parse_stt_output(utterances: list, participants: list = None) -> list:
    segments = []
    if participants is None:
        participants = []

    for i, utterance in enumerate(utterances):
        speaker_index = utterance['spk']
        # Get speaker name from participants list
        speaker_name = participants[speaker_index]

        segments.append(TranscriptSegment(
            speaker_index=speaker_index,
            text=utterance['msg'],
            start=utterance['start_at'],
            end=utterance['start_at'] + utterance['duration']
        ))

    return segments


@router.get("/api/meetings", response_model=MeetingListResponse)
async def get_meetings():
    """Get list of all meetings."""
    meetings = list_all_meetings()
    return MeetingListResponse(meetings=meetings)


@router.post("/api/meetings", response_model=Meeting)
async def create_new_meeting(title: str, participants: list = None):
    """Create a new meeting."""
    meeting = create_meeting(title, participants)
    return meeting


@router.get("/api/meetings/{meeting_id}", response_model=Meeting)
async def get_meeting(meeting_id: str):
    """Get a specific meeting."""
    meeting = load_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.post("/api/meetings/{meeting_id}/settings", response_model=Meeting)
async def update_settings(meeting_id: str, request: MeetingSettingsRequest):
    """Update meeting settings (participants)."""
    meeting = update_meeting_settings(meeting_id, request.participants)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.post("/api/meetings/{meeting_id}/upload-audio", response_model=UploadAudioResponse)
async def upload_audio(meeting_id: str, file: UploadFile = File(...)):
    """
    Upload WAV file and run STT conversion.
    Returns the transcription segments.
    """
    # Verify meeting exists
    meeting = load_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    try:
        # Save uploaded file
        timestamp = int(datetime.now().timestamp() * 1000)
        filename = f"meeting_{meeting_id}_{timestamp}.wav"
        file_path = UPLOADS_DIR / filename

        # Save file
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        # Call STT convert function
        # Set environment for stt.py
        os.environ["AUDIO_PATH"] = str(file_path)
        os.environ["PRESET"] = "sommers_basic"

        try:
            from stt import convert
            raw_transcript = convert()
        except Exception as e:
            print(f"STT conversion error: {e}")
            # Fallback: return placeholder if STT fails
            raw_transcript = f"[STT 변환 오류] {str(e)}"

        # Parse transcript into segments
        segments = parse_stt_output(raw_transcript, meeting.participants)

        # Convert TranscriptSegment to TranscriptSegmentResponse for API response
        response_segments = []
        for seg in segments:
            speaker_index = seg.speaker_index
            speaker_name = meeting.participants[speaker_index]
            response_segments.append(TranscriptSegmentResponse(
                speaker_index=speaker_index,
                speaker_name=speaker_name,
                text=seg.text,
                start=seg.start,
                end=seg.end
            ))

        # Save the transcript segments to the meeting (using internal format)
        transcript_data = [seg.model_dump() for seg in segments]
        update_transcript(meeting_id, transcript_data)

        # Update meeting with audio file reference
        add_audio_file(meeting_id, str(file_path))

        return UploadAudioResponse(
            status="ok",
            segments=response_segments,
            meeting_id=meeting_id
        )

    except Exception as e:
        print(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/api/meetings/{meeting_id}/transcript", response_model=Meeting)
async def update_meeting_transcript(meeting_id: str, request: TranscriptRequest):
    """Update and save the transcript of a meeting."""
    # Convert TranscriptSegmentResponse to internal format (speaker_index only)
    transcript_data = [
        {
            'speaker_index': seg.speaker_index,
            'text': seg.text,
            'start': seg.start,
            'end': seg.end
        }
        for seg in request.transcript
    ]
    meeting = update_transcript(meeting_id, transcript_data)

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    return meeting


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}