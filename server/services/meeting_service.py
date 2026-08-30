from datetime import datetime
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from database import SessionLocal, Meeting as DBMeeting, Transcript as DBTranscript, DomainKeywords as DBDomainKeywords, User as DBUser
from models.meeting import Meeting, TranscriptSegmentResponse


def get_db():
    """Get database session."""
    return SessionLocal()


def db_meeting_to_model(db_meeting: DBMeeting, db_transcripts: List[DBTranscript] = None) -> Meeting:
    """Convert database models to Pydantic Meeting model."""
    participants = list(db_meeting.participants) if db_meeting.participants else []
    
    transcript_segments = []
    if db_transcripts:
        for t in db_transcripts:
            # Get speaker name from participants using speaker_index
            speaker_index = t.speaker_index
            speaker_name = participants[speaker_index] if speaker_index < len(participants) else "Unknown"
            
            transcript_segments.append(
                TranscriptSegmentResponse(
                    speaker_index=speaker_index,
                    speaker_name=speaker_name,
                    text=t.text,
                    start=t.start_time,
                    end=t.end_time
                )
            )
    
    audio_files = [db_meeting.audio_file] if db_meeting.audio_file else []
    
    return Meeting(
        id=db_meeting.id,
        title=db_meeting.title,
        created_at=db_meeting.created_at.isoformat() + "Z" if db_meeting.created_at else datetime.now().isoformat() + "Z",
        participants=participants,
        transcript=transcript_segments,
        audio_files=audio_files,
        subject=db_meeting.subject,
        domain_id=db_meeting.domain_id,
        transcription_status=db_meeting.transcription_status,
    )


def load_meeting(meeting_id: str) -> Optional[Meeting]:
    """Load a meeting from database."""
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None
        
        db_transcripts = db.query(DBTranscript).filter(DBTranscript.meeting_id == meeting_id).all()
        return db_meeting_to_model(db_meeting, db_transcripts)
    except Exception as e:
        print(f"Error loading meeting {meeting_id}: {e}")
        return None
    finally:
        db.close()


def save_meeting(meeting: Meeting) -> bool:
    """Save a meeting to database."""
    db = get_db()
    try:
        # Check if meeting exists
        existing = db.query(DBMeeting).filter(DBMeeting.id == meeting.id).first()
        
        # Get audio_file from audio_files list
        audio_file = meeting.audio_files[0] if meeting.audio_files else None
        
        if existing:
            # Update existing meeting
            existing.title = meeting.title
            existing.participants = meeting.participants
            existing.audio_file = audio_file
        else:
            # Create new meeting
            db_meeting = DBMeeting(
                id=meeting.id,
                title=meeting.title,
                created_at=datetime.fromisoformat(meeting.created_at.replace("Z", "+00:00")),
                participants=meeting.participants,
                audio_file=audio_file
            )
            db.add(db_meeting)
        
        db.commit()
        return True
    except Exception as e:
        print(f"Error saving meeting {meeting.id}: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def list_all_meetings(user_id: str) -> List[Meeting]:
    """List meetings owned by user_id without transcripts for faster initial load."""
    db = get_db()
    meetings = []
    try:
        db_meetings = (
            db.query(DBMeeting)
            .filter(DBMeeting.user_id == user_id)
            .order_by(DBMeeting.created_at.desc())
            .all()
        )

        for db_meeting in db_meetings:
            # Load meeting without transcripts for faster initial list load
            meetings.append(db_meeting_to_model(db_meeting, db_transcripts=None))
    except Exception as e:
        print(f"Error listing meetings: {e}")
    finally:
        db.close()

    return meetings


def create_meeting(title: str, participants: Optional[List[str]] = None, user_id: str = None) -> Meeting:
    """Create a new meeting in database. domain_id는 사용자의 기본 도메인을 상속한다."""
    db = get_db()
    try:
        meeting_id = f"m_{int(datetime.now().timestamp() * 1000)}"

        default_domain_id = None
        if user_id:
            user = db.query(DBUser).filter(DBUser.email == user_id).first()
            if user:
                default_domain_id = user.domain_id

        db_meeting = DBMeeting(
            id=meeting_id,
            user_id=user_id,
            title=title,
            created_at=datetime.utcnow(),
            participants=participants or [f"화자{i+1}" for i in range(2)],
            domain_id=default_domain_id,
        )

        db.add(db_meeting)
        db.commit()

        meeting = Meeting(
            id=meeting_id,
            title=title,
            created_at=datetime.utcnow().isoformat() + "Z",
            participants=db_meeting.participants,
            transcript=[],
            audio_files=[],
            domain_id=default_domain_id,
        )

        return meeting
    except Exception as e:
        print(f"Error creating meeting: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def update_meeting_settings(meeting_id: str, participants: List[str]) -> Optional[Meeting]:
    """Update meeting settings (participants) in database."""
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None
        
        db_meeting.participants = participants
        db.commit()
        
        db_transcripts = db.query(DBTranscript).filter(DBTranscript.meeting_id == meeting_id).all()
        return db_meeting_to_model(db_meeting, db_transcripts)
    except Exception as e:
        print(f"Error updating meeting settings: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def update_transcript(meeting_id: str, transcript: List[Dict[str, Any]]) -> Optional[Meeting]:
    """Update the transcript of a meeting in database."""
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None
        # Delete existing transcripts for this meeting
        db.query(DBTranscript).filter(DBTranscript.meeting_id == meeting_id).delete()
        
        # Add new transcripts
        for seg in transcript:
            
            db_transcript = DBTranscript(
                meeting_id=meeting_id,
                speaker_index=seg.get('speaker_index'),
                text=seg.get('text'),
                start_time=seg.get('start', 0),
                end_time=seg.get('end', 0)
            )
            db.add(db_transcript)
        
        db.commit()
        
        # Return updated meeting with new transcripts
        db_transcripts = db.query(DBTranscript).filter(DBTranscript.meeting_id == meeting_id).all()
        return db_meeting_to_model(db_meeting, db_transcripts)
    except Exception as e:
        print(f"Error updating transcript: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def add_audio_file(meeting_id: str, audio_file_path: str) -> Optional[Meeting]:
    """Set the audio file for a meeting."""
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None

        db_meeting.audio_file = audio_file_path
        db.commit()

        db_transcripts = db.query(DBTranscript).filter(DBTranscript.meeting_id == meeting_id).all()
        return db_meeting_to_model(db_meeting, db_transcripts)
    except Exception as e:
        print(f"Error adding audio file: {e}")
        return None
    finally:
        db.close()


def clear_audio_file(meeting_id: str) -> Optional[Meeting]:
    """미팅의 audio_file을 비운다 (서버 사본을 더 이상 보관하지 않을 때 사용)."""
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None

        db_meeting.audio_file = None
        db.commit()

        db_transcripts = db.query(DBTranscript).filter(DBTranscript.meeting_id == meeting_id).all()
        return db_meeting_to_model(db_meeting, db_transcripts)
    except Exception as e:
        print(f"Error clearing audio file: {e}")
        return None
    finally:
        db.close()


def update_subject(meeting_id: str, subject: str) -> Optional[Meeting]:
    """Update the subject of a meeting in database."""
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None
        
        db_meeting.subject = subject
        db.commit()
        
        db_transcripts = db.query(DBTranscript).filter(DBTranscript.meeting_id == meeting_id).all()
        return db_meeting_to_model(db_meeting, db_transcripts)
    except Exception as e:
        print(f"Error updating subject: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def update_meeting_title(meeting_id: str, title: str) -> Optional[Meeting]:
    """Update the title of a meeting in database."""
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None
        
        db_meeting.title = title
        db.commit()
        
        db_transcripts = db.query(DBTranscript).filter(DBTranscript.meeting_id == meeting_id).all()
        return db_meeting_to_model(db_meeting, db_transcripts)
    except Exception as e:
        print(f"Error updating meeting title: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def delete_meeting(meeting_id: str) -> bool:
    """Delete a meeting and all related data (transcript, paragraph, next_steps)."""
    from database import Paragraph, NextStep
    
    db = get_db()
    try:
        # Delete all related transcripts
        db.query(DBTranscript).filter(DBTranscript.meeting_id == meeting_id).delete()
        
        # Delete all related paragraphs
        db.query(Paragraph).filter(Paragraph.meeting_id == meeting_id).delete()
        
        # Delete all related next_steps
        db.query(NextStep).filter(NextStep.meeting_id == meeting_id).delete()
        
        # Delete the meeting itself
        db.query(DBMeeting).filter(DBMeeting.id == meeting_id).delete()
        
        db.commit()
        return True
    except Exception as e:
        print(f"Error deleting meeting {meeting_id}: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_domain_keywords(domain_id: int) -> Optional[List[str]]:
    """
    도메인 ID로 해당 도메인의 키워드 목록을 조회합니다.
    키워드는 데이터베이스에 정렬된 상태로 저장됩니다.
    
    Args:
        domain_id: 도메인 ID (DomainKeywords.id, primary key)
        
    Returns:
        정렬된 키워드 리스트, 없으면 None
    """
    db = get_db()
    try:
        domain_keywords = db.query(DBDomainKeywords).filter(
            DBDomainKeywords.id == domain_id
        ).first()
        
        if domain_keywords:
            return list(domain_keywords.keywords)
        return None
    except Exception as e:
        print(f"Error fetching domain keywords for domain_id {domain_id}: {e}")
        return None
    finally:
        db.close()


def update_meeting_domain(meeting_id: str, domain_id: Optional[int]) -> Optional[Meeting]:
    """
    미팅의 도메인 ID를 업데이트합니다.
    
    Args:
        meeting_id: 미팅 ID
        domain_id: 도메인 ID (DomainKeywords.id, None으로 해제 가능)
        
    Returns:
        업데이트된 Meeting 객체
    """
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None
        
        db_meeting.domain_id = domain_id
        db.commit()

        db_transcripts = db.query(DBTranscript).filter(DBTranscript.meeting_id == meeting_id).all()
        return db_meeting_to_model(db_meeting, db_transcripts)
    except Exception as e:
        print(f"Error updating meeting domain: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def set_transcription_status(
    meeting_id: str,
    status: Optional[str],
    source_audio_path: Optional[str] = None,
    set_source: bool = False,
    clear_source: bool = False,
) -> None:
    """STT 변환 상태를 갱신한다.

    - set_source=True: source_audio_path를 함께 기록 (업로드 직후 재처리용 원본 경로)
    - clear_source=True: source_audio_path를 비움 (변환 완료 후)
    """
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return
        db_meeting.transcription_status = status
        if set_source:
            db_meeting.source_audio_path = source_audio_path
        if clear_source:
            db_meeting.source_audio_path = None
        db.commit()
    except Exception as e:
        print(f"Error setting transcription status for {meeting_id}: {e}")
        db.rollback()
    finally:
        db.close()


def get_pending_transcription(meeting_id: str):
    """재처리에 필요한 (source_audio_path, domain_id)를 반환. 없으면 (None, None)."""
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None, None
        return db_meeting.source_audio_path, db_meeting.domain_id
    finally:
        db.close()


def list_stuck_transcription_ids() -> List[str]:
    """status가 'processing'인 채로 멈춘 미팅 id 목록 (서버 재시작 시 재큐잉용)."""
    db = get_db()
    try:
        rows = (
            db.query(DBMeeting.id)
            .filter(DBMeeting.transcription_status == "processing")
            .all()
        )
        return [r[0] for r in rows]
    except Exception as e:
        print(f"Error listing stuck transcriptions: {e}")
        return []
    finally:
        db.close()
