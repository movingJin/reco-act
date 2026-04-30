"""Redis 기반 이메일 인증코드 저장소."""
import os
from typing import Optional

import redis
from dotenv import load_dotenv

load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

_client: Optional[redis.Redis] = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
    return _client


def _key(email: str) -> str:
    return f"verification:{email}"


def set_code(email: str, code: str, ttl_seconds: int) -> None:
    """인증코드를 TTL과 함께 저장한다. 기존 코드는 덮어쓴다."""
    _get_client().set(_key(email), code, ex=ttl_seconds)


def get_code(email: str) -> Optional[str]:
    """저장된 인증코드를 반환한다. 없거나 만료되었으면 None."""
    return _get_client().get(_key(email))


def delete_code(email: str) -> None:
    _get_client().delete(_key(email))
