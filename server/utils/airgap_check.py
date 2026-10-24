"""airgap 기동 검사 (01-plan 3.1, 3.8). AIRGAP_STRICT=1이면 API·워커가 뜰 때 외부 호출 0 조건을 확인한다.

하나라도 어기면 AirgapRuntimeError로 기동을 멈춘다. 문제는 모아서 한 번에 알린다(설치하는 사람이 한 번에 고치게).
- 오프라인 env: HF_HUB_OFFLINE, TRANSFORMERS_OFFLINE, HF_HUB_DISABLE_TELEMETRY = 1, PYANNOTE_METRICS_ENABLED = 0
- LangSmith 추적(LANGCHAIN_TRACING_V2, LANGSMITH_TRACING)이 꺼져 있다
- 가입 이메일의 DNS 조회(EMAIL_DELIVERABILITY_CHECK)가 꺼져 있다
- LLM_BASE_URL(필수)과 SMTP_HOST가 내부 호스트다: 사설·루프백 IP, 점 없는 이름(compose 서비스), 내부용 도메인, AIRGAP_INTERNAL_DOMAINS
- 워커: 고른 엔진의 모델이 로컬 디렉터리에 있다(faster-whisper, pyannote). mock 엔진은 모델이 필요 없다
vLLM의 usage stats는 vLLM 컨테이너의 env라 여기서 볼 수 없다. docker-compose.airgap.yaml에서 끈다.
"""
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse

from utils import config

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "PYANNOTE_METRICS_ENABLED": "0",
}
TRACING_ENV = ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING")
INTERNAL_SUFFIXES = ("internal", "local", "lan", "intranet", "home.arpa")


class AirgapRuntimeError(RuntimeError):
    """airgap 기동 조건을 어겼다. 메시지에 고칠 것을 모두 담는다."""


def is_internal_host(host: str, extra_domains: tuple[str, ...] = ()) -> bool:
    host = (host or "").strip().lower().rstrip(".").strip("[]")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        return ip.is_private or ip.is_loopback or ip.is_link_local
    if "." not in host:
        return True  # compose 서비스 이름(llm, db 등)이나 사내 단일 이름
    return any(host == d or host.endswith("." + d) for d in INTERNAL_SUFFIXES + tuple(extra_domains))


def _files_missing(directory: str, required: tuple[str, ...]) -> list[str]:
    path = Path(directory)
    if not path.is_dir():
        return ["(directory)"]
    return [name for name in required if not (path / name).is_file()]


def find_problems(role: str) -> list[str]:
    """role은 api 또는 worker. 어긴 조건의 설명 목록을 돌려준다(없으면 빈 목록)."""
    problems = []
    for name, expected in OFFLINE_ENV.items():
        if os.environ.get(name) != expected:
            problems.append(f"{name} must be {expected} (got {os.environ.get(name)!r})")
    for name in TRACING_ENV:
        if (os.environ.get(name) or "false").strip().lower() not in ("", "false", "0"):
            problems.append(f"{name} must be unset (LangSmith tracing sends data out)")
    if config.EMAIL_DELIVERABILITY_CHECK:
        problems.append("EMAIL_DELIVERABILITY_CHECK must be false (it looks up email domains in external DNS)")

    if not (config.LLM_BASE_URL and config.LLM_MODEL and config.LLM_API_KEY):
        problems.append("LLM_BASE_URL, LLM_MODEL and LLM_API_KEY must all be set")
    elif not is_internal_host(urlparse(config.LLM_BASE_URL).hostname or "", config.AIRGAP_INTERNAL_DOMAINS):
        problems.append(f"LLM_BASE_URL host is not internal: {config.LLM_BASE_URL} (add the domain to AIRGAP_INTERNAL_DOMAINS if it is)")
    if config.SMTP_HOST and not is_internal_host(config.SMTP_HOST, config.AIRGAP_INTERNAL_DOMAINS):
        problems.append(f"SMTP_HOST is not internal: {config.SMTP_HOST} (add the domain to AIRGAP_INTERNAL_DOMAINS if it is)")

    if role == "worker":
        if config.STT_PROVIDER == "faster_whisper":
            from stt.faster_whisper import REQUIRED_MODEL_FILES

            missing = _files_missing(config.WHISPER_MODEL_PATH, REQUIRED_MODEL_FILES)
            if missing:
                problems.append(f"WHISPER_MODEL_PATH={config.WHISPER_MODEL_PATH} is missing {', '.join(missing)}")
        if config.DIARIZER == "pyannote":
            from diarization.pyannote import REQUIRED_PIPELINE_FILES

            missing = _files_missing(config.PYANNOTE_PIPELINE_PATH, REQUIRED_PIPELINE_FILES)
            if missing:
                problems.append(f"PYANNOTE_PIPELINE_PATH={config.PYANNOTE_PIPELINE_PATH} is missing {', '.join(missing)}")
    return problems


def enforce_airgap_runtime(role: str) -> None:
    """airgap이고 AIRGAP_STRICT면 검사하고, 어긴 조건이 있으면 기동을 멈춘다. public이나 AIRGAP_STRICT=0이면 아무것도 하지 않는다."""
    if config.APP_PROFILE != "airgap":
        return
    if not config.AIRGAP_STRICT:
        print(f"[airgap] AIRGAP_STRICT=0: skipping startup checks for {role} (development only)")
        return
    if not config.SMTP_HOST:
        print("[airgap] SMTP_HOST is not set: sign-up codes, password resets and meeting-notes mail will fail")
    problems = find_problems(role)
    if problems:
        raise AirgapRuntimeError(f"airgap startup checks failed for {role}:\n" + "\n".join(f"  - {p}" for p in problems))
    print(f"[airgap] startup checks passed for {role}")
