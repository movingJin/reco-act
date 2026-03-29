from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

app = FastAPI(title="Meeting Transcription API", version="1.0.0")

# 애플리케이션 시작 시 데이터베이스 테이블 초기화
@app.on_event("startup")
def startup_event():
    init_db()

# CORS 설정 (개발 환경)
cors_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOADS_DIR = Path(__file__).parent / "records"
UPLOADS_DIR.mkdir(exist_ok=True)


def parse_stt_output(raw_text: str) -> list:
    """
    Simple parser to convert raw STT output to speaker segments.
    If the output doesn't have speaker info, we'll distribute it across configured participants.
    
    Expected format: "화자1: 텍스트1\n화자2: 텍스트2" or just plain text
    """
    segments = []
    
    # Try to parse speaker format
    lines = raw_text.strip().split('\n')
    
    for i, line in enumerate(lines):
        if ':' in line:
            parts = line.split(':', 1)
            speaker = parts[0].strip()
            text = parts[1].strip()
        else:
            # If no speaker info, use rotating assignment
            speaker = f"화자{(i % 2) + 1}"
            text = line.strip()
        
        if text:  # Only add non-empty segments
            segments.append(TranscriptSegment(
                speaker=speaker,
                text=text,
                start=float(i * 5),  # Simple time estimation
                end=float((i + 1) * 5)
            ))
    
    return segments


@app.get("/api/meetings", response_model=MeetingListResponse)
async def get_meetings():
    """Get list of all meetings."""
    meetings = list_all_meetings()
    return MeetingListResponse(meetings=meetings)


@app.post("/api/meetings", response_model=Meeting)
async def create_new_meeting(title: str, participants: list = None):
    """Create a new meeting."""
    meeting = create_meeting(title, participants)
    return meeting


@app.get("/api/meetings/{meeting_id}", response_model=Meeting)
async def get_meeting(meeting_id: str):
    """Get a specific meeting."""
    meeting = load_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@app.post("/api/meetings/{meeting_id}/settings", response_model=Meeting)
async def update_settings(meeting_id: str, request: MeetingSettingsRequest):
    """Update meeting settings (participants)."""
    meeting = update_meeting_settings(meeting_id, request.participants)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@app.post("/api/meetings/{meeting_id}/upload-audio", response_model=UploadAudioResponse)
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
        segments = parse_stt_output(raw_transcript)
        
        # Save the transcript segments to the meeting
        transcript_data = [seg.model_dump() for seg in segments]
        update_transcript(meeting_id, transcript_data)
        
        # Update meeting with audio file reference
        add_audio_file(meeting_id, str(file_path))
        
        return UploadAudioResponse(
            status="ok",
            raw_transcript=raw_transcript,
            segments=segments,
            meeting_id=meeting_id
        )
    
    except Exception as e:
        print(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.post("/api/meetings/{meeting_id}/transcript", response_model=Meeting)
async def update_meeting_transcript(meeting_id: str, request: TranscriptRequest):
    """Update and save the transcript of a meeting."""
    # Convert to dict for service layer
    transcript_data = [seg.model_dump() for seg in request.transcript]
    meeting = update_transcript(meeting_id, transcript_data)
    
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    return meeting


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
