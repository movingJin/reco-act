"""LLM 접근 추상화 (01-plan 3.7, PR-3).

127.0.0.1에 가짜 OpenAI 호환 서버를 띄우고, 요약 워크플로가 보내는 실제 HTTP 요청 본문을 확인한다. 외부로 나가지 않는다.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from graph import summary_workflow
from graph.summary_workflow import SummaryNode
from utils.config import parse_llm_serving
from synthetic_meeting import make_synthetic_meeting

# 요약 워크플로의 노드 세 개가 각각 받는 구조화 출력. 요청한 스키마의 필드로 구분해 돌려준다.
CANNED_OUTPUTS = {
    "paragraphs": {"paragraphs": [{"subject": "검색 개편", "start": 0, "end": 76000, "summary": "일정과 역할을 정함."}]},
    "next_steps": {"subject": "문서 검색 개편", "next_steps": ["1. 테스트 문서 준비."]},
    "meeting_notes": {"meeting_notes": "1. 회의 개요\n• 주제: 문서 검색 개편"},
}

# airgap에서만 보내는 thinking 비활성화 인자
AIRGAP_ONLY_KEYS = {"chat_template_kwargs", "reasoning", "provider"}


class FakeLLMServer:
    def __init__(self):
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append({
                    "path": self.path.split("?")[0],
                    "authorization": self.headers.get("Authorization"),
                    "api_key": self.headers.get("api-key"),
                    "body": body,
                })
                properties = body["response_format"]["json_schema"]["schema"]["properties"]
                output = next(value for key, value in CANNED_OUTPUTS.items() if key in properties)
                payload = json.dumps({
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body.get("model") or "test",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
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
def fake_llm_server():
    server = FakeLLMServer()
    yield server
    server.close()


@pytest.fixture
def synthetic_meeting(monkeypatch):
    meeting = make_synthetic_meeting()
    monkeypatch.setattr(summary_workflow, "load_meeting", lambda meeting_id: meeting)
    return meeting


def airgap_env(server, **overrides):
    env = {
        "APP_PROFILE": "airgap",
        "LLM_BASE_URL": f"{server.url}/v1",
        "LLM_MODEL": "Qwen/Qwen3-32B-AWQ",
        "LLM_API_KEY": "test-llm-key",
    }
    env.update(overrides)
    return env


def run_summary(meeting):
    result = SummaryNode().run(meeting.id)
    assert result is not None  # run()은 LLM·파싱 오류를 잡아 None을 돌려준다
    assert result.paragraphs == CANNED_OUTPUTS["paragraphs"]["paragraphs"]
    assert (result.subject, result.next_steps) == ("문서 검색 개편", ["1. 테스트 문서 준비."])
    assert result.meeting_notes == CANNED_OUTPUTS["meeting_notes"]["meeting_notes"]


# ==================== public (현행 Azure 그대로) ====================

def test_public_uses_azure_and_ignores_llm_env(fake_llm_server, synthetic_meeting, reload_config):
    cfg = reload_config(
        AZURE_OPENAI_ENDPOINT=fake_llm_server.url,
        AZURE_OPENAI_API_KEY="test-azure-key",
        AZURE_OPENAI_DEPLOYMENT_NAME="test-deployment",
        **{k: v for k, v in airgap_env(fake_llm_server).items() if k != "APP_PROFILE"},
    )
    assert cfg.APP_PROFILE == "public"
    assert type(cfg.get_llm(0)) is AzureChatOpenAI

    run_summary(synthetic_meeting)

    assert len(fake_llm_server.requests) == 3
    for request in fake_llm_server.requests:
        assert request["path"] == "/openai/deployments/test-deployment/chat/completions"
        assert request["api_key"] == "test-azure-key"
        assert request["body"]["temperature"] == 0
        assert request["body"]["response_format"]["type"] == "json_schema"
        assert AIRGAP_ONLY_KEYS.isdisjoint(request["body"])


# ==================== airgap ====================

def test_airgap_vllm_request(fake_llm_server, synthetic_meeting, reload_config):
    cfg = reload_config(**airgap_env(fake_llm_server))
    assert cfg.LLM_SERVING == "vllm"  # 기본값
    llm = cfg.get_llm(0)
    assert type(llm) is ChatOpenAI

    run_summary(synthetic_meeting)

    assert len(fake_llm_server.requests) == 3
    for request in fake_llm_server.requests:
        body = request["body"]
        assert request["path"] == "/v1/chat/completions"
        assert request["authorization"] == "Bearer test-llm-key"
        assert (body["model"], body["temperature"]) == ("Qwen/Qwen3-32B-AWQ", 0)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "reasoning" not in body and "provider" not in body
        # 구조화 출력은 with_structured_output 기본(json_schema, strict)을 그대로 쓴다
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
    # airgap 기본 요약은 map_reduce: 짧은 회의는 청크 하나라 요청 수는 같고, 전사는 압축 포맷으로 간다(PR-12)
    paragraph_prompt = fake_llm_server.requests[0]["body"]["messages"][1]["content"]
    assert "[0-7000] 화자1: 오늘은 사내 문서 검색 시스템 개편 일정과 역할을 정하려고 합니다." in paragraph_prompt
    assert "TranscriptSegmentResponse" not in paragraph_prompt


def test_airgap_openrouter_request(fake_llm_server, synthetic_meeting, reload_config):
    reload_config(**airgap_env(fake_llm_server, LLM_SERVING="openrouter", LLM_MODEL="qwen/qwen3-32b"))

    run_summary(synthetic_meeting)

    assert len(fake_llm_server.requests) == 3
    for request in fake_llm_server.requests:
        body = request["body"]
        # 본문 최상위 reasoning 키가 있어도 Responses API(/responses)로 바뀌지 않아야 한다
        assert request["path"] == "/v1/chat/completions"
        assert body["reasoning"] == {"enabled": False}
        assert body["provider"] == {"require_parameters": True}
        assert "chat_template_kwargs" not in body
        assert body["response_format"]["type"] == "json_schema"


def test_airgap_missing_settings(reload_config):
    cfg = reload_config(APP_PROFILE="airgap", LLM_API_KEY="secret-value")
    with pytest.raises(ValueError) as excinfo:
        cfg.get_llm(0)
    message = str(excinfo.value)
    assert "LLM_BASE_URL" in message and "LLM_MODEL" in message
    assert "LLM_API_KEY" not in message and "secret-value" not in message


# ==================== LLM_SERVING ====================

def test_serving_unset_is_vllm():
    assert parse_llm_serving(None) == "vllm"


@pytest.mark.parametrize("value", ["vllm", "openrouter"])
def test_valid_servings(value):
    assert parse_llm_serving(value) == value


@pytest.mark.parametrize("value", ["", "VLLM", " vllm", "ollama", "azure"])
def test_invalid_servings_are_rejected(value):
    with pytest.raises(ValueError, match="Invalid LLM_SERVING"):
        parse_llm_serving(value)


def test_invalid_serving_stops_import(reload_config):
    with pytest.raises(ValueError, match="Invalid LLM_SERVING"):
        reload_config(LLM_SERVING="ollama")
