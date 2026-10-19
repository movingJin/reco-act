"""STT 프로바이더 추상화 (01-plan 3.2, PR-4).

127.0.0.1에 가짜 Clova Speech 서버를 띄우고 합성 응답(synthetic_clova_response.json)을 돌려준다. 외부로 나가지 않는다.
Clova 어댑터의 출력과 요청이 기존 clova_stt.convert()와 같은지(골든), 전사 서비스가 public에서 그대로 동작하는지 확인한다.
"""
import json
import threading
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydub import AudioSegment

import clova_stt
from models.meeting import TranscriptSegment
from pipeline.audio import AudioDecodeError
from services import transcription_service
from stt.base import SttHints
from stt.clova import ClovaSttProvider
from stt.factory import get_stt_provider
from stt.faster_whisper import WhisperModelNotFound
from stt.mock import MOCK_WORDS, MockSttProvider
from utils import config
from utils.config import parse_stt_provider

CLOVA_RESPONSE = json.loads((Path(__file__).parent / "synthetic_clova_response.json").read_text(encoding="utf-8"))
KEYWORDS = ["검색엔진", "벡터DB"]

# 기존 파서(clova_stt)가 합성 응답에서 만드는 값. 어댑터와 전사 서비스가 이 값을 그대로 내야 한다.
EXPECTED_SEGMENTS = [
    TranscriptSegment(speaker_index=0, text="오늘은 검색엔진 개편 일정을 정하겠습니다.", start=0, end=3400),
    TranscriptSegment(speaker_index=1, text="벡터DB 테스트는 다음 주에 끝납니다.", start=3900, end=6800),  # text보다 textEdited 우선
    TranscriptSegment(speaker_index=0, text="좋습니다.", start=7100, end=8300),
    TranscriptSegment(speaker_index=2, text="문서 수집은 제가 맡겠습니다.", start=8600, end=11200),
    TranscriptSegment(speaker_index=1, text="그럼 금요일에 다시 보죠.", start=11500, end=12900),
]
EXPECTED_SPEAKER_NAMES = ["화자A", "화자B", "화자C"]  # speakers를 label 오름차순으로


class FakeClovaServer:
    def __init__(self):
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                form = BytesParser(policy=HTTP).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body
                )
                parts = {part.get_param("name", header="content-disposition"): part for part in form.iter_parts()}
                server.requests.append({
                    "path": self.path,
                    "api_key": self.headers.get("X-CLOVASPEECH-API-KEY"),
                    "params": json.loads(parts["params"].get_payload(decode=True)),
                    "media_filename": parts["media"].get_filename(),
                    "media": parts["media"].get_payload(decode=True),
                })
                payload = json.dumps(CLOVA_RESPONSE, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json;charset=UTF-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_port}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def fake_clova(monkeypatch):
    server = FakeClovaServer()
    monkeypatch.setenv("CLOVA_INVOKE_URL", server.url)
    monkeypatch.setenv("CLOVA_SECRET_KEY", "test-clova-key")
    yield server
    server.close()


def write_silence(path):
    AudioSegment.silent(duration=1500, frame_rate=16000).export(str(path), format="wav")
    return path


# ==================== Clova 어댑터 (골든) ====================

def test_clova_adapter_matches_existing_client(fake_clova, tmp_path):
    """어댑터의 출력과 Clova로 보내는 요청이 기존 clova_stt.convert()와 같다."""
    wav = write_silence(tmp_path / "meeting.wav")

    before = clova_stt.convert(file_path=str(wav), language="ko-KR", domain_keywords=KEYWORDS)
    result = ClovaSttProvider().transcribe(str(wav), language="ko-KR", hints=SttHints(keywords=KEYWORDS))

    assert (result.segments, result.speaker_names) == before
    assert (result.segments, result.speaker_names) == (EXPECTED_SEGMENTS, EXPECTED_SPEAKER_NAMES)
    assert (result.engine, result.words) == ("clova", [])

    old_request, new_request = fake_clova.requests
    assert new_request == old_request
    assert (new_request["path"], new_request["api_key"]) == ("/recognizer/upload", "test-clova-key")
    assert new_request["params"] == {
        "language": "ko-KR",
        "completion": "sync",
        "callback": "",
        "fullText": True,
        "boostings": [{"words": "검색엔진, 벡터DB", "weight": 1.0}],
    }
    assert (new_request["media_filename"], new_request["media"]) == ("meeting.wav", wav.read_bytes())


def test_clova_adapter_without_keywords(fake_clova, tmp_path):
    wav = write_silence(tmp_path / "meeting.wav")
    ClovaSttProvider().transcribe(str(wav), language="ko-KR", hints=SttHints())

    (request,) = fake_clova.requests
    assert "boostings" not in request["params"]


def test_mock_returns_words_without_speakers(tmp_path):
    result = MockSttProvider().transcribe(str(tmp_path / "unused.wav"), language="ko-KR", hints=SttHints(KEYWORDS))

    assert result.engine == "mock"
    assert (result.segments, result.speaker_names) == (None, None)
    assert result.words == list(MOCK_WORDS)
    assert all(word.start_ms < word.end_ms for word in result.words)
    assert all(a.end_ms <= b.start_ms for a, b in zip(result.words, result.words[1:]))


# ==================== 설정·팩토리 ====================

@pytest.mark.parametrize("profile, expected", [("public", "clova"), ("airgap", "faster_whisper")])
def test_stt_provider_default(profile, expected):
    assert parse_stt_provider(None, profile) == expected


@pytest.mark.parametrize("profile, value", [
    ("public", "clova"), ("public", "mock"), ("airgap", "faster_whisper"), ("airgap", "qwen3_asr"), ("airgap", "mock"),
])
def test_valid_stt_providers(profile, value):
    assert parse_stt_provider(value, profile) == value


@pytest.mark.parametrize("profile", ["public", "airgap"])
@pytest.mark.parametrize("value", ["", "Clova", "whisper", " mock"])
def test_invalid_stt_providers_are_rejected(profile, value):
    with pytest.raises(ValueError, match="Invalid STT_PROVIDER"):
        parse_stt_provider(value, profile)


def test_airgap_clova_stops_import(reload_config):
    """airgap에서 외부 API(Clova)를 고르면 config import가 실패해 기동이 멈춘다."""
    with pytest.raises(ValueError, match="cannot be used with APP_PROFILE=airgap"):
        reload_config(APP_PROFILE="airgap", STT_PROVIDER="clova")


def test_factory_public_default_is_clova():
    assert config.STT_PROVIDER == "clova"
    assert isinstance(get_stt_provider(), ClovaSttProvider)


def test_factory_mock(reload_config):
    reload_config(STT_PROVIDER="mock")
    assert isinstance(get_stt_provider(), MockSttProvider)


# ==================== 전사 서비스 ====================

MEETING_ID = "meeting-1"
DOMAIN_ID = 7


@pytest.fixture
def transcription_env(tmp_path, monkeypatch):
    """DB 함수를 호출 기록용 대역으로 바꾸고, 업로드된 원본(1.5초 무음)을 만든다."""
    source = write_silence(tmp_path / f"source_{MEETING_ID}_1.wav")
    records = tmp_path / "records"
    monkeypatch.setattr(transcription_service, "RECORDS_DIR", records)

    calls = []

    def recorder(name):
        return lambda *args, **kwargs: calls.append((name, args, kwargs))

    for name in ("update_meeting_settings", "update_transcript", "add_audio_file", "clear_audio_file", "set_transcription_status"):
        monkeypatch.setattr(transcription_service, name, recorder(name))
    monkeypatch.setattr(transcription_service, "get_pending_transcription", lambda meeting_id: (str(source), DOMAIN_ID))
    monkeypatch.setattr(
        transcription_service, "get_domain_keywords", lambda domain_id: KEYWORDS if domain_id == DOMAIN_ID else None
    )
    return SimpleNamespace(source=source, records=records, calls=calls)


def test_public_transcription_through_clova_adapter(fake_clova, transcription_env):
    transcription_service.transcribe_meeting(MEETING_ID)

    (request,) = fake_clova.requests
    assert request["params"]["boostings"] == [{"words": "검색엔진, 벡터DB", "weight": 1.0}]

    settings, transcript, audio, status = transcription_env.calls
    assert settings == ("update_meeting_settings", (MEETING_ID, EXPECTED_SPEAKER_NAMES), {})
    assert transcript == ("update_transcript", (MEETING_ID, [seg.model_dump() for seg in EXPECTED_SEGMENTS]), {})

    name, (meeting_id, wav_path), kwargs = audio
    assert (name, meeting_id, kwargs) == ("add_audio_file", MEETING_ID, {"duration_ms": 1500})
    assert Path(wav_path).parent == transcription_env.records
    assert request["media"] == Path(wav_path).read_bytes()

    assert status == ("set_transcription_status", (MEETING_ID, "done"), {"clear_source": True})
    assert not transcription_env.source.exists()


@pytest.mark.parametrize("stt_provider", [None, "mock"])
def test_airgap_transcription_never_calls_clova(fake_clova, transcription_env, reload_config, stt_provider):
    """Clova 접속 정보가 있어도 airgap은 Clova를 부르지 않는다.

    기본값 faster_whisper는 모델 디렉터리(/models/...)가 없어 STT에서, mock(단어만 줌)은 기본 화자분리 pyannote(PR-8 전)에서
    예외로 끝난다. 실패 상태 기록은 워커가 맡는다(test_worker).
    """
    env = {"APP_PROFILE": "airgap"}
    if stt_provider:
        env["STT_PROVIDER"] = stt_provider
    reload_config(**env)

    expected = (WhisperModelNotFound, "WHISPER_MODEL_PATH") if stt_provider is None else (NotImplementedError, "DIARIZER=pyannote")
    with pytest.raises(expected[0], match=expected[1]):
        transcription_service.transcribe_meeting(MEETING_ID)

    assert fake_clova.requests == []
    assert transcription_env.calls == []  # 아무것도 저장하지 않는다
    assert transcription_env.source.exists()  # 다시 처리할 수 있게 원본은 남긴다


def test_airgap_words_engine_is_diarized_and_merged(fake_clova, transcription_env, reload_config):
    """airgap: ffmpeg 16kHz mono 정규화 → 단어만 주는 STT(mock) → 화자분리(mock) → 병합 → 기존 저장 경로."""
    reload_config(APP_PROFILE="airgap", STT_PROVIDER="mock", DIARIZER="mock")
    stages = []

    saved = transcription_service.transcribe_meeting(MEETING_ID, stages.append)

    assert saved == 2
    assert stages == ["normalize", "stt", "diarize", "merge", "save"]
    assert fake_clova.requests == []

    settings, transcript, audio, status = transcription_env.calls
    assert settings == ("update_meeting_settings", (MEETING_ID, ["화자1", "화자2"]), {})
    assert transcript == ("update_transcript", (MEETING_ID, [
        {"speaker_index": 0, "text": "안녕하세요. 오늘은 검색 개편을 논의합니다.", "start": 0, "end": 3400},
        {"speaker_index": 1, "text": "네, 일정부터 정하죠.", "start": 4200, "end": 6100},
    ]), {})

    name, (meeting_id, wav_path), kwargs = audio
    assert (name, meeting_id, kwargs) == ("add_audio_file", MEETING_ID, {"duration_ms": 1500})
    assert Path(wav_path).parent == transcription_env.records
    stored = AudioSegment.from_file(wav_path)  # 재생용 파일도 정규화한 mono 16kHz다
    assert (stored.channels, stored.frame_rate, len(stored)) == (1, 16000, 1500)

    assert status == ("set_transcription_status", (MEETING_ID, "done"), {"clear_source": True})
    assert not transcription_env.source.exists()


def test_words_engine_without_diarizer_fails(transcription_env, reload_config):
    """화자분리를 고르지 않은 채(public 기본) 단어만 주는 엔진을 쓰면 저장하지 않고 예외로 끝난다."""
    reload_config(STT_PROVIDER="mock")

    with pytest.raises(RuntimeError, match="returned no speaker segments and DIARIZER is not set"):
        transcription_service.transcribe_meeting(MEETING_ID)

    assert transcription_env.calls == []
    assert transcription_env.source.exists()


def test_airgap_undecodable_audio_fails_before_stt(transcription_env, reload_config, monkeypatch):
    reload_config(APP_PROFILE="airgap", STT_PROVIDER="mock", DIARIZER="mock")
    transcription_env.source.write_bytes(b"not audio at all")
    monkeypatch.setattr(transcription_service, "get_stt_provider", lambda: pytest.fail("STT must not run"))
    stages = []

    with pytest.raises(AudioDecodeError):
        transcription_service.transcribe_meeting(MEETING_ID, stages.append)

    assert stages == ["normalize"]
    assert transcription_env.calls == []
    assert list(transcription_env.records.iterdir()) == []  # 반쯤 쓴 WAV를 남기지 않는다
    assert transcription_env.source.exists()
