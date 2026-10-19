"""STT_PROVIDER에 맞는 프로바이더를 만든다 (01-plan 3.2)."""
from stt.base import SttProvider
from utils import config


def get_stt_provider() -> SttProvider:
    """엔진 모듈은 고를 때만 import한다. airgap에서는 Clova 클라이언트를 불러오지 않는다(01-plan 3.8)."""
    name = config.STT_PROVIDER
    if name == "clova":
        from stt.clova import ClovaSttProvider
        return ClovaSttProvider()
    if name == "faster_whisper":
        from stt.faster_whisper import FasterWhisperSttProvider
        return FasterWhisperSttProvider()
    if name == "mock":
        from stt.mock import MockSttProvider
        return MockSttProvider()
    # qwen3_asr는 PR-17에서 추가한다.
    raise NotImplementedError(f"STT_PROVIDER={name} is not implemented yet")
