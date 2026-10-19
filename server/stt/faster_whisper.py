"""faster-whisper 어댑터 (airgap, 01-plan 3.2). 단어 타임스탬프만 주고, 화자는 화자분리·병합 단계가 붙인다.

- 모델(CTranslate2 변환본)은 WHISPER_MODEL_PATH 로컬 디렉터리에서만 읽는다. faster-whisper는 디렉터리가 아니면 HF Hub에서
  내려받고, tokenizer.json이 없으면 토크나이저를 HF Hub에서 받으므로 디렉터리와 필수 파일을 먼저 확인한다.
- VAD(silero)는 faster-whisper 패키지에 든 onnx 파일을 쓴다(런타임 다운로드 없음).
- 키워드는 hotwords로 넘긴다. initial_prompt는 첫 구간에만 들어가지만 hotwords는 30초 구간마다 프롬프트에 들어간다.
  키워드만 쉼표로 늘어놓으면 모델이 이를 앞선 발화로 여겨, 키워드로 끝나는 문장을 건너뛰거나 문장부호를 빼고 받아쓴다.
  그래서 "용어: A, B." 형태의 문장으로 넘긴다(2026-10-19 확인, 01-plan 9.2).
- 이전 구간 문맥(condition_on_previous_text)은 끈다. hotwords와 함께 켜 두면 프롬프트가 길어져 출력이 비는 구간이 생긴다
  (2026-10-19 확인, 01-plan 9.2).
- 모델은 프로세스에 한 번 올려 두고 작업마다 다시 쓴다. unload()가 내린다.
"""
import gc
import threading
from pathlib import Path
from typing import Callable, Optional

from services.job_service import RetryableError
from stt.base import SttHints, SttResult, Word
from utils import config

# faster-whisper가 hotwords를 자르는 길이(max_length // 2 - 1). 넘치면 토큰 중간에서 잘리므로 키워드 단위로 미리 자른다.
HOTWORDS_MAX_TOKENS = 223
HOTWORDS_PREFIX = "용어: "
# tokenizer.json이 없으면 faster-whisper가 HF Hub에서 토크나이저를 받는다.
REQUIRED_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")

_lock = threading.Lock()
_loaded: Optional[tuple] = None  # (설정 키, 모델, faster-whisper 버전)


class WhisperModelNotFound(Exception):
    """WHISPER_MODEL_PATH에 모델 디렉터리나 필수 파일이 없다. 다시 시도해도 같다."""


def _create_model(path: str, device: str, compute_type: str):
    """faster-whisper를 import해 모델을 만든다. public 이미지에는 이 패키지가 없어서 여기서만 import한다."""
    from faster_whisper import WhisperModel, __version__

    return WhisperModel(path, device=device, compute_type=compute_type, local_files_only=True), __version__


def _get_model() -> tuple:
    global _loaded
    key = (config.WHISPER_MODEL_PATH, config.STT_DEVICE, config.STT_COMPUTE_TYPE)
    with _lock:
        if _loaded is None or _loaded[0] != key:
            path = Path(key[0])
            missing = [name for name in REQUIRED_MODEL_FILES if not (path / name).is_file()]
            if not path.is_dir() or missing:
                raise WhisperModelNotFound(
                    f"WHISPER_MODEL_PATH={key[0]} is not a local CTranslate2 model directory (missing: {', '.join(missing)})"
                )
            _loaded = None  # 설정이 바뀌었으면 이전 모델을 먼저 놓는다
            _loaded = (key, *_create_model(*key))
        return _loaded[1], _loaded[2]


def _unload_model() -> None:
    global _loaded
    with _lock:
        _loaded = None
    gc.collect()  # CTranslate2 모델은 객체가 사라질 때 메모리(GPU 포함)를 돌려준다


def to_whisper_language(language: str) -> str:
    """Clova 형식(ko-KR)을 Whisper 언어 코드(ko)로 바꾼다."""
    return language.split("-")[0].lower()


def build_hotwords(keywords: list[str], count_tokens: Callable[[str], int], max_tokens: int = HOTWORDS_MAX_TOKENS) -> Optional[str]:
    """키워드를 "용어: A, B." 문장으로 만들되 max_tokens 안에 드는 키워드까지만 넣는다.

    앞에서부터 넣고 넘치는 키워드는 사전 치환 단계(01-plan 3.4)에 맡긴다. domain_keywords는 가나다순으로 저장되므로 앞쪽이 남는다.
    count_tokens는 faster-whisper가 hotwords를 인코딩하는 방식(앞에 공백 하나)으로 센다. 넣을 키워드가 없으면 None.
    """
    included: list[str] = []
    for keyword in keywords:
        keyword = keyword.strip()
        if not keyword:
            continue
        if count_tokens(" " + _hotwords_sentence(included + [keyword])) > max_tokens:
            break
        included.append(keyword)
    return _hotwords_sentence(included) if included else None


def _hotwords_sentence(keywords: list[str]) -> str:
    return HOTWORDS_PREFIX + ", ".join(keywords) + "."


def _ms(seconds: float) -> int:
    return int(round(float(seconds) * 1000))


class FasterWhisperSttProvider:
    name = "faster_whisper"

    def transcribe(self, wav_path: str, *, language: str, hints: SttHints) -> SttResult:
        model, version = _get_model()
        hotwords = build_hotwords(
            hints.keywords, lambda text: len(model.hf_tokenizer.encode(text, add_special_tokens=False).ids)
        )
        try:
            segments, info = model.transcribe(
                wav_path,
                language=to_whisper_language(language),
                vad_filter=True,
                word_timestamps=True,
                hotwords=hotwords,
                condition_on_previous_text=False,
            )
            words, raw_segments = [], []
            for segment in segments:  # 생성기라서 여기서 실제로 디코딩한다
                raw_segments.append({"start_ms": _ms(segment.start), "end_ms": _ms(segment.end), "text": segment.text})
                for word in segment.words or []:
                    words.append(Word(_ms(word.start), _ms(word.end), word.word, float(word.probability)))
        except RuntimeError as e:
            # CTranslate2의 CUDA OOM은 RuntimeError("... out of memory")로 온다. 모델을 내리고 다시 시도하게 한다.
            if "out of memory" in str(e).lower():
                self.unload()
                raise RetryableError(f"faster-whisper out of memory: {e}") from e
            raise

        return SttResult(
            engine=self.name,
            engine_version=f"faster-whisper {version} ({Path(config.WHISPER_MODEL_PATH).name}, {config.STT_COMPUTE_TYPE})",
            words=words,
            raw={
                "language": info.language,
                "duration_ms": _ms(info.duration),
                "duration_after_vad_ms": _ms(info.duration_after_vad),
                "hotwords": hotwords,
                "segments": raw_segments,
            },
        )

    def unload(self) -> None:
        _unload_model()
