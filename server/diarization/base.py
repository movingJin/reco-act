"""화자분리 인터페이스 (01-plan 3.3).

단어 타임스탬프만 주는 STT 엔진(faster_whisper, qwen3_asr, mock)의 결과에 화자를 붙이는 데 쓴다.
구간과 단어를 합치는 일은 pipeline/merge.py가 맡는다. Clova는 화자까지 주므로 public에서는 쓰지 않는다.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Turn:
    """한 화자가 말한 구간. speaker는 엔진이 붙인 라벨(예: SPEAKER_00)이고, 화면에 내는 이름은 병합 단계가 정한다."""
    start_ms: int
    end_ms: int
    speaker: str


class Diarizer(Protocol):
    name: str

    def diarize(self, wav_path: str) -> list[Turn]:
        """wav_path는 정규화한 mono 16kHz WAV다. 화자 수는 엔진이 추정한다.

        구간이 겹치지 않는 출력(pyannote exclusive)을 기대하지만, 병합은 구간이 겹쳐도 동작한다.
        """
        ...

    def unload(self) -> None:
        """모델이 잡은 메모리(GPU 포함)를 돌려준다(exclusive 모드, 01-plan 3.6). 모델이 없으면 아무것도 하지 않는다."""
        ...
