#!/usr/bin/env python3
"""STT 엔진 평가 도구 (00-decisions 4·5장 PoC 게이트, 01-plan PR-18).

샘플은 eval/samples/manifest.json과 정답 전사(eval/samples/*.ref.json)로 정의한다. 음원은 git에 넣지 않고
평가를 돌리는 서버의 디스크(--audio-dir)에 둔다. 실행 전에 음원이 모두 있고 SHA256이 맞는지 확인하고, 하나라도 다르면 멈춘다.

모드
  check     음원과 정답 전사만 확인한다.
  accuracy  엔진을 이 프로세스에서 워커와 같은 흐름(pipeline/recognize.py)으로 돌린다. DB·워커·LLM 요약은 쓰지 않는다.
            엔진 설정은 워커와 같은 env(WHISPER_MODEL_PATH, PYANNOTE_PIPELINE_PATH, STT_DEVICE, STT_COMPUTE_TYPE ...)로 준다.
            지표: CER, 용어 적중률, DER, 단계별 시간(normalize, stt, diarize, merge, glossary, [correct]).
            첫 샘플의 stt·diarize에는 모델을 올리는 시간이 들어간다.
  e2e       배포한 시스템의 API로 업로드 → 전사 → 자동 요약 저장까지 잰다(5장 처리 시간 기준: 업로드 완료부터 요약 저장까지).
            같은 지표를 API가 돌려준 전사로 계산한다. 샘플의 keywords는 임시 도메인으로 넘기고, 사전 치환(corrections)은 쓰지 않는다
            (배포한 DB의 사전을 쓰려면 그 도메인에 미리 넣는다). 계정은 env EVAL_EMAIL, EVAL_PASSWORD.

사용(레포 루트, airgap 엔진이 설치된 server venv)
  server/.venv/bin/python eval/run_eval.py check --audio-dir /data/eval-audio
  server/.venv/bin/python eval/run_eval.py accuracy --audio-dir /data/eval-audio [--stt faster_whisper|qwen3_asr] [--correction-llm]
  EVAL_EMAIL=... EVAL_PASSWORD=... server/.venv/bin/python eval/run_eval.py e2e --audio-dir /data/eval-audio --url https://<서버> [--insecure]
결과는 --out(기본 eval/results/<시각>-<모드>.json, git 밖)에 인식 결과와 함께 남긴다.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import metrics

EVAL_DIR = Path(__file__).resolve().parent
SERVER_DIR = EVAL_DIR.parent / "server"
MANIFEST = EVAL_DIR / "samples" / "manifest.json"
GATE_MINUTES_PER_HOUR = 15  # 1시간 회의를 15분 안에(00-decisions 5장)
CONFIG_ENV = ("APP_PROFILE", "STT_PROVIDER", "DIARIZER", "WHISPER_MODEL_PATH", "PYANNOTE_PIPELINE_PATH",
              "QWEN3_ASR_MODEL_PATH", "STT_DEVICE", "STT_COMPUTE_TYPE", "TERM_CORRECTION_LLM", "LLM_SERVING", "LLM_MODEL")


class EvalError(RuntimeError):
    """평가를 시작할 수 없다. 메시지에 고칠 것을 모두 담는다."""


@dataclass
class Sample:
    id: str
    audio: str
    sha256: str
    duration_sec: float
    speakers: int
    language: str
    reference: list  # [{"speaker", "start", "end", "text"}], 초
    keywords: list = field(default_factory=list)
    terms: list = field(default_factory=list)
    corrections: list = field(default_factory=list)  # [(오인식 표현, 정식 표기)]
    notes: str = ""


# ==================== 샘플 ====================

def load_manifest(path: Path = MANIFEST) -> list:
    """manifest와 정답 전사를 읽고 검사한다. 문제를 모두 모아 EvalError로 알린다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("samples"), list):
        raise EvalError(f"{path}: need {{\"version\": 1, \"samples\": [...]}}")
    problems, samples, seen = [], [], set()
    for index, raw in enumerate(data["samples"]):
        where = f"samples[{index}] {raw.get('id', '?')}"
        missing = [k for k in ("id", "audio", "sha256", "duration_sec", "speakers", "language", "reference") if k not in raw]
        if missing:
            problems.append(f"{where}: missing {missing}")
            continue
        if not re.fullmatch(r"[A-Za-z0-9._-]+", raw["id"]) or raw["id"] in seen:
            problems.append(f"{where}: id must be unique and use only letters, digits, . _ -")
        seen.add(raw["id"])
        if "/" in raw["audio"] or "\\" in raw["audio"] or raw["audio"].startswith("."):
            problems.append(f"{where}: audio must be a file name in --audio-dir, got {raw['audio']!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(raw["sha256"])):
            problems.append(f"{where}: sha256 must be 64 lowercase hex digits")
        corrections = raw.get("corrections", [])
        if not all(isinstance(c, list) and len(c) == 2 and all(isinstance(x, str) and x for x in c) for c in corrections):
            problems.append(f"{where}: corrections must be [[wrong, correct], ...]")
        reference_path = path.parent / str(raw["reference"])
        try:
            reference = json.loads(reference_path.read_text(encoding="utf-8"))["segments"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(f"{where}: cannot read reference {reference_path.name}: {exc}")
            continue
        for n, seg in enumerate(reference):
            if not (isinstance(seg, dict) and isinstance(seg.get("text"), str) and "speaker" in seg
                    and isinstance(seg.get("start"), (int, float)) and isinstance(seg.get("end"), (int, float))
                    and 0 <= seg["start"] < seg["end"]):
                problems.append(f"{where}: reference segment {n} needs speaker, text and 0 <= start < end (seconds)")
                break
        samples.append(Sample(
            raw["id"], raw["audio"], raw["sha256"], float(raw["duration_sec"]), int(raw["speakers"]), raw["language"],
            sorted(reference, key=lambda s: s.get("start", 0)) if isinstance(reference, list) else [],
            list(raw.get("keywords", [])), list(raw.get("terms", [])), [tuple(c) for c in corrections], raw.get("notes", "")))
    if problems:
        raise EvalError("manifest problems:\n  " + "\n  ".join(problems))
    return samples


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_audio(samples, audio_dir: Path) -> None:
    """음원이 모두 있고 SHA256이 맞아야 한다(00-decisions 4장). 하나라도 다르면 아무것도 돌리지 않고 멈춘다."""
    problems = []
    for sample in samples:
        path = audio_dir / sample.audio
        if not path.is_file():
            problems.append(f"{sample.id}: audio not found: {path}")
        elif sha256_file(path) != sample.sha256:
            problems.append(f"{sample.id}: sha256 mismatch: {path}")
    if problems:
        raise EvalError("audio check failed, nothing was run:\n  " + "\n  ".join(problems))


# ==================== 지표 ====================

def score(sample, hypothesis: list, duration: float) -> dict:
    """hypothesis: [{"speaker", "start", "end", "text"}] (초). duration은 음원 길이(초, DER 평가 구간)."""
    hypothesis = sorted(hypothesis, key=lambda s: s["start"])
    ref_text = " ".join(s["text"] for s in sample.reference)
    hyp_text = " ".join(s["text"] for s in hypothesis)
    return {
        "cer": metrics.cer(ref_text, hyp_text),
        "terms": metrics.term_hits(ref_text, hyp_text, sample.terms),
        "der": metrics.der(sample.reference, hypothesis, duration),
    }


def summarize(results: list) -> dict:
    failed = [r["id"] for r in results if "error" in r]
    results = [r for r in results if "error" not in r]
    ref_chars = sum(r["cer"]["ref_chars"] for r in results)
    expected = sum(r["terms"]["expected"] for r in results)
    der_total = sum(r["der"]["total"] for r in results)
    audio_sec = sum(r["audio_sec"] for r in results)
    total_sec = sum(r["times"]["total_sec"] for r in results)
    return {
        "samples": len(results),
        "failed": failed,
        "cer": sum(r["cer"]["errors"] for r in results) / ref_chars if ref_chars else None,
        "term_hit_rate": sum(r["terms"]["hits"] for r in results) / expected if expected else None,
        "der": sum(r["der"]["der"] * r["der"]["total"] for r in results) / der_total if der_total else None,
        "audio_sec": audio_sec,
        "total_sec": total_sec,
        "minutes_per_audio_hour": total_sec / audio_sec * 60 if audio_sec else None,
    }


# ==================== accuracy ====================

class StageTimer:
    def __init__(self):
        self.durations, self._name, self._start, self._t0 = {}, None, None, time.perf_counter()

    def stage(self, name: str) -> None:
        now = time.perf_counter()
        if self._name:
            self.durations[self._name] = round(now - self._start, 3)
        self._name, self._start = name, now

    def stop(self) -> float:
        self.stage("")
        return round(time.perf_counter() - self._t0, 3)


def import_app(stt=None, diarizer=None, correction_llm=False):
    """앱 모듈을 이 프로세스에 올린다. server/.env는 읽지 않는다(엔진 설정은 셸 env와 옵션으로만)."""
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False
    os.environ.setdefault("APP_PROFILE", "airgap")
    os.environ.setdefault("DIARIZER", "pyannote")
    for key, value in (("STT_PROVIDER", stt), ("DIARIZER", diarizer), ("TERM_CORRECTION_LLM", "true" if correction_llm else None)):
        if value:
            os.environ[key] = value
    if str(SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(SERVER_DIR))
    from pipeline.audio import normalize_audio
    from pipeline.recognize import recognize_audio

    return normalize_audio, recognize_audio


def run_accuracy(samples, audio_dir: Path, normalize_audio, recognize_audio) -> list:
    results = []
    with tempfile.TemporaryDirectory(prefix="reco-act-eval-") as tmp:
        for sample in samples:
            print(f"[eval] {sample.id}: running", flush=True)
            wav = Path(tmp) / f"{sample.id}.wav"
            try:
                timer = StageTimer()
                timer.stage("normalize")
                duration_ms = normalize_audio(str(audio_dir / sample.audio), str(wav))
                segments, _, record = recognize_audio(str(wav), language=sample.language, keywords=sample.keywords,
                                                      corrections=lambda s=sample: list(s.corrections), stage=timer.stage)
                total = timer.stop()
                hypothesis = [{"speaker": seg.speaker_index, "start": seg.start / 1000, "end": seg.end / 1000, "text": seg.text}
                              for seg in segments]
                results.append({"id": sample.id, "audio_sec": duration_ms / 1000, **score(sample, hypothesis, duration_ms / 1000),
                                "times": {"stages": timer.durations, "total_sec": total},
                                "engine": {k: (record or {}).get(k) for k in ("engine", "engine_version")},
                                "hypothesis": hypothesis})
            except Exception as exc:  # 한 샘플이 실패해도 나머지를 돌린다
                results.append(failure(sample, exc))
            finally:
                wav.unlink(missing_ok=True)
    return results


def failure(sample, exc) -> dict:
    print(f"[eval] {sample.id}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return {"id": sample.id, "error": f"{type(exc).__name__}: {exc}"}


# ==================== e2e ====================

class Api:
    def __init__(self, base_url: str, insecure: bool):
        import ssl
        import urllib.request

        self.base, self.token, self._request = base_url.rstrip("/"), None, urllib.request
        context = ssl._create_unverified_context() if insecure else None
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))

    def call(self, method, path, body=None, ctype="application/json"):
        import urllib.error

        data = json.dumps(body).encode() if ctype == "application/json" and body is not None else body
        request = self._request.Request(self.base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", ctype)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with self.opener.open(request, timeout=300) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise EvalError(f"{method} {path}: HTTP {exc.code} {exc.read()[:200]!r}") from None
        return json.loads(raw) if raw else None

    def upload(self, meeting_id, path: Path):
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        return self.call("POST", f"/api/meetings/{meeting_id}/upload-audio", body, f"multipart/form-data; boundary={boundary}")


SUMMARY_START_GRACE = 30  # 초. 워커는 전사를 저장한 뒤에 요약 작업을 넣으므로 그 사이에는 상태가 none이다(01-plan 4.3)


def wait_for(fetch, done, timeout: float, interval: float):
    deadline = time.monotonic() + timeout
    while True:
        value = fetch()
        if done(value):
            return value
        if time.monotonic() > deadline:
            raise EvalError(f"timed out after {timeout:.0f}s: {value}")
        time.sleep(interval)


def wait_for_summary(fetch, timeout: float, interval: float) -> str:
    """자동 요약이 끝나기를 기다려 마지막 상태를 돌려준다. 요약 작업이 생기지 않으면(SUMMARY_AUTO=false) none."""
    started = time.monotonic()

    def done(status):
        if status["status"] in ("done", "failed"):
            return True
        return status["status"] == "none" and time.monotonic() - started > SUMMARY_START_GRACE

    return wait_for(fetch, done, timeout, interval)["status"]


def run_e2e(samples, audio_dir: Path, api: Api, timeout: float, keep: bool, interval: float = 3.0) -> list:
    email, password = os.environ.get("EVAL_EMAIL"), os.environ.get("EVAL_PASSWORD")
    if not email or not password:
        raise EvalError("set EVAL_EMAIL and EVAL_PASSWORD for the e2e account")
    api.token = api.call("POST", "/api/auth/login", {"email": email, "password": password})["access_token"]
    run_id = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    results = []
    for sample in samples:
        print(f"[eval] {sample.id}: uploading", flush=True)
        meeting = api.call("POST", f"/api/meetings?title=eval-{run_id}-{sample.id}")
        domain = None
        try:
            if sample.keywords:
                domain = api.call("POST", "/api/domains", {"domain_name": f"eval-{run_id}-{sample.id}", "keywords": sample.keywords})
                api.call("PUT", f"/api/meetings/{meeting['id']}/domain?domain_id={domain['id']}")
            started = time.monotonic()
            api.upload(meeting["id"], audio_dir / sample.audio)
            uploaded = time.monotonic()  # 업로드 완료(202)부터 잰다
            meeting = wait_for(lambda: api.call("GET", f"/api/meetings/{meeting['id']}"),
                               lambda m: m.get("transcription_status") in ("done", "failed"), timeout, interval)
            transcribed = time.monotonic()
            if meeting["transcription_status"] != "done":
                raise EvalError(f"{sample.id}: transcription failed")
            summary_status = wait_for_summary(lambda: api.call("GET", f"/api/summary/{meeting['id']}/status"), timeout, interval)
            summarized = time.monotonic()
            hypothesis = [{"speaker": seg["speaker_index"], "start": seg["start"] / 1000, "end": seg["end"] / 1000,
                           "text": seg["text"]} for seg in meeting.get("transcript") or []]
            results.append({
                "id": sample.id, "audio_sec": sample.duration_sec, **score(sample, hypothesis, sample.duration_sec),
                "times": {"upload_sec": round(uploaded - started, 3), "transcribe_sec": round(transcribed - uploaded, 3),
                          "summary_sec": round(summarized - transcribed, 3), "total_sec": round(summarized - uploaded, 3)},
                "summary_status": summary_status, "hypothesis": hypothesis,
            })
            if summary_status != "done":
                print(f"[eval] {sample.id}: summary {summary_status}; total_sec does not include a saved summary", flush=True)
        except Exception as exc:  # 한 샘플이 실패해도 나머지를 돌린다
            results.append(failure(sample, exc))
        finally:
            cleanup = ([] if keep else [f"/api/meetings/{meeting['id']}"]) + ([f"/api/domains/{domain['id']}"] if domain else [])
            for path in cleanup:
                try:
                    api.call("DELETE", path)
                except EvalError as exc:
                    print(f"[eval] {sample.id}: cleanup failed, remove it by hand: {exc}", file=sys.stderr, flush=True)
    return results


# ==================== 보고 ====================

def fmt(value, pattern="{:.3f}"):
    return "-" if value is None else pattern.format(value)


def report(mode: str, results: list, summary: dict) -> None:
    print(f"\n{'id':<24} {'min':>6} {'CER':>7} {'terms':>9} {'DER':>7} {'spk':>5} {'sec':>8} {'min/h':>7}")
    for r in results:
        if "error" in r:
            print(f"{r['id']:<24} FAILED {r['error']}")
            continue
        terms = f"{r['terms']['hits']}/{r['terms']['expected']}"
        speakers = f"{r['der']['hyp_speakers']}/{r['der']['ref_speakers']}"
        per_hour = r["times"]["total_sec"] / r["audio_sec"] * 60 if r["audio_sec"] else None
        print(f"{r['id']:<24} {r['audio_sec'] / 60:>6.1f} {fmt(r['cer']['cer']):>7} {terms:>9} {fmt(r['der']['der']):>7} "
              f"{speakers:>5} {r['times']['total_sec']:>8.1f} {fmt(per_hour, '{:.1f}'):>7}")
    print(f"{'total':<24} {summary['audio_sec'] / 60:>6.1f} {fmt(summary['cer']):>7} {fmt(summary['term_hit_rate']):>9} "
          f"{fmt(summary['der']):>7} {'':>5} {summary['total_sec']:>8.1f} {fmt(summary['minutes_per_audio_hour'], '{:.1f}'):>7}")
    if mode == "e2e" and summary["minutes_per_audio_hour"] is not None:
        verdict = "within" if summary["minutes_per_audio_hour"] <= GATE_MINUTES_PER_HOUR else "OVER"
        print(f"processing time: {summary['minutes_per_audio_hour']:.1f} min per audio hour, {verdict} the "
              f"{GATE_MINUTES_PER_HOUR} min gate (judge on the 1-hour sample)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="reco-act STT evaluation (PoC gate)")
    parser.add_argument("mode", choices=("check", "accuracy", "e2e"))
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--only", default="", help="comma-separated sample ids")
    parser.add_argument("--out", type=Path, help="result JSON (default eval/results/<time>-<mode>.json)")
    parser.add_argument("--stt", help="accuracy: STT_PROVIDER (faster_whisper, qwen3_asr, mock)")
    parser.add_argument("--diarizer", help="accuracy: DIARIZER (pyannote, mock)")
    parser.add_argument("--correction-llm", action="store_true", help="accuracy: also run the LLM term correction (LLM_* env)")
    parser.add_argument("--url", help="e2e: base URL of the deployed web (https://...)")
    parser.add_argument("--insecure", action="store_true", help="e2e: accept a self-signed certificate")
    parser.add_argument("--timeout", type=float, default=3600, help="e2e: seconds to wait for each of transcription and summary")
    parser.add_argument("--keep", action="store_true", help="e2e: keep the uploaded meetings")
    args = parser.parse_args(argv)
    try:
        samples = load_manifest(args.manifest)
        if args.only:
            wanted = set(args.only.split(","))
            unknown = wanted - {s.id for s in samples}
            if unknown:
                raise EvalError(f"unknown sample ids: {sorted(unknown)}")
            samples = [s for s in samples if s.id in wanted]
        if not samples:
            raise EvalError(f"no samples in {args.manifest}")
        check_audio(samples, args.audio_dir)
        print(f"[eval] {len(samples)} samples, audio and references OK", flush=True)
        if args.mode == "check":
            return 0
        if args.mode == "accuracy":
            results = run_accuracy(samples, args.audio_dir, *import_app(args.stt, args.diarizer, args.correction_llm))
            config = {key: os.environ.get(key) for key in CONFIG_ENV}
        else:
            if not args.url:
                raise EvalError("e2e needs --url")
            results = run_e2e(samples, args.audio_dir, Api(args.url, args.insecure), args.timeout, args.keep)
            config = {"url": args.url}
    except EvalError as exc:
        print(f"[eval] {exc}", file=sys.stderr)
        return 1
    summary = summarize(results)
    report(args.mode, results, summary)
    out = args.out or EVAL_DIR / "results" / f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{args.mode}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mode": args.mode, "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
               "manifest": str(args.manifest), "config": config, "summary": summary, "samples": results}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[eval] results: {out}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
