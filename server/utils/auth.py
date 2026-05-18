"""인증 공통 유틸: 비밀번호 해시/검증, JWT 발급/검증, 현재 사용자 의존성.

토큰 전략:
- access token: 짧은 수명(6h). 모바일에서 백그라운드 녹음이 길어져도 만료되지 않도록 일반 웹보다 길게.
- refresh token: 긴 수명(60d). Redis에 jti가 저장된 동안에만 유효. 사용할 때마다 회전(rotate)되어
  슬라이딩 만료처럼 동작하므로, 활성 사용자는 사실상 무기한 유지되고 N일 미사용 시 자연 만료된다.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import User, get_db
from utils import refresh_token_store

load_dotenv()

# 비밀번호 암호화
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT 설정
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 6
REFRESH_TOKEN_EXPIRE_DAYS = 60

_TOKEN_TYPE_ACCESS = "access"
_TOKEN_TYPE_REFRESH = "refresh"

# Bearer 토큰 추출용 보안 스키마 (Swagger 문서에도 노출됨)
_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """비밀번호를 해시합니다."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """비밀번호를 검증합니다."""
    return pwd_context.verify(plain_password, hashed_password)


def _encode(payload: dict) -> str:
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def create_access_token(email: str) -> str:
    """짧은 수명의 access token을 발급한다."""
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return _encode({"sub": email, "exp": expire, "type": _TOKEN_TYPE_ACCESS})


def create_refresh_token(email: str) -> str:
    """긴 수명의 refresh token을 발급하고 Redis에 jti를 등록한다."""
    jti = uuid.uuid4().hex
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    token = _encode({"sub": email, "exp": expire, "type": _TOKEN_TYPE_REFRESH, "jti": jti})
    refresh_token_store.save(jti, email, REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600)
    return token


def verify_access_token(token: str) -> Optional[str]:
    """access token을 검증하고 이메일을 반환한다."""
    payload = _decode(token)
    if not payload or payload.get("type") != _TOKEN_TYPE_ACCESS:
        return None
    return payload.get("sub")


def verify_refresh_token(token: str) -> Optional[Tuple[str, str]]:
    """refresh token을 검증하고 (email, jti)를 반환한다.

    JWT 자체가 유효하더라도 Redis에 jti가 없으면 무효(회전됐거나 강제 로그아웃됨).
    """
    payload = _decode(token)
    if not payload or payload.get("type") != _TOKEN_TYPE_REFRESH:
        return None
    email = payload.get("sub")
    jti = payload.get("jti")
    if not email or not jti:
        return None
    if refresh_token_store.get_email(jti) != email:
        return None
    return email, jti


def rotate_refresh_token(token: str) -> Optional[Tuple[str, str]]:
    """refresh token을 회전한다. (new_access, new_refresh) 또는 무효 시 None.

    이전 jti는 즉시 삭제되어 한 번 사용된 refresh token은 재사용 불가.
    """
    verified = verify_refresh_token(token)
    if verified is None:
        return None
    email, jti = verified
    refresh_token_store.delete(jti)
    return create_access_token(email), create_refresh_token(email)


def revoke_refresh_token(token: str) -> None:
    """refresh token을 무효화한다(로그아웃). JWT 파싱 실패는 조용히 무시."""
    payload = _decode(token)
    if payload:
        jti = payload.get("jti")
        if jti:
            refresh_token_store.delete(jti)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Authorization 헤더의 Bearer access token을 검증해 현재 사용자를 반환합니다."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 토큰이 필요합니다",
        )

    email = verify_access_token(credentials.credentials)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 토큰입니다",
        )

    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다",
        )

    return user
