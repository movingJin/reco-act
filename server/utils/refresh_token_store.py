"""Redis 기반 refresh token 저장소.

refresh token의 jti(고유 ID)를 키로 사용해 회전/단일 무효화/전체 무효화를 지원한다.
- 슬라이딩 만료: 토큰 회전 시 새 jti가 새 TTL로 다시 저장되므로 활성 사용자는 무기한 유지된다.
- 단일 기기 로그아웃: delete(jti)
- 전체 기기 로그아웃(비밀번호 변경 등): delete_all_for_user(email)
"""
from typing import Optional

from utils import redis_client

_KEY_PREFIX = "refresh"


def _key(jti: str) -> str:
    return f"{_KEY_PREFIX}:{jti}"


def save(jti: str, email: str, ttl_seconds: int) -> None:
    """refresh token의 소유 email을 TTL과 함께 저장한다."""
    redis_client.set_with_ttl(_key(jti), email, ttl_seconds)


def get_email(jti: str) -> Optional[str]:
    """jti에 매핑된 email을 반환한다. 회전됐거나 만료되었으면 None."""
    return redis_client.get(_key(jti))


def delete(jti: str) -> None:
    """단일 refresh token을 무효화한다(로그아웃)."""
    redis_client.delete(_key(jti))


def delete_all_for_user(email: str) -> int:
    """해당 사용자의 모든 refresh token을 무효화한다. 삭제 개수를 반환."""
    client = redis_client.get_client()
    deleted = 0
    for key in client.scan_iter(match=f"{_KEY_PREFIX}:*", count=500):
        if client.get(key) == email:
            client.delete(key)
            deleted += 1
    return deleted
