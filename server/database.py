import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, String, DateTime, TIMESTAMP, Integer, Float, Text, ARRAY, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

load_dotenv()

# PostgreSQL 연결 정보
DATABASE_URL = os.getenv('DATABASE_URL')
print(f"Using DATABASE_URL: {DATABASE_URL}")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    email = Column(String, primary_key=True, unique=True, index=True)
    name = Column(String)
    password_hash = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)
    domain_id = Column(Integer, ForeignKey("domain_keywords.id", ondelete="SET NULL"), nullable=True)

    meetings = relationship("Meeting", back_populates="user")


class Meeting(Base):
    __tablename__ = "meetings"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.email"), nullable=False)
    title = Column(String)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    participants = Column(ARRAY(String))
    audio_file = Column(String, nullable=True)
    subject = Column(Text, nullable=True)
    domain_id = Column(Integer, ForeignKey("domain_keywords.id", ondelete="SET NULL"), nullable=True)

    user = relationship("User", back_populates="meetings")


class Transcript(Base):
    __tablename__ = "transcripts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String)
    speaker_index = Column(Integer)
    text = Column(Text)
    start_time = Column(Integer)  # milliseconds
    end_time = Column(Integer)    # milliseconds


class Paragraph(Base):
    __tablename__ = "paragraphs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    subject = Column(String)
    start = Column(Integer)
    end = Column(Integer)
    summary = Column(Text)


class NextStep(Base):
    __tablename__ = "next_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    todo = Column(Text)


class DomainKeywords(Base):
    __tablename__ = "domain_keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain_name = Column(String)
    keywords = Column(ARRAY(String))


def init_db():
    """애플리케이션 시작 시 테이블을 자동으로 생성합니다."""
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
