"""평가 도구(eval/run_eval.py, eval/metrics.py)의 검사 (01-plan PR-18).

- 지표: 비트 병렬 편집 거리(DP와 대조), CER 정규화, 용어 적중률, DER(pyannote.metrics)
- 샘플: manifest·정답 전사 검사, 음원이 없거나 SHA256이 다르면 아무것도 돌리지 않음
- accuracy: mock 엔진으로 워커와 같은 흐름(pipeline/recognize.py)을 끝까지, 한 샘플 실패 시 나머지 진행
- e2e: 가짜 API 서버로 업로드 → 전사 → 요약 대기, 임시 도메인, 정리
- -m slow: 실제 엔진(faster-whisper, pyannote)으로 합성 대화 1건 리허설(셸 env의 모델 경로, 네트워크 차단)
"""
import hashlib
import json
import math
import os
import random
import re
import socket
import struct
import subprocess
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
sys.path.insert(0, str(EVAL_DIR))
import metrics  # noqa: E402
import run_eval  # noqa: E402


def dp_distance(a, b):
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def sine_wav(path: Path, seconds=2.0, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / rate)))
                               for i in range(int(seconds * rate))))
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_samples(tmp_path, samples, references):
    """manifest와 정답 전사를 tmp에 쓴다. references: {파일 이름: segments}"""
    root = tmp_path / "samples"
    root.mkdir(exist_ok=True)
    for name, segments in references.items():
        (root / name).write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "samples": samples}, ensure_ascii=False), encoding="utf-8")
    return manifest


def sample_entry(sample_id, audio: Path, **extra):
    return dict({"id": sample_id, "audio": audio.name, "sha256": sha256(audio), "duration_sec": 2.0, "speakers": 2,
                 "language": "ko-KR", "reference": f"{sample_id}.ref.json"}, **extra)


# ==================== 지표 ====================

def test_bit_parallel_distance_matches_dp():
    rng = random.Random(7)
    for alphabet in ("ab", "가나다라마", "abc가나1 "):
        for _ in range(150):
            a = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 140)))
            b = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 140)))
            assert metrics.edit_distance(a, b) == dp_distance(a, b), (a, b)
    long_ref = "".join(rng.choice("가나다라마바사") for _ in range(3000))  # 비트열이 기계어 크기를 한참 넘는다
    long_hyp = long_ref[:1000] + "아" + long_ref[1001:2500] + long_ref[2600:]
    assert metrics.edit_distance(long_ref, long_hyp) == dp_distance(long_ref, long_hyp)


def test_cer_ignores_spacing_and_punctuation():
    assert metrics.normalize("오늘은 검색엔진, 개편!  Vector-DB 3개") == "오늘은검색엔진개편vectordb3개"
    assert metrics.cer("오늘은 검색엔진, 개편을 합니다.", "오늘은검색엔진 개편을 합니다")["cer"] == 0
    one_wrong = metrics.cer("가나다라", "가나마라")
    assert (one_wrong["errors"], one_wrong["ref_chars"], one_wrong["cer"]) == (1, 4, 0.25)
    assert metrics.cer("", "")["cer"] == 0 and metrics.cer("", "가")["cer"] == 1.0


def test_term_hits_count_occurrences_without_spacing():
    result = metrics.term_hits("엘라스틱서치와 벡터DB, 다시 엘라스틱서치", "엘라스틱 서치와 벡터 디비, 엘라스틱서치 엘라스틱서치",
                               ["엘라스틱서치", "벡터DB", "엘라스틱서치", "쿠버네티스"])
    assert result["terms"]["엘라스틱서치"] == {"reference": 2, "hypothesis": 3}
    assert result["terms"]["벡터DB"] == {"reference": 1, "hypothesis": 0}
    assert (result["hits"], result["expected"], result["extra"]) == (2, 3, 1)
    assert result["rate"] == pytest.approx(2 / 3)
    assert metrics.term_hits("가", "가", [])["rate"] is None


def test_der_maps_speakers_and_uses_collar():
    reference = [{"speaker": "A", "start": 0, "end": 10}, {"speaker": "B", "start": 10, "end": 20}]
    swapped = [{"speaker": 1, "start": 0, "end": 10.2}, {"speaker": 0, "start": 10.2, "end": 20}]  # 0.2초 어긋남은 collar 안
    assert metrics.der(reference, swapped, 20)["der"] == 0
    one = metrics.der(reference, [{"speaker": 0, "start": 0, "end": 20}], 20)
    assert one["der"] == pytest.approx(9.5 / 19) and (one["ref_speakers"], one["hyp_speakers"]) == (2, 1)
    assert metrics.der(reference, [], 20)["missed"] == pytest.approx(19)
    # 정답에 말이 없는 곳(15~18초)을 화자로 잡으면 오탐이다. 평가 구간은 음원 전체(0~20초)
    alone = [{"speaker": "A", "start": 0, "end": 10}]
    extra = metrics.der(alone, [{"speaker": 0, "start": 0, "end": 10}, {"speaker": 1, "start": 15, "end": 18}], 20)
    assert extra["false_alarm"] == pytest.approx(3) and extra["der"] == pytest.approx(3 / 9.5)


# ==================== 샘플 ====================

def test_manifest_problems_are_reported_together(tmp_path):
    audio = sine_wav(tmp_path / "a.wav")
    manifest = write_samples(tmp_path, [
        sample_entry("ok", audio),
        dict(sample_entry("ok", audio), audio="../a.wav"),
        dict(sample_entry("bad-sha", audio), sha256="ABC"),
        dict(sample_entry("bad-ref", audio), reference="missing.json"),
        dict(sample_entry("bad-seg", audio), corrections=[["only-one"]]),
        {"id": "short"},
    ], {"ok.ref.json": [{"speaker": "A", "start": 0, "end": 1, "text": "가"}],
        "bad-sha.ref.json": [], "bad-seg.ref.json": [{"speaker": "A", "start": 2, "end": 1, "text": "가"}]})
    with pytest.raises(run_eval.EvalError) as caught:
        run_eval.load_manifest(manifest)
    message = str(caught.value)
    for expected in ("id must be unique", "audio must be a file name", "sha256 must be", "cannot read reference missing.json",
                     "corrections must be", "0 <= start < end", "samples[5] short: missing"):
        assert expected in message


def test_missing_or_changed_audio_stops_before_running(tmp_path, monkeypatch, capsys):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    first, second = sine_wav(audio_dir / "a.wav"), sine_wav(audio_dir / "b.wav", seconds=1)
    reference = [{"speaker": "A", "start": 0, "end": 1, "text": "가"}]
    manifest = write_samples(tmp_path, [sample_entry("a", first), sample_entry("b", second)],
                             {"a.ref.json": reference, "b.ref.json": reference})
    first.write_bytes(first.read_bytes() + b"\0")  # 바뀐 음원
    second.unlink()
    monkeypatch.setattr(run_eval, "import_app", lambda *args: pytest.fail("must not run engines"))

    assert run_eval.main(["accuracy", "--audio-dir", str(audio_dir), "--manifest", str(manifest)]) == 1
    err = capsys.readouterr().err
    assert "nothing was run" in err and "a: sha256 mismatch" in err and "b: audio not found" in err
    assert run_eval.main(["check", "--audio-dir", str(audio_dir), "--manifest", str(manifest), "--only", "zzz"]) == 1


def test_accuracy_imports_no_database_or_redis():
    """평가 서버에는 DB가 없다. 평가 도구가 올리는 흐름은 DB·Redis 모듈과 설정 없이 import되어야 한다(별도 프로세스)."""
    code = ("import sys; sys.path.insert(0, %r); import run_eval; run_eval.import_app('mock', 'mock'); "
            "print(sorted(m for m in ('database', 'utils.redis_client', 'services.meeting_service') if m in sys.modules))") % str(EVAL_DIR)
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=EVAL_DIR.parent)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "[]"


# ==================== accuracy (mock 엔진) ====================

# stt/mock.py의 단어열과 diarization/mock.py의 구간에 맞춘 정답. 사전 치환("검색 개편" → "검색엔진 개편")을 거치면 똑같다.
MOCK_REFERENCE = [
    {"speaker": "A", "start": 0.0, "end": 3.4, "text": "안녕하세요. 오늘은 검색엔진 개편을 논의합니다."},
    {"speaker": "B", "start": 4.2, "end": 6.1, "text": "네, 일정부터 정하죠."},
]


def test_accuracy_runs_the_worker_pipeline_with_mock_engines(tmp_path, reload_config, capsys):
    reload_config(APP_PROFILE="airgap", STT_PROVIDER="mock", DIARIZER="mock")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    good = sine_wav(audio_dir / "good.wav")
    broken = audio_dir / "broken.wav"
    broken.write_bytes(b"not audio")  # SHA256은 맞지만 디코딩할 수 없다
    manifest = write_samples(tmp_path, [
        sample_entry("good", good, keywords=["검색엔진"], terms=["검색엔진", "일정"], corrections=[["검색 개편", "검색엔진 개편"]]),
        sample_entry("broken", broken),
    ], {"good.ref.json": MOCK_REFERENCE, "broken.ref.json": MOCK_REFERENCE})
    out = tmp_path / "result.json"

    code = run_eval.main(["accuracy", "--audio-dir", str(audio_dir), "--manifest", str(manifest), "--out", str(out)])

    assert code == 1  # 한 샘플이 실패했다
    result = json.loads(out.read_text())
    good_result, broken_result = result["samples"]
    assert broken_result["id"] == "broken" and broken_result["error"].startswith("AudioDecodeError")
    assert good_result["cer"]["cer"] == 0 and good_result["terms"]["rate"] == 1.0 and good_result["der"]["der"] == 0
    assert list(good_result["times"]["stages"]) == ["normalize", "stt", "diarize", "merge", "glossary"]
    assert good_result["engine"]["engine"] == "mock" and good_result["audio_sec"] == 2.0
    assert [h["text"] for h in good_result["hypothesis"]] == [s["text"] for s in MOCK_REFERENCE]
    assert result["summary"]["failed"] == ["broken"] and result["summary"]["samples"] == 1
    assert result["config"]["STT_PROVIDER"] == "mock"
    out_text = capsys.readouterr().out
    assert re.search(r"^good\s+0\.0\s+0\.000\s+2/2\s+0\.000\s+2/2", out_text, re.M) and "broken" in out_text


# ==================== e2e (가짜 API) ====================

class FakeApi:
    """배포한 앱의 API 흉내. 전사는 두 번째 조회에서, 요약은 none → running → done 순으로 끝난다."""

    def __init__(self, auto_summary=True):
        self.calls, self.auto_summary, self.meeting_polls, self.status_polls = [], auto_summary, 0, 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, code, body=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                server.calls.append((self.command, self.path, self.headers.get("Authorization"), body[:200]))
                path = self.path
                if path == "/api/auth/login":
                    return self._reply(200, {"access_token": "tok"})
                if self.headers.get("Authorization") != "Bearer tok":
                    return self._reply(401, {"detail": "no"})
                if self.command == "POST" and path.startswith("/api/meetings?title="):
                    return self._reply(200, {"id": "m1"})
                if self.command == "POST" and path == "/api/domains":
                    return self._reply(200, dict(json.loads(body), id=5))
                if self.command == "PUT" and path == "/api/meetings/m1/domain?domain_id=5":
                    return self._reply(200, {"id": "m1"})
                if path == "/api/meetings/m1/upload-audio":
                    return self._reply(202, {"status": "processing", "segments": [], "meeting_id": "m1"})
                if self.command == "GET" and path == "/api/meetings/m1":
                    server.meeting_polls += 1
                    done = server.meeting_polls >= 2
                    transcript = [{"speaker_index": i, "speaker_name": f"화자{i + 1}", "text": s["text"],
                                   "start": int(s["start"] * 1000), "end": int(s["end"] * 1000)} for i, s in enumerate(MOCK_REFERENCE)]
                    return self._reply(200, {"id": "m1", "transcription_status": "done" if done else "processing",
                                             "transcript": transcript if done else []})
                if path == "/api/summary/m1/status":
                    server.status_polls += 1
                    order = ["none", "running", "done"] if server.auto_summary else ["none"]
                    return self._reply(200, {"status": order[min(server.status_polls - 1, len(order) - 1)], "error": None})
                if self.command == "DELETE":
                    return self._reply(200, {"ok": True})
                return self._reply(404, {"detail": path})

            do_GET = do_POST = do_PUT = do_DELETE = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def e2e_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_EMAIL", "eval@example.com")
    monkeypatch.setenv("EVAL_PASSWORD", "pw")
    monkeypatch.setattr(run_eval, "SUMMARY_START_GRACE", 0.2)
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio = sine_wav(audio_dir / "good.wav")
    manifest = write_samples(tmp_path, [sample_entry("good", audio, keywords=["검색엔진"], terms=["검색엔진"])],
                             {"good.ref.json": MOCK_REFERENCE})
    return run_eval.load_manifest(manifest), audio_dir


def test_e2e_times_upload_to_saved_summary_and_cleans_up(e2e_setup):
    samples, audio_dir = e2e_setup
    api = FakeApi()
    try:
        (result,) = run_eval.run_e2e(samples, audio_dir, run_eval.Api(api.url, insecure=False), timeout=10, keep=False, interval=0.01)
    finally:
        api.httpd.shutdown()

    assert result["summary_status"] == "done" and result["cer"]["cer"] == 0 and result["der"]["der"] == 0
    assert set(result["times"]) == {"upload_sec", "transcribe_sec", "summary_sec", "total_sec"}
    assert result["times"]["total_sec"] >= result["times"]["transcribe_sec"]
    methods = [(m, p.split("?")[0]) for m, p, _, _ in api.calls]
    assert ("POST", "/api/domains") in methods and ("PUT", "/api/meetings/m1/domain") in methods
    domain_body = next(b for m, p, _, b in api.calls if p == "/api/domains")
    assert json.loads(domain_body)["keywords"] == ["검색엔진"]
    assert methods[-2:] == [("DELETE", "/api/meetings/m1"), ("DELETE", "/api/domains/5")]
    upload = next(b for m, p, _, b in api.calls if p.endswith("/upload-audio"))
    assert b'name="file"; filename="good.wav"' in upload


def test_e2e_without_auto_summary_stops_after_the_grace_period(e2e_setup, capsys):
    samples, audio_dir = e2e_setup
    api = FakeApi(auto_summary=False)
    try:
        (result,) = run_eval.run_e2e(samples, audio_dir, run_eval.Api(api.url, insecure=False), timeout=10, keep=True, interval=0.01)
    finally:
        api.httpd.shutdown()
    assert result["summary_status"] == "none" and "does not include a saved summary" in capsys.readouterr().out
    assert ("DELETE", "/api/meetings/m1") not in [(m, p) for m, p, _, _ in api.calls]  # --keep


def test_e2e_needs_credentials(e2e_setup, monkeypatch):
    samples, audio_dir = e2e_setup
    monkeypatch.delenv("EVAL_PASSWORD")
    with pytest.raises(run_eval.EvalError, match="EVAL_EMAIL and EVAL_PASSWORD"):
        run_eval.run_e2e(samples, audio_dir, run_eval.Api("http://127.0.0.1:9", insecure=False), timeout=1, keep=False)


# ==================== -m slow: 실제 엔진 리허설 ====================

DIALOG = [
    ("slt", "Good morning everyone. Today we will review the search engine migration plan and the schedule."),
    ("kal", "Thanks. The index rebuild finished last night, and the latency numbers look much better than before."),
    ("awb", "I still have concerns about the vector database costs. Can we compare the two vendors before Friday?"),
    ("slt", "Sure. Let us collect the numbers first and then decide on the vendor next week."),
]
PAUSE = 0.8


def synthesize_dialog(directory: Path):
    """DIALOG를 이어 붙인 WAV와 정답 전사(차례마다 말한 구간, 초)를 만든다."""
    parts, reference, start = [], [], 0.0
    for i, (voice, text) in enumerate(DIALOG):
        part = directory / f"part{i}.wav"
        completed = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"flite=text='{text}':voice={voice}",
             "-ar", "16000", "-ac", "1", str(part)], capture_output=True)
        if completed.returncode != 0:
            pytest.skip("local ffmpeg has no flite source")
        with wave.open(str(part), "rb") as w:
            length = w.getnframes() / w.getframerate()
        reference.append({"speaker": voice, "start": round(start, 3), "end": round(start + length, 3), "text": text})
        parts.append(part)
        start += length + PAUSE
    dialog = directory / "dialog.wav"
    filters = "".join(f"[{i}:a]apad=pad_dur={PAUSE}[p{i}];" for i in range(len(parts)))
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *[a for p in parts for a in ("-i", str(p))],
                    "-filter_complex", filters + "".join(f"[p{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1",
                    "-ar", "16000", "-ac", "1", str(dialog)], check=True, capture_output=True)
    for part in parts:
        part.unlink()
    return dialog, reference


@pytest.fixture
def no_network(monkeypatch):
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(args)
        raise OSError("network is blocked in this test")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    return attempts


@pytest.mark.slow
def test_rehearsal_with_real_engines(tmp_path, reload_config, no_network):
    """샘플 1건 리허설(01-plan PR-18): faster-whisper + pyannote(CPU)로 합성 3화자 대화를 평가한다."""
    pytest.importorskip("faster_whisper")
    pytest.importorskip("pyannote.audio")
    whisper, pipeline = os.environ.get("WHISPER_TEST_MODEL_PATH"), os.environ.get("PYANNOTE_TEST_PIPELINE_PATH")
    if not (whisper and Path(whisper).is_dir() and pipeline and Path(pipeline).is_dir()):
        pytest.skip("set WHISPER_TEST_MODEL_PATH and PYANNOTE_TEST_PIPELINE_PATH")
    reload_config(APP_PROFILE="airgap", STT_PROVIDER="faster_whisper", DIARIZER="pyannote", STT_DEVICE="cpu",
                  WHISPER_MODEL_PATH=whisper, PYANNOTE_PIPELINE_PATH=pipeline, HF_HUB_OFFLINE="1")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    dialog, reference = synthesize_dialog(audio_dir)
    manifest = write_samples(tmp_path, [dict(sample_entry("dialog", dialog, keywords=["search engine", "vector database"],
                                                          terms=["search engine", "vector database", "latency"]),
                                             language="en-US", speakers=3, duration_sec=reference[-1]["end"])],
                             {"dialog.ref.json": reference})
    out = tmp_path / "result.json"

    assert run_eval.main(["accuracy", "--audio-dir", str(audio_dir), "--manifest", str(manifest), "--out", str(out)]) == 0

    result = json.loads(out.read_text())["samples"][0]
    assert no_network == []
    assert result["cer"]["cer"] < 0.15 and result["terms"]["rate"] >= 2 / 3
    assert result["der"]["der"] < 0.25 and result["der"]["hyp_speakers"] == 3
    assert {"stt", "diarize"} <= set(result["times"]["stages"]) and result["engine"]["engine"] == "faster_whisper"
