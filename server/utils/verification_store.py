"""Redis 기반 이메일 인증코드 저장소."""
from typing import Optional

from utils import redis_client


def _key(email: str) -> str:
    return f"verification:{email}"


def set_code(email: str, code: str, ttl_seconds: int) -> None:
    """인증코드를 TTL과 함께 저장한다. 기존 코드는 덮어쓴다."""
    redis_client.set_with_ttl(_key(email), code, ttl_seconds)


def get_code(email: str) -> Optional[str]:
    """저장된 인증코드를 반환한다. 없거나 만료되었으면 None."""
    return redis_client.get(_key(email))


def delete_code(email: str) -> None:
    redis_client.delete(_key(email))
