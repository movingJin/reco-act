"""faster-whisper 어댑터 (01-plan 3.2, PR-7).

단위 테스트는 가짜 모델을 넣어 faster-whisper 없이 돈다(public 테스트 환경에는 requirements-airgap.txt가 없다).
-m slow 스모크는 실제 faster-whisper로 CPU int8 전사를 한다. 셸 env WHISPER_TEST_MODEL_PATH(CTranslate2 변환본 로컬 디렉터리,
예: Systran/faster-whisper-tiny)가 있어야 돌고, 음성은 ffmpeg flite로 만든 영어 합성 문장만 쓴다. 네트워크 연결은 막고 돌린다.
"""
import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.job_service import RetryableError, is_retryable
from stt import faster_whisper as fw
from stt.base import SttHints
from stt.factory import get_stt_provider
from stt.faster_whisper import FasterWhisperSttProvider, WhisperModelNotFound, build_hotwords, to_whisper_language
from utils import config
from utils.config import parse_stt_device

class FakeModel:
    """WhisperModel 대역. 토큰 수는 글자 수로 센다."""

    def __init__(self, segments=None, error=None):
        self.calls = []
        self.segments = segments or []
        self.error = error
        self.hf_tokenizer = SimpleNamespace(encode=lambda text, add_special_tokens: SimpleNamespace(ids=list(text)))

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if self.error:
            raise self.error
        info = SimpleNamespace(language=kwargs["language"], duration=9.25, duration_after_vad=8.5)
        return iter(self.segments), info


def fake_word(start, end, word, probability):
    return SimpleNamespace(start=start, end=end, word=word, probability=probability)


SEGMENTS = [
    SimpleNamespace(start=0.0, end=1.18, text=" 안녕하세요. 오늘은", words=[
        fake_word(0.0, 0.4, " 안녕하세요.", 0.9), fake_word(0.4, 1.18, " 오늘은", 0.83),
    ]),
    SimpleNamespace(start=6.34, end=7.12, text=" 네.", words=[fake_word(6.34, 7.1249, " 네.", 0.99)]),
]


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    """필수 파일만 있는 모델 디렉터리. 실제 로드는 _create_model 대역이 맡는다."""
    for name in fw.REQUIRED_MODEL_FILES:
        (tmp_path / name).write_text("{}")
    monkeypatch.setattr(config, "WHISPER_MODEL_PATH", str(tmp_path))
    monkeypatch.setattr(config, "STT_DEVICE", "cpu")
    monkeypatch.setattr(config, "STT_COMPUTE_TYPE", "int8")
    fw._unload_model()
    yield tmp_path
    fw._unload_model()


@pytest.fixture
def fake_model(model_dir, monkeypatch):
    model = FakeModel(SEGMENTS)
    created = []

    def create(path, device, compute_type):
        created.append((path, device, compute_type))
        return model, "1.2.1"

    monkeypatch.setattr(fw, "_create_model", create)
    model.created = created
    return model


# ==================== 변환 ====================

@pytest.mark.parametrize("language, expected", [("ko-KR", "ko"), ("en-US", "en"), ("ko", "ko")])
def test_language_code(language, expected):
    assert to_whisper_language(language) == expected


def test_hotwords_fit_the_token_budget_by_whole_keywords():
    count = len  # 글자 수를 토큰 수로
    assert build_hotwords(["검색엔진", " 벡터DB ", "", "문서"], count) == "용어: 검색엔진, 벡터DB, 문서."
    # " 용어: 검색엔진, 벡터DB."는 16자. 다음 키워드를 넣으면 넘치므로 키워드 중간에서 자르지 않고 멈춘다
    assert build_hotwords(["검색엔진", "벡터DB", "문서수집기"], count, max_tokens=16) == "용어: 검색엔진, 벡터DB."
    assert build_hotwords(["검색엔진"], count, max_tokens=9) is None  # " 용어: 검색엔진."은 10자
    assert build_hotwords([], count) is None


def test_transcribe_maps_words_and_options(fake_model, model_dir):
    result = FasterWhisperSttProvider().transcribe("/audio/x.wav", language="ko-KR", hints=SttHints(["검색엔진", "벡터DB"]))

    (audio, kwargs), = fake_model.calls
    assert audio == "/audio/x.wav"
    assert kwargs == {
        "language": "ko",
        "vad_filter": True,
        "word_timestamps": True,
        "hotwords": "용어: 검색엔진, 벡터DB.",
        "condition_on_previous_text": False,
    }
    assert result.engine == "faster_whisper"
    assert result.segments is None and result.speaker_names is None  # 화자는 화자분리·병합이 붙인다
    assert [(w.start_ms, w.end_ms, w.text, w.prob) for w in result.words] == [
        (0, 400, " 안녕하세요.", 0.9), (400, 1180, " 오늘은", 0.83), (6340, 7125, " 네.", 0.99),
    ]
    assert all(type(w.start_ms) is int and type(w.prob) is float for w in result.words)
    assert result.engine_version == f"faster-whisper 1.2.1 ({model_dir.name}, int8)"
    assert result.raw == {
        "language": "ko",
        "duration_ms": 9250,
        "duration_after_vad_ms": 8500,
        "hotwords": "용어: 검색엔진, 벡터DB.",
        "segments": [
            {"start_ms": 0, "end_ms": 1180, "text": " 안녕하세요. 오늘은"},
            {"start_ms": 6340, "end_ms": 7120, "text": " 네."},
        ],
    }
    json.dumps(result.raw)  # 원문 보존(stt_results jsonb, PR-9)에 그대로 넣을 수 있다


def test_no_keywords_means_no_hotwords(fake_model):
    FasterWhisperSttProvider().transcribe("x.wav", language="ko-KR", hints=SttHints())
    assert fake_model.calls[0][1]["hotwords"] is None


# ==================== 모델 로드 ====================

def test_model_is_loaded_once_and_unloaded(fake_model, model_dir):
    FasterWhisperSttProvider().transcribe("a.wav", language="ko-KR", hints=SttHints())
    FasterWhisperSttProvider().transcribe("b.wav", language="ko-KR", hints=SttHints())
    assert fake_model.created == [(str(model_dir), "cpu", "int8")]  # 작업마다 다시 올리지 않는다

    FasterWhisperSttProvider().unload()
    FasterWhisperSttProvider().transcribe("c.wav", language="ko-KR", hints=SttHints())
    assert len(fake_model.created) == 2


def test_settings_change_reloads_model(fake_model, monkeypatch):
    FasterWhisperSttProvider().transcribe("a.wav", language="ko-KR", hints=SttHints())
    monkeypatch.setattr(config, "STT_COMPUTE_TYPE", "int8_float32")
    FasterWhisperSttProvider().transcribe("b.wav", language="ko-KR", hints=SttHints())

    assert [created[2] for created in fake_model.created] == ["int8", "int8_float32"]


@pytest.mark.parametrize("missing", fw.REQUIRED_MODEL_FILES)
def test_missing_model_file_fails_without_loading(model_dir, monkeypatch, missing):
    """모델 id나 빠진 파일을 HF Hub에서 받지 않도록 faster-whisper를 부르기 전에 멈춘다."""
    (model_dir / missing).unlink()
    monkeypatch.setattr(fw, "_create_model", lambda *args: pytest.fail("must not load"))

    with pytest.raises(WhisperModelNotFound, match=missing):
        FasterWhisperSttProvider().transcribe("x.wav", language="ko-KR", hints=SttHints())


@pytest.mark.parametrize("path", ["large-v3", "Systran/faster-whisper-large-v3", "/models/none"])
def test_model_id_is_never_downloaded(monkeypatch, path):
    monkeypatch.setattr(config, "WHISPER_MODEL_PATH", path)
    monkeypatch.setattr(fw, "_create_model", lambda *args: pytest.fail("must not load"))
    fw._unload_model()

    with pytest.raises(WhisperModelNotFound, match="not a local CTranslate2 model directory"):
        FasterWhisperSttProvider().transcribe("x.wav", language="ko-KR", hints=SttHints())
    assert not is_retryable(WhisperModelNotFound())


def test_cuda_out_of_memory_is_retryable_and_unloads(fake_model):
    FasterWhisperSttProvider().transcribe("a.wav", language="ko-KR", hints=SttHints())
    fake_model.error = RuntimeError("CUDA failed with error out of memory")

    with pytest.raises(RetryableError) as raised:
        FasterWhisperSttProvider().transcribe("b.wav", language="ko-KR", hints=SttHints())
    assert is_retryable(raised.value)
    assert fw._loaded is None  # 다시 시도할 때 새로 올린다


def test_other_runtime_errors_are_not_retried(fake_model):
    fake_model.error = RuntimeError("Invalid input features shape")

    with pytest.raises(RuntimeError, match="Invalid input") as raised:
        FasterWhisperSttProvider().transcribe("a.wav", language="ko-KR", hints=SttHints())
    assert not is_retryable(raised.value)


# ==================== 설정·팩토리 ====================

def test_factory_returns_adapter_without_loading(reload_config, monkeypatch):
    reload_config(APP_PROFILE="airgap")
    monkeypatch.setattr(fw, "_create_model", lambda *args: pytest.fail("must not load"))
    assert isinstance(get_stt_provider(), FasterWhisperSttProvider)


def test_device_and_compute_type_defaults(reload_config):
    cfg = reload_config(APP_PROFILE="airgap")
    assert (cfg.WHISPER_MODEL_PATH, cfg.STT_DEVICE, cfg.STT_COMPUTE_TYPE) == ("/models/faster-whisper-large-v3", "cuda", "float16")
    cfg = reload_config(APP_PROFILE="airgap", STT_DEVICE="cpu")
    assert cfg.STT_COMPUTE_TYPE == "int8"
    cfg = reload_config(APP_PROFILE="airgap", STT_DEVICE="cpu", STT_COMPUTE_TYPE="float32", WHISPER_MODEL_PATH="/m/tiny")
    assert (cfg.WHISPER_MODEL_PATH, cfg.STT_COMPUTE_TYPE) == ("/m/tiny", "float32")


@pytest.mark.parametrize("value", ["", "gpu", "CUDA", "auto"])
def test_invalid_stt_device_is_rejected(value):
    with pytest.raises(ValueError, match="Invalid STT_DEVICE"):
        parse_stt_device(value)


# ==================== CPU 스모크 (-m slow) ====================

@pytest.fixture
def no_network(monkeypatch):
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(args)
        raise OSError("network is blocked in this test")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    return attempts


def synthesize(path):
    """영어 합성 음성 두 문장(다른 목소리, 사이에 1.5초 무음)을 16kHz mono WAV로 만든다."""
    first = "Good morning everyone. Today we will discuss the search engine migration."
    second = "Sounds good. Let us set the schedule first."
    completed = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"flite=text='{first}':voice=slt", "-f", "lavfi", "-i", f"flite=text='{second}':voice=kal",
         "-filter_complex", "[0:a]apad=pad_dur=1.5[a];[a][1:a]concat=n=2:v=0:a=1", "-ar", "16000", "-ac", "1", str(path)],
        capture_output=True,
    )
    if completed.returncode != 0:
        pytest.skip("local ffmpeg has no flite source")


@pytest.mark.slow
def test_cpu_smoke_transcribes_synthetic_speech(tmp_path, monkeypatch, no_network):
    pytest.importorskip("faster_whisper")
    model_path = os.environ.get("WHISPER_TEST_MODEL_PATH")
    if not model_path or not Path(model_path).is_dir():
        pytest.skip("set WHISPER_TEST_MODEL_PATH to a local faster-whisper model directory (e.g. faster-whisper-tiny)")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(config, "WHISPER_MODEL_PATH", model_path)
    monkeypatch.setattr(config, "STT_DEVICE", "cpu")
    monkeypatch.setattr(config, "STT_COMPUTE_TYPE", "int8")
    wav = tmp_path / "speech.wav"
    synthesize(wav)

    provider = FasterWhisperSttProvider()
    try:
        result = provider.transcribe(str(wav), language="en-US", hints=SttHints(["search engine", "migration"]))
    finally:
        provider.unload()

    assert no_network == []
    assert result.engine_version.startswith("faster-whisper 1.2.1")
    assert (result.raw["language"], result.raw["hotwords"]) == ("en", "용어: search engine, migration.")
    text = " ".join(w.text.strip() for w in result.words).lower()
    # 키워드만 늘어놓은 hotwords("search engine, migration")로는 tiny 모델이 키워드로 끝나는 첫 문장을 통째로 건너뛰었다
    assert "search engine migration" in text and "schedule" in text
    assert all(0 <= w.start_ms <= w.end_ms <= result.raw["duration_ms"] for w in result.words)
    assert all(a.start_ms <= b.start_ms for a, b in zip(result.words, result.words[1:]))
    # 두 문장 사이의 1.5초 무음이 단어 타임스탬프에 드러난다(화자분리 병합의 입력)
    assert max(b.start_ms - a.end_ms for a, b in zip(result.words, result.words[1:])) >= 1000
