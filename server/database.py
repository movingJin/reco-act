import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, String, DateTime, TIMESTAMP, Integer, Float, Text, ARRAY
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

load_dotenv()

# PostgreSQL 연결 정보
DATABASE_URL = os.getenv('DATABASE_URL')
print(f"Using DATABASE_URL: {DATABASE_URL}")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Meeting(Base):
    __tablename__ = "meetings"
    
    id = Column(String, primary_key=True)
    title = Column(String)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    participants = Column(ARRAY(String))
    audio_file = Column(String, nullable=True)


class Transcript(Base):
    __tablename__ = "transcripts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String)
    speaker_index = Column(Integer)
    text = Column(Text)
    start_time = Column(Float)
    end_time = Column(Float)


def init_db():
    """애플리케이션 시작 시 테이블을 자동으로 생성합니다."""
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
