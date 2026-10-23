import os, sys
from typing import Dict, Any, List, Optional
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings, ChatOpenAI

import logging

# 로그 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
inf = 9223372036854775807

load_dotenv()

# ==================== 프로파일 ====================
APP_PROFILES = ("public", "airgap")


def parse_app_profile(value: Optional[str]) -> str:
    """APP_PROFILE 값을 검증한다. 미설정이면 public(현재 운영 동작).

    그 외 값(빈 문자열, 대소문자·공백이 다른 값 포함)은 예외를 내서 기동을 멈춘다.
    오타나 빈 값 때문에 airgap 서버가 조용히 public(외부 API 호출)으로 뜨는 것을 막는다.
    """
    if value is None:
        return "public"
    if value not in APP_PROFILES:
        raise ValueError(f"Invalid APP_PROFILE={value!r}. Use one of: {', '.join(APP_PROFILES)}")
    return value


APP_PROFILE = parse_app_profile(os.getenv("APP_PROFILE"))


def parse_bool_env(name: str, value: Optional[str], default: bool) -> bool:
    """true/false 형식 env를 읽는다. 미설정이면 default, 알 수 없는 값이면 예외로 기동을 멈춘다."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    raise ValueError(f"Invalid {name}={value!r}. Use true or false")


ENVIRONMENT = os.getenv("ENVIRONMENT")  # 'local' or 'prod'

if ENVIRONMENT == "prod":
    # Docker 환경에서는 absolute path
    RECORDS_DIR = Path("/app/records")
else:
    # 로컬 환경에서는 프로젝트 상대경로
    RECORDS_DIR = Path(__file__).parent.parent / "records"

RECORDS_DIR.mkdir(parents=True, exist_ok=True)

logger.info(f"[CONFIG] App profile: {APP_PROFILE}")
logger.info(f"[CONFIG] Environment: {ENVIRONMENT}")
logger.info(f"[CONFIG] Records directory: {RECORDS_DIR}")

# ==================== STT ====================
# 엔진은 SttProvider(server/stt/) 뒤에 둔다(00-decisions 2.3). public은 Clova(현행)다.
STT_PROVIDERS = ("clova", "faster_whisper", "qwen3_asr", "mock")


def parse_stt_provider(value: Optional[str], app_profile: str) -> str:
    """STT_PROVIDER 값을 검증한다. 미설정이면 public은 clova(현행), airgap은 faster_whisper.

    airgap에서 clova(외부 API)를 고르면 예외로 기동을 멈춘다(00-decisions 3장).
    """
    if value is None:
        return "clova" if app_profile == "public" else "faster_whisper"
    if value not in STT_PROVIDERS:
        raise ValueError(f"Invalid STT_PROVIDER={value!r}. Use one of: {', '.join(STT_PROVIDERS)}")
    if value == "clova" and app_profile == "airgap":
        raise ValueError("STT_PROVIDER=clova calls an external API and cannot be used with APP_PROFILE=airgap")
    return value


STT_PROVIDER = parse_stt_provider(os.getenv("STT_PROVIDER"), APP_PROFILE)

# faster-whisper(airgap). 모델(CTranslate2 변환본)은 로컬 디렉터리에서만 읽는다(00-decisions 3장).
STT_DEVICES = ("cuda", "cpu")


def parse_stt_device(value: Optional[str]) -> str:
    """STT_DEVICE 값을 검증한다. 미설정이면 cuda(운영 GPU). GPU가 없는 개발 PC는 cpu."""
    if value is None:
        return "cuda"
    if value not in STT_DEVICES:
        raise ValueError(f"Invalid STT_DEVICE={value!r}. Use one of: {', '.join(STT_DEVICES)}")
    return value


WHISPER_MODEL_PATH = os.getenv("WHISPER_MODEL_PATH") or "/models/faster-whisper-large-v3"
STT_DEVICE = parse_stt_device(os.getenv("STT_DEVICE"))
# CTranslate2 계산 정밀도. 미설정이면 cuda는 float16, cpu는 int8. 지원하지 않는 값이면 모델을 올릴 때 실패한다.
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE") or ("float16" if STT_DEVICE == "cuda" else "int8")

# ==================== 화자분리 ====================
# 단어 타임스탬프만 주는 STT 엔진의 결과에 화자를 붙인다(01-plan 3.3). public은 Clova가 화자까지 주므로 쓰지 않는다.
DIARIZERS = ("pyannote", "mock")


def parse_diarizer(value: Optional[str], app_profile: str) -> Optional[str]:
    """DIARIZER 값을 검증한다. 미설정이면 public은 None(사용 안 함), airgap은 pyannote."""
    if value is None:
        return None if app_profile == "public" else "pyannote"
    if value not in DIARIZERS:
        raise ValueError(f"Invalid DIARIZER={value!r}. Use one of: {', '.join(DIARIZERS)}")
    return value


DIARIZER = parse_diarizer(os.getenv("DIARIZER"), APP_PROFILE)

# pyannote speaker-diarization-community-1 파이프라인 디렉터리(config.yaml + segmentation/embedding/plda). 로컬 경로만 쓴다.
# 장치는 STT와 같은 STT_DEVICE를 쓴다(GPU 1장).
PYANNOTE_PIPELINE_PATH = os.getenv("PYANNOTE_PIPELINE_PATH") or "/models/speaker-diarization-community-1"

# ==================== 작업 실행 (워커) ====================
# 전사·요약은 두 프로파일 모두 작업 큐 + 워커 프로세스(python -m worker)가 실행한다(00-decisions 2.1, 01-plan 3.5).


def parse_worker_concurrency(value: Optional[str], app_profile: str) -> int:
    """WORKER_CONCURRENCY(워커 프로세스 하나의 동시 작업 수)를 검증한다. 미설정이면 public 4, airgap 1.

    public은 외부 API(Clova, Azure) 응답을 기다리는 시간이 대부분이라 여러 건을 동시에 처리한다.
    airgap은 GPU 1장을 순차로 쓰므로 2 이상이면 예외로 기동을 멈춘다(00-decisions 2.1).
    """
    if value is None:
        return 4 if app_profile == "public" else 1
    try:
        concurrency = int(value)
    except ValueError:
        raise ValueError(f"Invalid WORKER_CONCURRENCY={value!r}. Use a positive integer")
    if concurrency < 1:
        raise ValueError(f"Invalid WORKER_CONCURRENCY={value!r}. Use a positive integer")
    if concurrency > 1 and app_profile == "airgap":
        raise ValueError("WORKER_CONCURRENCY must be 1 with APP_PROFILE=airgap (single GPU)")
    return concurrency


WORKER_CONCURRENCY = parse_worker_concurrency(os.getenv("WORKER_CONCURRENCY"), APP_PROFILE)

# 요약·회의록 재생성 요청이 작업 완료를 기다리는 최대 시간(초, 01-plan 3.7). 넘기면 오류 응답을 주고 작업은 계속 돈다.
# public은 nginx proxy_read_timeout(600s)보다 짧게 둔다.
SUMMARY_WAIT_TIMEOUT = float(os.getenv("SUMMARY_WAIT_TIMEOUT") or (540 if APP_PROFILE == "public" else 1500))

# 전사가 끝나면 요약 작업을 자동으로 넣을지(00-decisions 2.1, 두 프로파일 기본 true). 운영 중 끌 수 있는 스위치다(Azure 비용 등).
# 꺼도 사용자가 "AI요약"을 누르면 요약은 만들어진다.
SUMMARY_AUTO = parse_bool_env("SUMMARY_AUTO", os.getenv("SUMMARY_AUTO"), default=True)

# ==================== 메일(SMTP) ====================
# 회원가입 인증코드, 비밀번호 재설정, 회의록 메일이 모두 이 설정을 쓴다(00-decisions 2.7).
# public 기본값은 현행 Gmail(465, SSL)과 같다. airgap은 설치할 때 그 망의 사내 메일 서버를 지정한다.
SMTP_SECURITY_MODES = ("ssl", "starttls", "none")
SMTP_DEFAULT_PORTS = {"ssl": 465, "starttls": 587, "none": 25}


def parse_smtp_security(value: Optional[str]) -> str:
    """SMTP_SECURITY 값을 검증한다. 미설정이면 ssl(현행 Gmail 방식)."""
    if value is None:
        return "ssl"
    if value not in SMTP_SECURITY_MODES:
        raise ValueError(f"Invalid SMTP_SECURITY={value!r}. Use one of: {', '.join(SMTP_SECURITY_MODES)}")
    return value


# airgap에서 미설정이면 None으로 두고 발송을 실패시킨다(외부 Gmail로 나가지 않게).
SMTP_HOST = os.getenv("SMTP_HOST") or ("smtp.gmail.com" if APP_PROFILE == "public" else None)
SMTP_SECURITY = parse_smtp_security(os.getenv("SMTP_SECURITY"))
SMTP_PORT = int(os.getenv("SMTP_PORT") or SMTP_DEFAULT_PORTS[SMTP_SECURITY])
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")  # 비어 있으면 로그인을 건너뛴다(인증 없는 사내 릴레이)

# 가입 이메일 검증 때 DNS로 도메인의 메일 수신 가능 여부를 조회할지. airgap은 외부 DNS 조회를 끈다(01-plan R2).
EMAIL_DELIVERABILITY_CHECK = parse_bool_env(
    "EMAIL_DELIVERABILITY_CHECK", os.getenv("EMAIL_DELIVERABILITY_CHECK"), default=APP_PROFILE == "public"
)

# ==================== Azure OpenAI 설정 ====================
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
AZURE_OPENAI_EMBEDDING = os.getenv("AZURE_OPENAI_EMBEDDING")

# ==================== LLM (airgap) ====================
# airgap은 OpenAI 호환 서버에 붙는다. 운영은 vLLM, 개발은 OpenRouter(00-decisions 2.2). public은 위 Azure 설정을 그대로 쓴다.
LLM_SERVINGS = ("vllm", "openrouter")


def parse_llm_serving(value: Optional[str]) -> str:
    """LLM_SERVING 값을 검증한다. 미설정이면 vllm(airgap 운영)."""
    if value is None:
        return "vllm"
    if value not in LLM_SERVINGS:
        raise ValueError(f"Invalid LLM_SERVING={value!r}. Use one of: {', '.join(LLM_SERVINGS)}")
    return value


LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL")
LLM_API_KEY = os.getenv("LLM_API_KEY")  # 인증 없는 vLLM이면 아무 값(예: EMPTY)
LLM_SERVING = parse_llm_serving(os.getenv("LLM_SERVING"))

# Qwen3 thinking 모드를 끄는 요청 본문. 서빙마다 방식이 다르다(01-plan 3.7, 9장).
# ChatOpenAI의 reasoning 인자나 본문 최상위 "reasoning" 키는 Responses API(/responses)로 바꿔 보내므로 extra_body에만 넣는다.
LLM_THINKING_OFF_BODY = {
    # Qwen3 chat template 인자. vLLM은 요청의 chat_template_kwargs를 템플릿에 넘긴다. GPU 환경에서 실제 확인 전(9장).
    "vllm": {"chat_template_kwargs": {"enable_thinking": False}},
    # OpenRouter 통합 reasoning 인자. chat_template_kwargs와 reasoning.effort=none은 공급자가 무시했다(2026-10-14 확인).
    # require_parameters: 이 인자와 response_format을 지원하는 공급자로만 보낸다.
    "openrouter": {"reasoning": {"enabled": False}, "provider": {"require_parameters": True}},
}


def get_llm(temperature: float = 0.3):
    """요약에 쓰는 LLM. public은 Azure OpenAI(현행), airgap은 OpenAI 호환 서버."""
    if APP_PROFILE == "airgap":
        return _get_openai_compatible_llm(temperature)
    return AzureChatOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        azure_deployment=AZURE_OPENAI_DEPLOYMENT_NAME,
        api_version=AZURE_OPENAI_API_VERSION,
        temperature=temperature,
        # streaming=True
    )


def _get_openai_compatible_llm(temperature: float):
    missing = [
        name for name, value in (("LLM_BASE_URL", LLM_BASE_URL), ("LLM_MODEL", LLM_MODEL), ("LLM_API_KEY", LLM_API_KEY))
        if not value
    ]
    if missing:
        raise ValueError(f"airgap LLM settings are missing: {', '.join(missing)}")
    # 구조화 출력은 with_structured_output 기본(json_schema, strict)을 그대로 쓴다.
    # OpenRouter에서는 reasoning이 별도 필드로 와서 content에 <think>가 섞이지 않았다(2026-10-14 확인).
    return ChatOpenAI(
        base_url=LLM_BASE_URL,
        model=LLM_MODEL,
        api_key=LLM_API_KEY,
        temperature=temperature,
        extra_body=LLM_THINKING_OFF_BODY[LLM_SERVING],
    )


def get_embeddings():
    return AzureOpenAIEmbeddings(
        model=AZURE_OPENAI_EMBEDDING,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION
    )