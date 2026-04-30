"""인증/사용자 관리 비즈니스 로직."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from database import (
    DomainKeywords,
    Meeting,
    NextStep,
    Paragraph,
    Transcript,
    User,
)
from utils import verification_store
from utils.auth import create_access_token, hash_password, verify_password
from utils.email import generate_verification_code, send_verification_email

VERIFICATION_CODE_TTL = timedelta(minutes=10)


# ==================== Helpers ====================

def _validate_email_or_raise(email: str) -> None:
    try:
        validate_email(email)
    except EmailNotValidError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="유효하지 않은 이메일입니다",
        )


def _verify_code(email: str, code: str) -> None:
    """Redis에 저장된 인증코드와 일치하는지 확인한다. (TTL이 만료를 자동 처리)"""
    stored_code = verification_store.get_code(email)

    if stored_code is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="인증코드가 만료되었거나 존재하지 않습니다",
        )

    if stored_code != code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="인증코드가 일치하지 않습니다",
        )


def _issue_and_send_code(email: str) -> None:
    """새 인증코드를 Redis에 저장(TTL 적용)하고 이메일로 전송한다."""
    code = generate_verification_code()
    verification_store.set_code(email, code, int(VERIFICATION_CODE_TTL.total_seconds()))

    if not send_verification_email(email, code):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="이메일 전송에 실패했습니다",
        )


# ==================== Email / Verification ====================

def check_email_available(email: str, db: Session) -> None:
    """이메일이 가입 가능한지(중복 아님) 확인한다."""
    _validate_email_or_raise(email)

    if db.query(User).filter(User.email == email).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미 가입된 이메일입니다",
        )


def send_signup_verification_code(email: str) -> None:
    """회원가입 전 인증코드를 발급하고 이메일로 전송한다."""
    _validate_email_or_raise(email)
    _issue_and_send_code(email)


def verify_code(email: str, code: str) -> None:
    """이메일+인증코드 유효성만 확인한다 (소비/삭제 없음)."""
    _verify_code(email, code)


# ==================== Signup / Login ====================

def signup(email: str, name: str, password: str, code: str, db: Session) -> User:
    """인증코드 검증 후 사용자를 생성한다."""
    _validate_email_or_raise(email)

    if db.query(User).filter(User.email == email).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미 가입된 이메일입니다",
        )

    _verify_code(email, code)

    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    verification_store.delete_code(email)

    return user


def login(email: str, password: str, db: Session) -> Tuple[User, str]:
    """자격증명을 검증하고 (사용자, access_token)을 반환한다."""
    user = db.query(User).filter(User.email == email).first()

    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 잘못되었습니다",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화된 계정입니다",
        )

    access_token = create_access_token(user.email)
    return user, access_token


# ==================== Profile ====================

def update_user_profile(
    user: User,
    db: Session,
    name: Optional[str] = None,
    domain_id: Optional[int] = None,
    update_domain: bool = False,
) -> User:
    """사용자 프로필(이름, 기본 도메인)을 업데이트한다.

    domain_id를 None으로 명시 변경하려면 update_domain=True로 전달한다.
    """
    if name is not None:
        user.name = name

    if update_domain:
        if domain_id is not None:
            exists = db.query(DomainKeywords).filter(DomainKeywords.id == domain_id).first()
            if not exists:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="존재하지 않는 도메인입니다",
                )
        user.domain_id = domain_id

    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


# ==================== Password Reset ====================

def request_password_reset(email: str, db: Session) -> None:
    """가입된 사용자에게 비밀번호 재설정용 인증코드를 발송한다."""
    user = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="가입되지 않은 이메일입니다",
        )

    _issue_and_send_code(email)


def confirm_password_reset(
    email: str,
    code: str,
    new_password: str,
    password_confirm: str,
    db: Session,
) -> None:
    """인증코드 검증 후 새 비밀번호로 변경한다."""
    if new_password != password_confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="비밀번호가 일치하지 않습니다",
        )

    user = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="가입되지 않은 이메일입니다",
        )

    _verify_code(email, code)

    user.password_hash = hash_password(new_password)
    user.updated_at = datetime.now(timezone.utc)
    db.commit()

    verification_store.delete_code(email)


# ==================== Account Deletion ====================

def delete_user_account(user: User, password: str, db: Session) -> None:
    """비밀번호 재확인 후 사용자와 모든 관련 데이터(미팅/오디오 포함)를 영구 삭제한다."""
    if not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="비밀번호가 잘못되었습니다",
        )

    user_email = user.email

    meetings = db.query(Meeting).filter(Meeting.user_id == user_email).all()
    meeting_ids = [m.id for m in meetings]
    audio_file_paths = [m.audio_file for m in meetings if m.audio_file]

    if meeting_ids:
        db.query(Transcript).filter(Transcript.meeting_id.in_(meeting_ids)).delete(synchronize_session=False)
        db.query(Paragraph).filter(Paragraph.meeting_id.in_(meeting_ids)).delete(synchronize_session=False)
        db.query(NextStep).filter(NextStep.meeting_id.in_(meeting_ids)).delete(synchronize_session=False)
        db.query(Meeting).filter(Meeting.user_id == user_email).delete(synchronize_session=False)

    # Meeting의 FK 제약 때문에 미팅 삭제 후 사용자 삭제
    db.delete(user)
    db.commit()

    verification_store.delete_code(user_email)

    # DB 커밋 성공 후 디스크의 오디오 파일 정리 (실패해도 계정 삭제는 유효)
    for path_str in audio_file_paths:
        try:
            file_path = Path(path_str)
            if file_path.exists():
                file_path.unlink()
        except Exception as e:
            print(f"Error deleting audio file {path_str}: {e}")
