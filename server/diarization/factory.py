"""DIARIZER에 맞는 화자분리를 만든다 (01-plan 3.3)."""
from typing import Optional

from diarization.base import Diarizer
from utils import config


def get_diarizer() -> Optional[Diarizer]:
    """설정이 없으면(public 기본) None. 엔진 모듈은 고를 때만 import한다(torch 등 무거운 의존성)."""
    name = config.DIARIZER
    if name is None:
        return None
    if name == "mock":
        from diarization.mock import MockDiarizer
        return MockDiarizer()
    # pyannote는 PR-8에서 추가한다.
    raise NotImplementedError(f"DIARIZER={name} is not implemented yet")
