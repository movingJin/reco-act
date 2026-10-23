"""pyannote speaker-diarization-community-1 화자분리 (airgap, 01-plan 3.3). exclusive 출력(구간이 겹치지 않음)을 쓴다.

- 파이프라인은 PYANNOTE_PIPELINE_PATH 로컬 디렉터리(config.yaml + segmentation/embedding/plda)에서만 읽는다.
  저장소 id를 넘기면 HF Hub에서 받으므로 디렉터리와 필수 파일을 먼저 확인한다. 화자 수는 파이프라인이 추정한다.
- pyannote.audio 4.x는 기본으로 사용 정보(길이, 화자 수 등)를 otel.pyannote.ai로 보낸다. 부를 때마다
  PYANNOTE_METRICS_ENABLED=0으로 끈다(외부 호출 0, 00-decisions 3장. 2026-10-20 확인, 01-plan 9.3).
- 음성은 파일 경로 대신 메모리 파형으로 넘긴다. 파일 경로는 torchcodec(FFmpeg 공유 라이브러리)으로 읽는데,
  입력이 이미 정규화한 16kHz mono PCM WAV(pipeline/audio.py)라 wave로 읽으면 충분하다.
- 파이프라인은 프로세스에 한 번 올려 두고 작업마다 다시 쓴다. unload()가 내린다.
"""
import gc
import os
import sys
import threading
import warnings
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from diarization.base import Turn
from services.job_service import RetryableError
from utils import config

REQUIRED_PIPELINE_FILES = (
    "config.yaml",
    "segmentation/pytorch_model.bin",
    "embedding/pytorch_model.bin",
    "plda/plda.npz",
    "plda/xvec_transform.npz",
)

_lock = threading.Lock()
_loaded: Optional[tuple] = None  # (설정 키, 파이프라인)


class PyannotePipelineNotFound(Exception):
    """PYANNOTE_PIPELINE_PATH에 파이프라인 디렉터리나 필수 파일이 없다. 다시 시도해도 같다."""


def _disable_telemetry() -> None:
    # pyannote는 이 값을 기록할 때마다 다시 읽는다. import 전에 없으면 패키지 기본값(true)을 넣으므로 매번 덮어쓴다.
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"


def _create_pipeline(path: str, device: str):
    """pyannote.audio를 import해 파이프라인을 만든다. public 이미지에는 이 패키지가 없어서 여기서만 import한다."""
    _disable_telemetry()
    import torch
    with warnings.catch_warnings():
        # 파일 디코딩(torchcodec)을 쓰지 않으므로 FFmpeg 라이브러리를 못 찾는다는 import 경고는 무시한다.
        warnings.filterwarnings("ignore", message=r"\s*torchcodec is not installed correctly")
        from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(path)
    if pipeline is None:
        raise PyannotePipelineNotFound(f"pyannote could not load the pipeline at {path}")
    return pipeline.to(torch.device(device))


def _get_pipeline():
    global _loaded
    key = (config.PYANNOTE_PIPELINE_PATH, config.STT_DEVICE)
    with _lock:
        if _loaded is None or _loaded[0] != key:
            path = Path(key[0])
            missing = [name for name in REQUIRED_PIPELINE_FILES if not (path / name).is_file()]
            if not path.is_dir() or missing:
                raise PyannotePipelineNotFound(
                    f"PYANNOTE_PIPELINE_PATH={key[0]} is not a local pipeline directory (missing: {', '.join(missing)})"
                )
            _loaded = None  # 설정이 바뀌었으면 이전 파이프라인을 먼저 놓는다
            _loaded = (key, _create_pipeline(*key))
        return _loaded[1]


def _unload_pipeline() -> None:
    global _loaded
    with _lock:
        _loaded = None
    gc.collect()
    torch = sys.modules.get("torch")  # 올린 적이 없으면 import하지 않는다
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


def _load_audio(wav_path: str) -> dict:
    """16bit PCM WAV를 pyannote 입력({"waveform": (channel, time) float32 tensor, "sample_rate"})으로 읽는다."""
    import torch

    with wave.open(wav_path, "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError(f"expected 16-bit PCM WAV, got {wav.getsampwidth() * 8}-bit: {wav_path}")
        channels, sample_rate = wav.getnchannels(), wav.getframerate()
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    samples = (pcm.astype(np.float32) / 32768.0).reshape(-1, channels).T
    return {"waveform": torch.from_numpy(np.ascontiguousarray(samples)), "sample_rate": sample_rate}


def _ms(seconds: float) -> int:
    return int(round(float(seconds) * 1000))


class PyannoteDiarizer:
    name = "pyannote"

    def diarize(self, wav_path: str) -> list[Turn]:
        pipeline = _get_pipeline()
        audio = _load_audio(wav_path)
        _disable_telemetry()
        try:
            output = pipeline(audio)
        except Exception as e:
            # torch.cuda.OutOfMemoryError 등. 파이프라인을 내리고 다시 시도하게 한다.
            if type(e).__name__ == "OutOfMemoryError" or "out of memory" in str(e).lower():
                self.unload()
                raise RetryableError(f"pyannote out of memory: {e}") from e
            raise
        return [
            Turn(_ms(segment.start), _ms(segment.end), speaker)
            for segment, _, speaker in output.exclusive_speaker_diarization.itertracks(yield_label=True)
        ]

    def unload(self) -> None:
        _unload_pipeline()
