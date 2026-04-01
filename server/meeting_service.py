from datetime import datetime
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from database import SessionLocal, Meeting as DBMeeting, Transcript as DBTranscript
from models import Meeting, TranscriptSegmentResponse

UPLOADS_DIR = Path(__file__).parent / "records"

# Ensure directories exist
UPLOADS_DIR.mkdir(exist_ok=True)


def get_db():
    """Get database session."""
    return SessionLocal()


def db_meeting_to_model(db_meeting: DBMeeting, db_transcripts: List[DBTranscript]) -> Meeting:
    """Convert database models to Pydantic Meeting model."""
    participants = list(db_meeting.participants) if db_meeting.participants else []
    
    transcript_segments = []
    for t in db_transcripts:
        # Get speaker name from participants using speaker_index
        speaker_index = t.speaker_index
        speaker_name = participants[speaker_index]
        
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
        audio_files=audio_files
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


def list_all_meetings() -> List[Meeting]:
    """List all meetings from database."""
    db = get_db()
    meetings = []
    try:
        db_meetings = db.query(DBMeeting).order_by(DBMeeting.created_at.desc()).all()
        
        for db_meeting in db_meetings:
            db_transcripts = db.query(DBTranscript).filter(DBTranscript.meeting_id == db_meeting.id).all()
            meetings.append(db_meeting_to_model(db_meeting, db_transcripts))
    except Exception as e:
        print(f"Error listing meetings: {e}")
    finally:
        db.close()
    
    return meetings


def create_meeting(title: str, participants: Optional[List[str]] = None) -> Meeting:
    """Create a new meeting in database."""
    db = get_db()
    try:
        meeting_id = f"m_{int(datetime.now().timestamp() * 1000)}"
        
        db_meeting = DBMeeting(
            id=meeting_id,
            title=title,
            created_at=datetime.utcnow(),
            participants=participants or [f"화자{i+1}" for i in range(2)]
        )
        
        db.add(db_meeting)
        db.commit()
        
        meeting = Meeting(
            id=meeting_id,
            title=title,
            created_at=datetime.utcnow().isoformat() + "Z",
            participants=db_meeting.participants,
            transcript=[],
            audio_files=[]
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
                start_time=seg.get('start', 0.0),
                end_time=seg.get('end', 0.0)
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
