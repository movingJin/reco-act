"""공용 Redis 클라이언트.

이메일 인증코드, refresh token 등 TTL 기반 키-값 저장이 필요한 곳에서 공통으로 사용한다.
"""
import os
from typing import Optional

import redis
from dotenv import load_dotenv

load_dotenv()

_REDIS_HOST = os.getenv("REDIS_HOST")
_REDIS_PORT = int(os.getenv("REDIS_PORT"))
_REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None

_client: Optional[redis.Redis] = None


def get_client() -> redis.Redis:
    """싱글톤 Redis 클라이언트를 반환한다."""
    global _client
    if _client is None:
        _client = redis.Redis(
            host=_REDIS_HOST,
            port=_REDIS_PORT,
            password=_REDIS_PASSWORD,
            decode_responses=True,
        )
    return _client


def set_with_ttl(key: str, value: str, ttl_seconds: int) -> None:
    get_client().set(key, value, ex=ttl_seconds)


def get(key: str) -> Optional[str]:
    return get_client().get(key)


def delete(key: str) -> None:
    get_client().delete(key)
