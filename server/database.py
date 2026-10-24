import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, String, DateTime, TIMESTAMP, Integer, Float, Text, ARRAY, ForeignKey, Boolean, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

load_dotenv()

# PostgreSQL 연결 정보
DATABASE_URL = os.getenv('DATABASE_URL')  # 비밀번호가 들어 있으므로 로그에 출력하지 않는다

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


class ProcessingJob(Base):
    """처리 작업 기록(01-plan 3.5). 두 프로파일이 같이 쓴다.

    airgap 워커가 이 테이블을 큐로 쓴다(SELECT ... FOR UPDATE SKIP LOCKED). 새 테이블이라 create_all로 생기며 수동 ALTER가 필요 없다.
    kind: transcribe | summarize | regenerate_notes / status: queued | running | done | failed
    """
    __tablename__ = "processing_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String, nullable=False)
    stage = Column(String, nullable=True)  # 실행 중인 단계(normalize, stt, ...). 상태 표시와 장애 분석용
    status = Column(String, nullable=False, default="queued")
    priority = Column(Integer, nullable=False, default=0)  # 클수록 먼저 꺼낸다
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    error = Column(Text, nullable=True)
    params = Column(JSONB, nullable=True)
    created_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)
    started_at = Column(TIMESTAMP, nullable=True)
    heartbeat_at = Column(TIMESTAMP, nullable=True)
    finished_at = Column(TIMESTAMP, nullable=True)
    locked_by = Column(String, nullable=True)  # 작업을 잡은 워커(호스트:pid)

    # 큐에서 다음 작업을 꺼내는 조회(status='queued', 우선순위·등록순)
    __table_args__ = (Index("ix_processing_jobs_queue", "status", "priority", "id"),)


class TermCorrection(Base):
    """용어 사전: 오인식 표현(wrong) → 정식 표기(correct), 도메인별 (01-plan 3.4).

    airgap 전사의 사전 치환 단계가 쓴다. 새 테이블이라 create_all로 생기며 수동 ALTER가 필요 없다.
    처음에는 scripts/import_term_corrections.py(CSV)로 채운다. 도메인을 지우면 함께 지워진다.
    """
    __tablename__ = "term_corrections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain_id = Column(Integer, ForeignKey("domain_keywords.id", ondelete="CASCADE"), nullable=False, index=True)
    wrong = Column(String, nullable=False)
    correct = Column(String, nullable=False)
    created_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("domain_id", "wrong", name="uq_term_corrections_domain_wrong"),)


class SttResultRecord(Base):
    """airgap 전사의 원문 보존 (01-plan 3.4, 00-decisions 2.4).

    transcripts에는 보정한 세그먼트를 저장하고, 보정 전 원문과 엔진 출력은 여기에 남긴다. 전사가 끝날 때마다 한 행씩 쌓인다.
    public(Clova)은 저장하지 않는다. 새 테이블이라 create_all로 생기며, 회의를 지우면 함께 지워진다.
    """
    __tablename__ = "stt_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String, ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(Integer, nullable=True)  # 이 전사를 만든 processing_jobs.id(작업 기록은 따로 지워질 수 있어 FK는 두지 않음)
    engine = Column(String, nullable=False)
    engine_version = Column(String, nullable=True)
    words = Column(JSONB, nullable=True)  # STT 단어 [{start_ms, end_ms, text, prob}]
    diarization = Column(JSONB, nullable=True)  # 화자분리 구간 [{start_ms, end_ms, speaker}]
    raw_segments = Column(JSONB, nullable=True)  # 병합 직후, 보정 전 세그먼트
    corrected_segments = Column(JSONB, nullable=True)  # 사전 치환·LLM 보정 후 세그먼트(transcripts에 저장한 값)
    engine_raw = Column(JSONB, nullable=True)  # 엔진 부가 정보(언어, 길이, hotwords 등)
    created_at = Column(TIMESTAMP, nullable=False, default=datetime.utcnow)


def init_db():
    """애플리케이션 시작 시 테이블을 자동으로 생성합니다."""
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
