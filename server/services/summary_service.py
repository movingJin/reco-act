from typing import List, Dict, Any, Optional
from database import SessionLocal, Meeting as DBMeeting, Paragraph as DBParagraph, NextStep as DBNextStep
from graph.summary_workflow import SummaryNode
from models.summary import SummaryResponse, Paragraph


def get_db():
    """Get database session."""
    return SessionLocal()


def db_summary_to_model(db_meeting: DBMeeting, db_paragraphs: List[DBParagraph], db_next_steps: List[DBNextStep]) -> SummaryResponse:
    """Convert database models to Pydantic SummaryResponse model."""
    paragraphs = [
        Paragraph(
            id=p.id,
            subject=p.subject,
            summary=p.summary,
            start=p.start,
            end=p.end
        )
        for p in db_paragraphs
    ]
    
    next_steps = [step.todo for step in db_next_steps]
    
    return SummaryResponse(
        meeting_id=db_meeting.id,
        paragraphs=paragraphs,
        next_steps=next_steps,
        subject=db_meeting.subject
    )


def load_summary(meeting_id: str) -> Optional[SummaryResponse]:
    """Load a summary from database."""
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None
        
        db_paragraphs = db.query(DBParagraph).filter(DBParagraph.meeting_id == meeting_id).all()
        db_next_steps = db.query(DBNextStep).filter(DBNextStep.meeting_id == meeting_id).all()
        return db_summary_to_model(db_meeting, db_paragraphs, db_next_steps)
    except Exception as e:
        print(f"Error loading meeting {meeting_id}: {e}")
        return None
    finally:
        db.close()


def create_summary(meeting_id: str) -> Optional[SummaryResponse]:
    """Create a new summary in database."""
    db = get_db()
    try:
        summary_node = SummaryNode()
        summaries = summary_node.run(meeting_id)
        
        # Delete existing paragraphs and next steps for this meeting
        db.query(DBParagraph).filter(DBParagraph.meeting_id == meeting_id).delete()
        db.query(DBNextStep).filter(DBNextStep.meeting_id == meeting_id).delete()
        
        # Add paragraphs to database
        db_paragraphs_list = []
        for p in summaries.paragraphs:
            db_paragraph = DBParagraph(
                meeting_id=meeting_id,
                subject=p["subject"],
                start=p["start"],
                end=p["end"],
                summary=p["summary"]
            )
            db.add(db_paragraph)
            db_paragraphs_list.append(db_paragraph)
        
        # Add next steps to database
        for step in summaries.next_steps:
            db.add(DBNextStep(
                meeting_id=meeting_id,
                todo=step
            ))
        
        # Update meeting subject
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        db_meeting.subject = summaries.subject
        db.commit()
        
        # Create paragraphs with ID
        paragraphs = [
            Paragraph(
                id=p.id,
                subject=p.subject,
                start=p.start,
                end=p.end,
                summary=p.summary
            )
            for p in db_paragraphs_list
        ]
        
        return SummaryResponse(
            meeting_id=meeting_id,
            paragraphs=paragraphs,
            next_steps=summaries.next_steps,
            subject=summaries.subject
        )
    except Exception as e:
        print(f"Error creating meeting: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def update_paragraph(meeting_id: str, paragraph_id: int, paragraph_data: Dict[str, Any]) -> Optional[SummaryResponse]:
    """Update a specific paragraph of a meeting in database."""
    db = get_db()
    try:
        # Get the paragraph to update
        db_paragraph = db.query(DBParagraph).filter(
            DBParagraph.id == paragraph_id,
            DBParagraph.meeting_id == meeting_id
        ).first()
        
        if not db_paragraph:
            return None
        
        # Update paragraph fields
        if "subject" in paragraph_data:
            db_paragraph.subject = paragraph_data["subject"]
        if "summary" in paragraph_data:
            db_paragraph.summary = paragraph_data["summary"]
        if "start" in paragraph_data:
            db_paragraph.start = paragraph_data["start"]
        if "end" in paragraph_data:
            db_paragraph.end = paragraph_data["end"]
        
        db.commit()
        
        # Return updated summary
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        db_paragraphs = db.query(DBParagraph).filter(DBParagraph.meeting_id == meeting_id).all()
        db_next_steps = db.query(DBNextStep).filter(DBNextStep.meeting_id == meeting_id).all()
        
        return db_summary_to_model(db_meeting, db_paragraphs, db_next_steps)
    except Exception as e:
        print(f"Error updating paragraph: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def update_next_steps(meeting_id: str, next_steps: List[str]) -> Optional[SummaryResponse]:
    """Update the next steps of a meeting in database."""
    db = get_db()
    try:
        db_meeting = db.query(DBMeeting).filter(DBMeeting.id == meeting_id).first()
        if not db_meeting:
            return None
        
        # Delete existing next steps for this meeting
        db.query(DBNextStep).filter(DBNextStep.meeting_id == meeting_id).delete()
        
        # Add new next steps
        for step in next_steps:
            db.add(DBNextStep(
                meeting_id=meeting_id,
                todo=step
            ))
        
        db.commit()
        
        # Return updated summary
        db_paragraphs = db.query(DBParagraph).filter(DBParagraph.meeting_id == meeting_id).all()
        db_next_steps_updated = db.query(DBNextStep).filter(DBNextStep.meeting_id == meeting_id).all()
        
        return db_summary_to_model(db_meeting, db_paragraphs, db_next_steps_updated)
    except Exception as e:
        print(f"Error updating next steps: {e}")
        db.rollback()
        return None
    finally:
        db.close()
