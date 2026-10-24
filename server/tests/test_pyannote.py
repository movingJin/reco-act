"""pyannote community-1 화자분리 (01-plan 3.3, PR-8).

단위 테스트는 가짜 파이프라인을 넣어 pyannote 없이 돈다(public 테스트 환경에는 requirements-airgap.txt가 없다).
-m slow 스모크는 실제 pyannote로 CPU 화자분리를 한다. 셸 env PYANNOTE_TEST_PIPELINE_PATH(community-1 로컬 디렉터리)가 있어야 돌고,
음성은 ffmpeg flite의 서로 다른 목소리로 만든 영어 합성 대화만 쓴다. 네트워크 연결과 DNS 조회는 막고 돌린다.
airgap 전체 경로(정규화 → faster-whisper → pyannote → 병합) 스모크는 WHISPER_TEST_MODEL_PATH도 있어야 돈다.
"""
import os
import socket
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from diarization import pyannote as pa
from diarization.base import Turn
from diarization.factory import get_diarizer
from diarization.pyannote import PyannoteDiarizer, PyannotePipelineNotFound
from services import transcription_service
from services.job_service import RetryableError, is_retryable
from utils import config


class FakeAnnotation:
    def __init__(self, tracks):
        self.tracks = tracks

    def itertracks(self, yield_label=False):
        assert yield_label
        for start, end, label in self.tracks:
            yield SimpleNamespace(start=start, end=end), None, label


class FakePipeline:
    """pyannote Pipeline 대역. 일반 출력과 exclusive 출력을 다르게 두어 어느 쪽을 쓰는지 확인한다."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, audio):
        self.calls.append((audio, os.environ.get("PYANNOTE_METRICS_ENABLED")))
        if self.error:
            raise self.error
        return SimpleNamespace(
            speaker_diarization=FakeAnnotation([(0.0, 5.2, "SPEAKER_00"), (4.8, 9.0, "SPEAKER_01")]),  # 겹침 있음
            exclusive_speaker_diarization=FakeAnnotation([(0.18, 4.99, "SPEAKER_00"), (6.2749, 9.0, "SPEAKER_01")]),
        )


@pytest.fixture
def pipeline_dir(tmp_path, monkeypatch):
    """필수 파일만 있는 파이프라인 디렉터리. 실제 로드는 _create_pipeline 대역이 맡는다."""
    for name in pa.REQUIRED_PIPELINE_FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x")
    monkeypatch.setattr(config, "PYANNOTE_PIPELINE_PATH", str(tmp_path))
    monkeypatch.setattr(config, "STT_DEVICE", "cpu")
    pa._unload_pipeline()
    yield tmp_path
    pa._unload_pipeline()


@pytest.fixture
def fake_pipeline(pipeline_dir, monkeypatch):
    pipeline = FakePipeline()
    pipeline.created = []

    def create(path, device):
        pipeline.created.append((path, device))
        return pipeline

    monkeypatch.setattr(pa, "_create_pipeline", create)
    monkeypatch.setattr(pa, "_load_audio", lambda wav_path: {"waveform": f"waveform of {wav_path}", "sample_rate": 16000})
    return pipeline


# ==================== 단위 ====================

def test_diarize_uses_exclusive_output_in_ms(fake_pipeline, monkeypatch):
    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "1")

    turns = PyannoteDiarizer().diarize("/audio/x.wav")

    assert turns == [Turn(180, 4990, "SPEAKER_00"), Turn(6275, 9000, "SPEAKER_01")]
    (audio, metrics), = fake_pipeline.calls
    assert audio == {"waveform": "waveform of /audio/x.wav", "sample_rate": 16000}  # 파일 경로가 아니라 메모리 파형
    assert metrics == "0"  # 셸에서 켜 두어도 부를 때 끈다


def test_pipeline_is_loaded_once_and_unloaded(fake_pipeline, pipeline_dir):
    PyannoteDiarizer().diarize("a.wav")
    PyannoteDiarizer().diarize("b.wav")
    assert fake_pipeline.created == [(str(pipeline_dir), "cpu")]

    PyannoteDiarizer().unload()
    PyannoteDiarizer().diarize("c.wav")
    assert len(fake_pipeline.created) == 2


def test_settings_change_reloads_pipeline(fake_pipeline, monkeypatch):
    PyannoteDiarizer().diarize("a.wav")
    monkeypatch.setattr(config, "STT_DEVICE", "cuda")
    PyannoteDiarizer().diarize("b.wav")

    assert [created[1] for created in fake_pipeline.created] == ["cpu", "cuda"]


@pytest.mark.parametrize("missing", pa.REQUIRED_PIPELINE_FILES)
def test_missing_pipeline_file_fails_without_loading(pipeline_dir, monkeypatch, missing):
    (pipeline_dir / missing).unlink()
    monkeypatch.setattr(pa, "_create_pipeline", lambda *args: pytest.fail("must not load"))

    with pytest.raises(PyannotePipelineNotFound, match=missing):
        PyannoteDiarizer().diarize("x.wav")


@pytest.mark.parametrize("path", ["pyannote/speaker-diarization-community-1", "/models/none"])
def test_repo_id_is_never_downloaded(monkeypatch, path):
    monkeypatch.setattr(config, "PYANNOTE_PIPELINE_PATH", path)
    monkeypatch.setattr(pa, "_create_pipeline", lambda *args: pytest.fail("must not load"))
    pa._unload_pipeline()

    with pytest.raises(PyannotePipelineNotFound, match="not a local pipeline directory"):
        PyannoteDiarizer().diarize("x.wav")
    assert not is_retryable(PyannotePipelineNotFound())


def test_out_of_memory_is_retryable_and_unloads(fake_pipeline):
    PyannoteDiarizer().diarize("a.wav")
    fake_pipeline.error = type("OutOfMemoryError", (RuntimeError,), {})("CUDA out of memory. Tried to allocate 2.00 GiB")

    with pytest.raises(RetryableError) as raised:
        PyannoteDiarizer().diarize("b.wav")
    assert is_retryable(raised.value)
    assert pa._loaded is None


def test_other_errors_are_not_retried(fake_pipeline):
    fake_pipeline.error = ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        PyannoteDiarizer().diarize("a.wav")


def test_factory_returns_pyannote_without_loading(reload_config, monkeypatch):
    cfg = reload_config(APP_PROFILE="airgap")
    monkeypatch.setattr(pa, "_create_pipeline", lambda *args: pytest.fail("must not load"))

    assert isinstance(get_diarizer(), PyannoteDiarizer)
    assert cfg.PYANNOTE_PIPELINE_PATH == "/models/speaker-diarization-community-1"


def test_load_audio_reads_mono_and_stereo_pcm(tmp_path):
    pytest.importorskip("torch")
    mono, stereo = tmp_path / "mono.wav", tmp_path / "stereo.wav"
    for path, channels, frames in ((mono, 1, b"\x00\x40\x00\xc0"), (stereo, 2, b"\x00\x40\x00\xc0\x00\x20\x00\xe0")):
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(frames)

    audio = pa._load_audio(str(mono))
    assert audio["sample_rate"] == 16000
    assert audio["waveform"].shape == (1, 2) and audio["waveform"].tolist() == [[0.5, -0.5]]
    assert pa._load_audio(str(stereo))["waveform"].tolist() == [[0.5, 0.25], [-0.5, -0.25]]  # (channel, time)


# ==================== CPU 스모크 (-m slow) ====================

# 서로 다른 flite 목소리. 첫 화자가 마지막에 다시 말한다.
DIALOG = [
    ("slt", "Good morning everyone. Today we will review the search engine migration plan and the schedule."),
    ("kal", "Thanks. The index rebuild finished last night, and the latency numbers look much better than before."),
    ("awb", "I still have concerns about the vector database costs. Can we compare the two vendors before Friday?"),
    ("slt", "Sure. Let us collect the numbers first and then decide on the vendor next week."),
]


@pytest.fixture
def no_network(monkeypatch):
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(args)
        raise OSError("network is blocked in this test")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)  # 텔레메트리는 연결 전에 DNS부터 조회한다
    return attempts


def synthesize_dialog(directory):
    """DIALOG를 이어 붙인 16kHz mono WAV와 차례별 (시작 ms, 끝 ms)를 만든다. 차례 사이에 0.8초 무음을 둔다."""
    parts, spans, start = [], [], 0
    for i, (voice, text) in enumerate(DIALOG):
        part = directory / f"part{i}.wav"
        completed = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"flite=text='{text}':voice={voice}",
             "-af", "apad=pad_dur=0.8", "-ar", "16000", "-ac", "1", str(part)],
            capture_output=True,
        )
        if completed.returncode != 0:
            pytest.skip("local ffmpeg has no flite source")
        with wave.open(str(part), "rb") as wav:
            end = start + wav.getnframes() * 1000 // wav.getframerate()
        parts.append(part)
        spans.append((start, end))
        start = end
    dialog = directory / "dialog.wav"
    inputs = [arg for part in parts for arg in ("-i", str(part))]
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *inputs, "-filter_complex", f"concat=n={len(parts)}:v=0:a=1",
         "-ar", "16000", "-ac", "1", str(dialog)],
        check=True, capture_output=True,
    )
    return dialog, spans


def require_model(env_name):
    path = os.environ.get(env_name)
    if not path or not Path(path).is_dir():
        pytest.skip(f"set {env_name} to a local model directory")
    return path


@pytest.fixture
def real_pyannote(monkeypatch):
    pytest.importorskip("pyannote.audio")
    monkeypatch.setattr(config, "PYANNOTE_PIPELINE_PATH", require_model("PYANNOTE_TEST_PIPELINE_PATH"))
    monkeypatch.setattr(config, "STT_DEVICE", "cpu")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    pa._unload_pipeline()
    yield
    pa._unload_pipeline()


@pytest.mark.slow
def test_cpu_smoke_diarizes_synthetic_dialog(tmp_path, real_pyannote, no_network):
    dialog, spans = synthesize_dialog(tmp_path)

    turns = PyannoteDiarizer().diarize(str(dialog))

    assert no_network == []
    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "0"
    assert all(t.start_ms < t.end_ms for t in turns)
    assert all(a.end_ms <= b.start_ms for a, b in zip(turns, turns[1:]))  # exclusive: 겹치지 않는다

    def speaker_at(ms):
        (speaker,) = {t.speaker for t in turns if t.start_ms <= ms <= t.end_ms}
        return speaker

    speakers = [speaker_at((start + end) // 2) for start, end in spans]
    assert len(set(speakers)) == 3
    assert speakers[0] == speakers[3] != speakers[1] != speakers[2] != speakers[0]


@pytest.mark.slow
def test_airgap_pipeline_end_to_end(tmp_path, real_pyannote, no_network, monkeypatch):
    """정규화 → faster-whisper → pyannote → 병합 → 저장 경로까지 실제 엔진으로 돈다(DB 저장 함수만 대역)."""
    pytest.importorskip("faster_whisper")
    from stt import faster_whisper as fw

    monkeypatch.setattr(config, "APP_PROFILE", "airgap")
    monkeypatch.setattr(config, "DIARIZER", "pyannote")
    monkeypatch.setattr(config, "WHISPER_MODEL_PATH", require_model("WHISPER_TEST_MODEL_PATH"))
    monkeypatch.setattr(config, "STT_COMPUTE_TYPE", "int8")
    dialog, _ = synthesize_dialog(tmp_path)
    source = tmp_path / "source_m1_1.webm"  # 확장자와 상관없이 내용으로 판별한다
    dialog.rename(source)

    saved = {}
    monkeypatch.setattr(transcription_service, "RECORDS_DIR", tmp_path / "records")
    monkeypatch.setattr(transcription_service, "get_pending_transcription", lambda meeting_id: (str(source), None))
    monkeypatch.setattr(transcription_service, "update_meeting_settings", lambda m, names: saved.update(names=names))
    monkeypatch.setattr(transcription_service, "update_transcript", lambda m, data: saved.update(transcript=data))
    monkeypatch.setattr(transcription_service, "save_stt_result", lambda m, **fields: saved.update(record=fields))
    for name in ("add_audio_file", "clear_audio_file", "set_transcription_status"):
        monkeypatch.setattr(transcription_service, name, lambda *args, **kwargs: None)
    stages = []

    class EnglishAudio:
        """서비스는 언어를 ko-KR로 고정해 부른다. 합성 음성이 영어라 실제 faster-whisper에는 en으로 넘긴다.
        영어 음성을 ko로 디코딩하면 tiny 모델이 반복 생성에 빠져 26초 음성에 150초를 넘겼다."""
        name = "faster_whisper"

        def transcribe(self, wav_path, *, language, hints):
            assert language == "ko-KR"
            return fw.FasterWhisperSttProvider().transcribe(wav_path, language="en-US", hints=hints)

    monkeypatch.setattr(transcription_service, "get_stt_provider", EnglishAudio)

    try:
        transcription_service.transcribe_meeting("m1", stages.append)
    finally:
        fw._unload_model()

    assert no_network == []
    assert stages == ["normalize", "stt", "diarize", "merge", "glossary", "save"]
    assert saved["names"] == ["화자1", "화자2", "화자3"]
    record = saved["record"]  # 원문 보존: 실제 엔진의 단어, 화자 구간, 엔진 정보
    assert record["engine"] == "faster_whisper" and record["engine_version"].startswith("faster-whisper 1.2.1")
    assert record["words"] and {t["speaker"] for t in record["diarization"]} == {"SPEAKER_00", "SPEAKER_01", "SPEAKER_02"}
    assert record["corrected_segments"] == saved["transcript"]  # 사전이 없으면 원문과 같다
    assert record["raw_segments"] == saved["transcript"]
    turns = []  # 연속된 같은 화자 세그먼트를 묶은 차례
    for segment in saved["transcript"]:
        if turns and turns[-1][0] == segment["speaker_index"]:
            turns[-1][1].append(segment["text"])
        else:
            turns.append((segment["speaker_index"], [segment["text"]]))
    assert [speaker for speaker, _ in turns] == [0, 1, 2, 0]
    texts = [" ".join(parts).lower() for _, parts in turns]
    # tiny 모델은 "latency"를 "the late and"처럼 틀리므로 잘 맞히는 단어만 본다
    assert "search engine" in texts[0] and "index" in texts[1] and "vector database" in texts[2] and "vendor" in texts[3]
