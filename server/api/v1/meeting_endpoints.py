from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
import os
from datetime import datetime

from models.meeting import (
    Meeting,
    MeetingListResponse,
    MeetingSettingsRequest,
    UpdateSubjectRequest,
    UpdateTitleRequest,
    TranscriptRequest,
    UploadAudioResponse,
    TranscriptSegment,
    TranscriptSegmentResponse,
)
from services.meeting_service import (
    list_all_meetings,
    load_meeting,
    create_meeting,
    update_meeting_settings,
    update_transcript,
    add_audio_file,
    update_subject,
    update_meeting_title,
    delete_meeting,
    get_domain_keywords,
    update_meeting_domain,
)
from utils.config import RECORDS_DIR
from clova_stt import convert as clova_convert

router = APIRouter()

# UPLOADS_DIR은 config에서 설정한 RECORDS_DIR 사용
UPLOADS_DIR = RECORDS_DIR


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
    Upload WAV file and run STT conversion using Naver Clova Speech.
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

        # Create directory if it doesn't exist
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

        # Save file
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        # Call Clova STT conversion
        try:
            # 미팅에 도메인이 설정되어 있으면 해당 도메인의 키워드를 조회
            domain_keywords = None
            if hasattr(meeting, 'domain_id') and meeting.domain_id:
                # Meeting 모델에 domain_id를 반영해야 함 (아래에서 추가)
                # 여기서는 db 직접 조회로 처리
                from database import SessionLocal, Meeting as DBMeeting
                db = SessionLocal()
                try:
                    db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
                    if db_meeting and db_meeting.domain_id:
                        domain_keywords = get_domain_keywords(db_meeting.domain_id)
                finally:
                    db.close()

            segments = clova_convert(
                file_path=str(file_path),
                language="ko-KR",
                domain_keywords=domain_keywords
            )
        except Exception as e:
            print(f"Clova STT conversion error: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"STT conversion failed: {str(e)}"
            )

        # segments는 이미 TranscriptSegment 리스트 형식
        # Convert TranscriptSegment to TranscriptSegmentResponse for API response
        response_segments = []
        for seg in segments:
            speaker_index = seg.speaker_index % len(meeting.participants)  # 안전하게 인덱스 계산
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

    except HTTPException:
        raise
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


@router.put("/api/meetings/{meeting_id}/subject", response_model=Meeting)
async def update_meeting_subject(meeting_id: str, request: UpdateSubjectRequest):
    """Update meeting subject."""
    meeting = update_subject(meeting_id, request.subject)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.put("/api/meetings/{meeting_id}/title", response_model=Meeting)
async def update_meeting_title_endpoint(meeting_id: str, request: UpdateTitleRequest):
    """Update meeting title."""
    meeting = update_meeting_title(meeting_id, request.title)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.put("/api/meetings/{meeting_id}/domain", response_model=Meeting)
async def update_meeting_domain_endpoint(meeting_id: str, domain_id: str = None):
    """
    Update meeting domain for STT keyword boosting.
    
    Args:
        meeting_id: 미팅 ID
        domain_id: 도메인 ID (optional)
    """
    meeting = update_meeting_domain(meeting_id, domain_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.delete("/api/meetings/{meeting_id}")
async def delete_meeting_endpoint(meeting_id: str):
    """Delete a meeting and all related data (transcript, paragraph, next_steps)."""
    success = delete_meeting(meeting_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete meeting")
    return {"status": "ok", "message": "Meeting deleted successfully"}


@router.get("/api/meetings/{meeting_id}/download-audio")
async def download_audio(meeting_id: str):
    """Download the audio file for a meeting."""
    meeting = load_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    if not meeting.audio_files or len(meeting.audio_files) == 0:
        raise HTTPException(status_code=404, detail="No audio file found for this meeting")
    
    audio_file_path = meeting.audio_files[0]
    file_path = Path(audio_file_path)
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    
    # Extract filename from path for download
    filename = file_path.name
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="audio/wav"
    )


@router.get("/api/meetings/{meeting_id}/download-transcript")
async def download_transcript(meeting_id: str):
    """Download the transcript of a meeting as a text file."""
    meeting = load_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    if not meeting.transcript or len(meeting.transcript) == 0:
        raise HTTPException(status_code=404, detail="No transcript found for this meeting")
    
    # Build transcript text with speaker names
    transcript_text = f"회의록: {meeting.title}\n"
    transcript_text += f"생성일: {meeting.created_at}\n"
    transcript_text += f"참석자: {', '.join(meeting.participants)}\n"
    transcript_text += "=" * 60 + "\n\n"
    
    for segment in meeting.transcript:
        speaker_name = segment.speaker_name
        text = segment.text
        transcript_text += f"[{speaker_name}]\n{text}\n\n"
    
    # Convert text to bytes
    transcript_bytes = transcript_text.encode('utf-8')
    
    return StreamingResponse(
        iter([transcript_bytes]),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=transcript-{meeting_id}.txt"
        }
    )


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}