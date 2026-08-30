import os, sys
from typing import Dict, Any, List
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

import logging

# 로그 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
inf = 9223372036854775807

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT")  # 'local' or 'prod'

if ENVIRONMENT == "prod":
    # Docker 환경에서는 absolute path
    RECORDS_DIR = Path("/app/records")
else:
    # 로컬 환경에서는 프로젝트 상대경로
    RECORDS_DIR = Path(__file__).parent.parent / "records"

RECORDS_DIR.mkdir(parents=True, exist_ok=True)

# 운영 배포 시의 공개 도메인(예: https://reco-act.movingjin.com). 설정돼 있으면
# Clova STT를 비동기(콜백) 방식으로 제출해 긴 녹음도 안정적으로 처리한다.
# 로컬 개발처럼 외부에서 접근 불가능한 환경에서는 비워두면 기존 sync 방식으로 동작한다.
PUBLIC_API_BASE_URL = os.getenv("PUBLIC_API_BASE_URL")

logger.info(f"[CONFIG] Environment: {ENVIRONMENT}")
logger.info(f"[CONFIG] Records directory: {RECORDS_DIR}")

# ==================== Azure OpenAI 설정 ====================
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
AZURE_OPENAI_EMBEDDING = os.getenv("AZURE_OPENAI_EMBEDDING")


def get_llm(temperature: float = 0.3):
    """Azure OpenAI LLM 인스턴스를 반환합니다."""
    return AzureChatOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        azure_deployment=AZURE_OPENAI_DEPLOYMENT_NAME,
        api_version=AZURE_OPENAI_API_VERSION,
        temperature=temperature,
        # streaming=True
    )


def get_embeddings():
    return AzureOpenAIEmbeddings(
        model=AZURE_OPENAI_EMBEDDING,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION
    )