"""airgap 기동 검사와 API 기동 구성 (01-plan 3.1, R12, PR-15)."""
import asyncio
import importlib

import httpx
import pytest

from diarization.pyannote import REQUIRED_PIPELINE_FILES
from stt.faster_whisper import REQUIRED_MODEL_FILES
from utils import airgap_check, config
from utils.airgap_check import AirgapRuntimeError, enforce_airgap_runtime, find_problems, is_internal_host

GOOD_ENV = {
    "APP_PROFILE": "airgap",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "PYANNOTE_METRICS_ENABLED": "0",
    "LLM_BASE_URL": "http://llm:8000/v1",
    "LLM_MODEL": "qwen3-32b",
    "LLM_API_KEY": "EMPTY",
    "SMTP_HOST": "10.0.0.25",
}


def make_dir(root, required):
    for name in required:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x")
    return str(root)


@pytest.fixture
def airgap(reload_config, tmp_path):
    """기동 조건을 모두 갖춘 airgap 설정. 테스트에서 하나씩 깨 본다."""
    whisper = make_dir(tmp_path / "whisper", REQUIRED_MODEL_FILES)
    pyannote = make_dir(tmp_path / "pyannote", REQUIRED_PIPELINE_FILES)
    return lambda **overrides: reload_config(
        **{**GOOD_ENV, "WHISPER_MODEL_PATH": whisper, "PYANNOTE_PIPELINE_PATH": pyannote, **overrides}
    )


@pytest.mark.parametrize("host, expected", [
    ("llm", True),  # compose 서비스 이름
    ("10.1.2.3", True), ("172.20.0.5", True), ("192.168.0.10", True), ("127.0.0.1", True), ("::1", True), ("fd00::1", True),
    ("mail.corp.internal", True), ("vllm.local", True), ("printer.home.arpa", True),
    ("openrouter.ai", False), ("smtp.gmail.com", False), ("8.8.8.8", False), ("internal.example.com", False), ("", False),
    ("mail.agency.go.kr", False),
])
def test_internal_host(host, expected):
    assert is_internal_host(host) is expected


def test_site_domains_can_be_declared_internal():
    assert is_internal_host("mail.agency.go.kr", ("agency.go.kr",))
    assert not is_internal_host("agency.go.kr.evil.com", ("agency.go.kr",))


def test_good_airgap_settings_pass(airgap):
    airgap()
    assert find_problems("api") == []
    assert find_problems("worker") == []
    enforce_airgap_runtime("worker")


@pytest.mark.parametrize("overrides, message", [
    ({"HF_HUB_OFFLINE": "0"}, "HF_HUB_OFFLINE must be 1"),
    ({"TRANSFORMERS_OFFLINE": ""}, "TRANSFORMERS_OFFLINE must be 1"),
    ({"HF_HUB_DISABLE_TELEMETRY": "true"}, "HF_HUB_DISABLE_TELEMETRY must be 1"),
    ({"PYANNOTE_METRICS_ENABLED": "1"}, "PYANNOTE_METRICS_ENABLED must be 0"),
    ({"LANGCHAIN_TRACING_V2": "true"}, "LANGCHAIN_TRACING_V2 must be unset"),
    ({"LANGSMITH_TRACING": "1"}, "LANGSMITH_TRACING must be unset"),
    ({"EMAIL_DELIVERABILITY_CHECK": "true"}, "EMAIL_DELIVERABILITY_CHECK must be false"),
    ({"LLM_BASE_URL": "https://openrouter.ai/api/v1"}, "LLM_BASE_URL host is not internal"),
    ({"LLM_API_KEY": ""}, "LLM_BASE_URL, LLM_MODEL and LLM_API_KEY must all be set"),
    ({"SMTP_HOST": "smtp.gmail.com"}, "SMTP_HOST is not internal"),
])
def test_each_violation_is_reported(airgap, overrides, message):
    airgap(**overrides)

    problems = find_problems("api")

    assert any(message in p for p in problems), problems
    with pytest.raises(AirgapRuntimeError, match=message):
        enforce_airgap_runtime("api")


def test_worker_checks_model_directories(airgap, tmp_path):
    airgap(WHISPER_MODEL_PATH=str(tmp_path / "none"), PYANNOTE_PIPELINE_PATH=str(tmp_path / "pyannote"))
    (tmp_path / "pyannote" / "plda" / "plda.npz").unlink()

    problems = find_problems("worker")

    assert any("WHISPER_MODEL_PATH" in p and "(directory)" in p for p in problems)
    assert any("PYANNOTE_PIPELINE_PATH" in p and "plda/plda.npz" in p for p in problems)
    assert find_problems("api") == []  # API는 모델을 마운트하지 않는다


def test_mock_engines_need_no_models(airgap, tmp_path):
    airgap(STT_PROVIDER="mock", DIARIZER="mock", WHISPER_MODEL_PATH=str(tmp_path / "none"), PYANNOTE_PIPELINE_PATH=str(tmp_path / "none"))
    assert find_problems("worker") == []


def test_all_problems_are_reported_at_once(airgap):
    airgap(HF_HUB_OFFLINE="0", LLM_BASE_URL="https://openrouter.ai/api/v1", SMTP_HOST="smtp.gmail.com")

    with pytest.raises(AirgapRuntimeError) as raised:
        enforce_airgap_runtime("api")

    assert str(raised.value).count("\n  - ") == 3


def test_internal_domains_setting(airgap):
    airgap(LLM_BASE_URL="http://vllm.agency.go.kr:8000/v1", AIRGAP_INTERNAL_DOMAINS="Agency.go.kr., other.kr")
    assert config.AIRGAP_INTERNAL_DOMAINS == ("agency.go.kr", "other.kr")
    assert find_problems("api") == []


def test_strict_off_skips_checks(airgap, capsys):
    airgap(AIRGAP_STRICT="0", LLM_BASE_URL="https://openrouter.ai/api/v1", HF_HUB_OFFLINE="0")
    enforce_airgap_runtime("worker")
    assert "AIRGAP_STRICT=0" in capsys.readouterr().out


def test_public_is_never_checked(monkeypatch):
    assert config.APP_PROFILE == "public"
    monkeypatch.setattr(airgap_check, "find_problems", lambda role: pytest.fail("public must not run airgap checks"))
    enforce_airgap_runtime("api")


# ==================== API 기동 구성 ====================

def get_statuses(app, *paths):
    """ASGI로 GET을 보낸다(lifespan을 돌리지 않아 startup의 DB 연결은 하지 않는다)."""
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return {path: await client.get(path) for path in paths}
    return asyncio.run(run())


@pytest.fixture
def reload_main():
    import main
    yield lambda: importlib.reload(main)
    importlib.reload(main)  # reload_config가 env를 되돌린 뒤 public 앱으로 다시 만든다


def test_public_keeps_swagger_and_has_health(reload_main):
    app = reload_main().app
    responses = get_statuses(app, "/docs", "/health")

    assert responses["/docs"].status_code == 200
    assert responses["/health"].json() == {"status": "ok"}  # 기존 엔드포인트(meeting_endpoints). airgap healthcheck가 쓴다


@pytest.fixture
def swagger_files(tmp_path):
    directory = tmp_path / "swagger-ui"
    directory.mkdir()
    for name in ("swagger-ui-bundle.js", "swagger-ui.css", "favicon-32x32.png"):
        (directory / name).write_text(f"/* {name} */")
    return directory


def test_airgap_serves_swagger_from_local_files(airgap, reload_main, swagger_files):
    """airgap Swagger UI는 CDN 대신 이미지에 넣은 파일을 쓴다(2026-10-24 결정). ReDoc은 띄우지 않는다."""
    airgap(SWAGGER_UI_DIR=str(swagger_files))
    responses = get_statuses(
        reload_main().app, "/docs", "/docs-static/swagger-ui-bundle.js", "/docs-static/swagger-ui.css", "/redoc", "/openapi.json", "/health"
    )

    html = responses["/docs"].text
    assert responses["/docs"].status_code == 200
    assert "/docs-static/swagger-ui-bundle.js" in html and "/docs-static/swagger-ui.css" in html and "/openapi.json" in html
    assert "cdn.jsdelivr.net" not in html and "https://" not in html.replace("https://fastapi.tiangolo.com", "")
    assert responses["/docs-static/swagger-ui-bundle.js"].text == "/* swagger-ui-bundle.js */"
    assert responses["/docs-static/swagger-ui.css"].status_code == 200
    assert responses["/redoc"].status_code == 404
    assert responses["/openapi.json"].status_code == 200
    assert responses["/health"].json() == {"status": "ok"}


def test_airgap_without_swagger_files_has_no_docs(airgap, reload_main, tmp_path, capsys):
    """파일이 없으면 CDN으로 대신하지 않고 /docs를 끈다."""
    airgap(SWAGGER_UI_DIR=str(tmp_path / "none"))
    responses = get_statuses(reload_main().app, "/docs")

    assert responses["/docs"].status_code == 404
    assert "Swagger UI files not found" in capsys.readouterr().out


def test_public_docs_and_schema_are_unchanged(reload_main):
    app = reload_main().app
    assert "/docs-static" not in [getattr(route, "path", "") for route in app.routes]
    assert "/docs" not in app.openapi()["paths"]


def test_api_startup_stops_on_violation(airgap, reload_main, monkeypatch):
    airgap(LLM_BASE_URL="https://openrouter.ai/api/v1")
    main = reload_main()
    monkeypatch.setattr(main, "init_db", lambda: pytest.fail("must stop before touching the DB"))

    with pytest.raises(AirgapRuntimeError, match="LLM_BASE_URL host is not internal"):
        main.startup_event()


def test_worker_main_stops_on_violation(airgap, monkeypatch):
    import worker

    airgap(HF_HUB_OFFLINE="0")
    monkeypatch.setattr(worker, "init_db", lambda: pytest.fail("must stop before touching the DB"))

    with pytest.raises(AirgapRuntimeError, match="HF_HUB_OFFLINE"):
        worker.main()
