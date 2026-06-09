from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
from datetime import datetime
from sqlalchemy.orm import Session

from models.meeting import (
    Meeting,
    MeetingListResponse,
    MeetingSettingsRequest,
    UpdateSubjectRequest,
    UpdateTitleRequest,
    TranscriptRequest,
    UploadAudioResponse,
    TranscriptSegmentResponse,
)
from services.meeting_service import (
    list_all_meetings,
    load_meeting,
    create_meeting,
    update_meeting_settings,
    update_transcript,
    update_subject,
    update_meeting_title,
    delete_meeting,
    update_meeting_domain,
    set_transcription_status,
)
from services.transcription_service import process_audio_transcription
from utils.config import RECORDS_DIR
from utils.auth import get_current_user
from database import get_db, User, Meeting as DBMeeting

router = APIRouter()

# UPLOADS_DIR은 config에서 설정한 RECORDS_DIR 사용
UPLOADS_DIR = RECORDS_DIR


def verify_meeting_ownership(meeting_id: str, user_email: str, db: Session) -> None:
    """미팅의 소유자가 user_email과 일치하는지 확인합니다. 없거나 다른 소유자면 404로 응답."""
    owned = (
        db.query(DBMeeting.id)
        .filter(DBMeeting.id == meeting_id, DBMeeting.user_id == user_email)
        .first()
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Meeting not found")


@router.get("/api/meetings", response_model=MeetingListResponse)
async def get_meetings(current_user: User = Depends(get_current_user)):
    """Get list of meetings owned by the current user."""
    meetings = list_all_meetings(current_user.email)
    return MeetingListResponse(meetings=meetings)


@router.post("/api/meetings", response_model=Meeting)
async def create_new_meeting(title: str, participants: list = None, current_user: User = Depends(get_current_user)):
    """Create a new meeting."""
    meeting = create_meeting(title, participants, current_user.email)
    return meeting


@router.get("/api/meetings/{meeting_id}", response_model=Meeting)
async def get_meeting(meeting_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get a specific meeting."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    meeting = load_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.post("/api/meetings/{meeting_id}/settings", response_model=Meeting)
async def update_settings(meeting_id: str, request: MeetingSettingsRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update meeting settings (participants)."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    meeting = update_meeting_settings(meeting_id, request.participants)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.post("/api/meetings/{meeting_id}/upload-audio", response_model=UploadAudioResponse, status_code=202)
async def upload_audio(
    meeting_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    업로드된 오디오 파일을 저장하고 STT 변환은 백그라운드로 처리한다.

    파일 수신이 끝나면 즉시 202(status='processing')로 응답하므로, 응답이 수 분간
    지연돼(화면 꺼짐 등으로) 끊기는 문제가 사라진다. 프론트는 이후 회의 상태를
    폴링해 변환 완료(done)/실패(failed)를 확인한다.
    """
    verify_meeting_ownership(meeting_id, current_user.email, db)

    # Verify meeting exists
    meeting = load_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    try:
        # 원본 업로드 파일을 디스크에 스트리밍 저장한다(메모리에 전체를 올리지 않음).
        # 변환(ffmpeg)·STT는 백그라운드에서 수행하므로 여기서는 저장만 한다.
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = int(datetime.now().timestamp() * 1000)
        suffix = Path(file.filename or "upload").suffix or ".dat"
        source_path = UPLOADS_DIR / f"source_{meeting_id}_{timestamp}{suffix}"

        with open(source_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)

        # 상태를 processing으로 표시하고, 재처리(서버 재시작 복구)를 위해 원본 경로를 보관.
        set_transcription_status(
            meeting_id,
            "processing",
            source_audio_path=str(source_path),
            set_source=True,
        )

        # 응답 전송 후 백그라운드에서 변환 수행 (동기 함수 → threadpool 실행)
        background_tasks.add_task(process_audio_transcription, meeting_id)

        return UploadAudioResponse(
            status="processing",
            segments=[],
            meeting_id=meeting_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/api/meetings/{meeting_id}/transcript", response_model=Meeting)
async def update_meeting_transcript(meeting_id: str, request: TranscriptRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update and save the transcript of a meeting."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
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
async def update_meeting_subject(meeting_id: str, request: UpdateSubjectRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update meeting subject."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    meeting = update_subject(meeting_id, request.subject)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.put("/api/meetings/{meeting_id}/title", response_model=Meeting)
async def update_meeting_title_endpoint(meeting_id: str, request: UpdateTitleRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update meeting title."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    meeting = update_meeting_title(meeting_id, request.title)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.put("/api/meetings/{meeting_id}/domain", response_model=Meeting)
async def update_meeting_domain_endpoint(meeting_id: str, domain_id: str = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Update meeting domain for STT keyword boosting.

    Args:
        meeting_id: 미팅 ID
        domain_id: 도메인 ID (optional)
    """
    verify_meeting_ownership(meeting_id, current_user.email, db)
    meeting = update_meeting_domain(meeting_id, domain_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.delete("/api/meetings/{meeting_id}")
async def delete_meeting_endpoint(meeting_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete a meeting and all related data (transcript, paragraph, next_steps)."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
    success = delete_meeting(meeting_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete meeting")
    return {"status": "ok", "message": "Meeting deleted successfully"}


@router.get("/api/meetings/{meeting_id}/download-audio")
async def download_audio(meeting_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download the audio file for a meeting."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
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
async def download_transcript(meeting_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download the transcript of a meeting as a text file."""
    verify_meeting_ownership(meeting_id, current_user.email, db)
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
