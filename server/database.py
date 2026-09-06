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
    # 회의록 양식에 맞춰 생성된 회의록 본문(편집 가능). 요약(subject/paragraphs)과 별개로 보관한다.
    meeting_notes = Column(Text, nullable=True)
    domain_id = Column(Integer, ForeignKey("domain_keywords.id", ondelete="SET NULL"), nullable=True)
    # STT 변환 상태: 'processing' | 'done' | 'failed' | NULL(미처리)
    # 업로드는 즉시 응답하고 STT는 백그라운드로 돌리므로, 진행 상태를 여기에 기록해
    # 프론트가 폴링으로 조회한다.
    transcription_status = Column(String, nullable=True)
    # 변환 대기 중인 원본 업로드 파일 경로. 백그라운드 STT 도중 서버가 재시작돼도
    # 기동 시 이 경로로 재처리(re-queue)할 수 있게 보관하고, 완료 후 비운다.
    source_audio_path = Column(String, nullable=True)
    # 녹음 파일의 실제 길이(밀리초). STT 변환 시 pydub으로 오디오를 로드하는
    # 김에 함께 구해서 저장한다(회의 화면에 "녹음 길이"로 표시).
    duration_ms = Column(Integer, nullable=True)

    user = relationship("User", back_populates="meetings")


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    speaker_index = Column(Integer)
    text = Column(Text)
    start_time = Column(Integer)  # milliseconds
    end_time = Column(Integer)    # milliseconds


class Paragraph(Base):
    __tablename__ = "paragraphs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    subject = Column(Text)
    start = Column(Integer)
    end = Column(Integer)
    summary = Column(Text)


class NextStep(Base):
    __tablename__ = "next_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    todo = Column(Text)
    order_index = Column(Integer)


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
