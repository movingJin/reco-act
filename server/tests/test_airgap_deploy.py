"""airgap 배포 파일의 정적 검사 (01-plan 4.4, PR-15). Docker 없이 확인할 수 있는 것만 본다.

- docker-compose.airgap.yaml: internal 네트워크, 포트는 web만, 오프라인 env, 모델 읽기 전용, vLLM 사용 통계 끔, 비밀은 env_file로
- Dockerfile.airgap.dockerignore: 실제 server/ 파일 목록에 Docker의 규칙(마지막에 맞은 패턴, 상위 디렉터리 매칭)을 적용해
  비밀·녹음·테스트가 빠지고 앱 코드가 들어가는지
- nginx: 요약 대기 시간보다 긴 read timeout, Swagger 경로
- mock LLM(scripts/mock_llm.py): 요약 워크플로가 끝까지 돈다
- 모의 환경(docker-compose.airgap-sim.yaml, deploy-airgap-sim.yml): public 운영과 분리, OpenRouter, CPU 워커, 수동 배포
"""
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from utils.airgap_check import OFFLINE_ENV, is_internal_host

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "server"


class ComposeLoader(yaml.SafeLoader):
    """compose의 !reset 태그를 읽는다."""


ComposeLoader.add_constructor("!reset", lambda loader, node: None)


def load_compose(name):
    return yaml.load((REPO / name).read_text(encoding="utf-8"), Loader=ComposeLoader)


@pytest.fixture(scope="module")
def compose():
    return load_compose("docker-compose.airgap.yaml")


# ==================== compose ====================

def test_only_web_reaches_outside(compose):
    assert compose["networks"]["internal"] == {"internal": True}
    services = compose["services"]
    assert sorted(services) == ["api", "db", "llm", "redis", "web", "worker"]
    for name, service in services.items():
        if name == "web":
            assert sorted(service["networks"]) == ["edge", "internal"]
            assert [p.split(":")[-1] for p in service["ports"]] == ["443", "80"]
        else:
            assert service["networks"] == ["internal"], name
            assert "ports" not in service and "network_mode" not in service, name


@pytest.mark.parametrize("name", ["api", "worker"])
def test_app_services_run_strict_airgap(compose, name):
    env = compose["services"][name]["environment"]
    assert (env["APP_PROFILE"], env["AIRGAP_STRICT"], env["ENVIRONMENT"]) == ("airgap", "1", "prod")
    assert all(env[key] == value for key, value in OFFLINE_ENV.items())
    assert env["LLM_SERVING"] == "vllm"
    assert is_internal_host(re.match(r"https?://([^:/]+)", env["LLM_BASE_URL"]).group(1))
    # 비밀은 env_file로만 넣는다(LLM_API_KEY는 내부 vLLM이라 인증 없음)
    assert not [k for k, v in env.items() if re.search(r"PASSWORD|SECRET|DATABASE_URL", k)]
    assert env["LLM_API_KEY"] == "EMPTY"
    assert compose["services"][name]["env_file"] == ["${RECO_ACT_HOME:-/opt/reco-act}/airgap.env"]


def test_worker_uses_gpu_and_read_only_models(compose):
    worker = compose["services"]["worker"]
    env = worker["environment"]
    assert (env["WORKER_CONCURRENCY"], env["STT_PROVIDER"], env["DIARIZER"], env["STT_DEVICE"]) == ("1", "faster_whisper", "pyannote", "cuda")
    assert env["WHISPER_MODEL_PATH"].startswith("/models/") and env["PYANNOTE_PIPELINE_PATH"].startswith("/models/")
    assert "${RECO_ACT_HOME:-/opt/reco-act}/models:/models:ro" in worker["volumes"]
    assert worker["deploy"]["resources"]["reservations"]["devices"][0]["capabilities"] == ["gpu"]
    # 실행 중인 작업을 5초 기다린 뒤 큐에 돌려준다(PR-5b). 강제 종료보다 먼저 끝나야 한다.
    assert int(worker["stop_grace_period"].rstrip("s")) > 5


def test_records_volume_is_shared(compose):
    volume = "${RECO_ACT_HOME:-/opt/reco-act}/records:/app/records"
    assert volume in compose["services"]["api"]["volumes"] and volume in compose["services"]["worker"]["volumes"]


def test_vllm_is_offline_and_reads_models_read_only(compose):
    llm = compose["services"]["llm"]
    assert re.fullmatch(r"vllm/vllm-openai:\$\{VLLM_VERSION:-v\d+\.\d+\.\d+\}", llm["image"])
    assert llm["environment"]["VLLM_NO_USAGE_STATS"] == "1" and llm["environment"]["DO_NOT_TRACK"] == "1"
    assert all(llm["environment"][key] == value for key, value in OFFLINE_ENV.items())
    assert llm["volumes"] == ["${RECO_ACT_HOME:-/opt/reco-act}/models:/models:ro"]


def test_images_are_pinned(compose):
    assert re.fullmatch(r"postgres:16\.\d+-bookworm", compose["services"]["db"]["image"])  # 운영과 같은 메이저 버전
    assert re.fullmatch(r"redis:7\.2\.\d+-alpine", compose["services"]["redis"]["image"])  # 7.4부터 라이선스 변경(R14)


def test_mock_override_needs_no_gpu_or_models():
    mock = load_compose("docker-compose.airgap.mock.yaml")["services"]
    assert mock["worker"]["environment"] == {"STT_PROVIDER": "mock", "DIARIZER": "mock", "STT_DEVICE": "cpu"}
    assert mock["worker"]["build"]["target"] == "api"
    assert mock["worker"]["deploy"] is None and mock["llm"]["deploy"] is None  # !reset
    assert mock["llm"]["command"][:3] == ["python", "-m", "scripts.mock_llm"]


# ==================== Dockerfile.airgap.dockerignore ====================

def dockerignore_included(patterns, paths):
    """Docker의 .dockerignore 규칙을 단순화해 적용한다: 마지막에 맞은 패턴이 정하고, 상위 디렉터리가 맞으면 하위도 맞는다."""
    def regex(pattern):
        out, i = "", 0
        while i < len(pattern):
            if pattern.startswith("**", i):
                out, i = out + ".*", i + 2
            elif pattern[i] == "*":
                out, i = out + "[^/]*", i + 1
            else:
                out, i = out + re.escape(pattern[i]), i + 1
        return re.compile(out)

    rules = [(line.startswith("!"), regex(line.lstrip("!").rstrip("/"))) for line in patterns]
    included = []
    for path in paths:
        parts = path.split("/")
        prefixes = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
        excluded = False
        for exception, pattern in rules:
            if any(pattern.fullmatch(prefix) for prefix in prefixes):
                excluded = not exception
        if not excluded:
            included.append(path)
    return included


@pytest.fixture(scope="module")
def server_context():
    patterns = [
        line.strip() for line in (SERVER / "Dockerfile.airgap.dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    files = [
        p.relative_to(SERVER).as_posix() for p in SERVER.rglob("*")
        if p.is_file() and ".venv" not in p.parts
    ]
    return set(dockerignore_included(patterns, files)), files


def test_server_image_context_keeps_secrets_and_recordings_out(server_context):
    included, files = server_context
    leaked = [f for f in included if re.search(
        r"(^|/)(\.env|prod\.env|prompt\.txt|clova_stt\.py|test\.py)$|\.wav$|^records/|^tests/|^\.idea/|__pycache__|\.pyc$", f
    )]
    assert leaked == []
    assert all(not f.endswith(".env") for f in included)


def test_server_image_context_has_the_app(server_context):
    included, files = server_context
    for required in ("main.py", "worker.py", "database.py", "requirements.txt", "requirements-airgap.txt",
                     "api/routers.py", "stt/faster_whisper.py", "diarization/pyannote.py", "pipeline/merge.py",
                     "graph/summary_workflow.py", "utils/config.py", "utils/airgap_check.py", "scripts/mock_llm.py"):
        assert required in included, required
    # 앱 코드(.py)는 빠짐없이 들어간다(제외 대상만 빼고)
    app_code = {f for f in files if f.endswith(".py") and f.split("/")[0] in
                {"api", "graph", "models", "services", "stt", "diarization", "pipeline", "utils", "scripts"}}
    assert app_code - included == set()


def test_matcher_follows_docker_rules():
    patterns = ["*", "!api", "!api/**", "**/__pycache__"]
    assert dockerignore_included(patterns, [".env", "api/x.py", "api/__pycache__/x.pyc", "records/a.wav"]) == ["api/x.py"]


# ==================== Dockerfile·nginx ====================

APP_DIRS = ("api", "graph", "models", "services", "stt", "diarization", "pipeline", "utils", "scripts")


def test_server_dockerfile_copies_only_the_app():
    """ignore 파일을 쓰지 않는 빌드 도구(BuildKit이 아닌 docker build)에서도 비밀·녹음이 들어가지 않게 COPY를 목록으로 둔다."""
    server = (SERVER / "Dockerfile.airgap").read_text(encoding="utf-8")
    copies = [line for line in server.splitlines() if line.startswith("COPY")]
    assert not [line for line in copies if line.split()[1] == "."]  # COPY . . 없음
    for directory in APP_DIRS:
        assert server.count(f"COPY {directory} ./{directory}") == 2, directory  # api, worker 두 타깃
    assert server.count("COPY main.py worker.py database.py ./") == 2


def test_dockerfiles_bake_in_airgap_defaults():
    server = (SERVER / "Dockerfile.airgap").read_text(encoding="utf-8")
    assert server.count("APP_PROFILE=airgap") == 2 and server.count("PYANNOTE_METRICS_ENABLED=0") == 2  # worker-cpu는 api를 잇는다
    assert "FROM python:3.10" in server and "AS api" in server and "AS worker" in server and "FROM api AS worker-cpu" in server
    assert "download.pytorch.org/whl/cu128" in server and "download.pytorch.org/whl/cpu" in server
    assert "python -m scripts.fetch_swagger_ui /app/static/swagger-ui" in server  # airgap Swagger UI 정적 파일
    client = (REPO / "client" / "Dockerfile.airgap").read_text(encoding="utf-8")
    assert "npm run build:airgap" in client and "dist-airgap" in client  # dist 검사까지 통과해야 이미지가 만들어진다
    assert "COPY nginx.airgap.locations.conf /etc/nginx/reco-act/locations.conf" in client


def test_nginx_configs(reload_config):
    summary_wait = reload_config(APP_PROFILE="airgap").SUMMARY_WAIT_TIMEOUT
    client = REPO / "client"
    https, http, locations = (
        (client / name).read_text(encoding="utf-8")
        for name in ("nginx.airgap.conf", "nginx.airgap-http.conf", "nginx.airgap.locations.conf")
    )

    assert "listen 443 ssl;" in https and "return 301 https://$host$request_uri;" in https
    assert "listen 80;" in http and "return 301" not in http  # 모의 환경: TLS는 앞단 리버스 프록시
    for conf in (https, http):
        assert "include /etc/nginx/reco-act/locations.conf;" in conf
    assert int(re.search(r"proxy_read_timeout\s+(\d+)s;", locations).group(1)) > summary_wait
    for path in ("location /api/", "location = /docs", "location = /openapi.json", "location /docs-static/"):
        assert path in locations, path
    # 외부 주소 없음: api 서비스와 HTTPS 리다이렉트뿐
    assert set(re.findall(r"https?://\S+", https + http + locations)) == {"https://$host$request_uri;", "http://api:8000;"}


# ==================== mock LLM ====================

def test_mock_llm_runs_the_summary_workflow(reload_config, monkeypatch):
    from graph import summary_workflow
    from graph.summary_workflow import SummaryNode
    from scripts.mock_llm import Handler
    from synthetic_meeting import make_synthetic_meeting

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        reload_config(APP_PROFILE="airgap", LLM_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1", LLM_MODEL="mock", LLM_API_KEY="EMPTY")
        meeting = make_synthetic_meeting()
        monkeypatch.setattr(summary_workflow, "load_meeting", lambda meeting_id: meeting)

        result = SummaryNode().run(meeting.id)
    finally:
        server.shutdown()
        server.server_close()

    assert result is not None
    assert result.subject.startswith("(mock)") and result.meeting_notes.startswith("(mock)")
    assert result.paragraphs and result.next_steps


# ==================== 모의 환경(public 서버) ====================

@pytest.fixture(scope="module")
def sim():
    return load_compose("docker-compose.airgap-sim.yaml")


def test_sim_is_separate_from_public(sim):
    raw = (REPO / "docker-compose.airgap-sim.yaml").read_text(encoding="utf-8")
    # 운영 compose의 네트워크·녹음 디렉터리·외부 DB와 닿지 않는다
    assert "app-network" not in raw and "server/records" not in raw and "host.docker.internal" not in raw
    assert sorted(sim["services"]) == ["api", "db", "redis", "web", "worker"]
    for name, service in sim["services"].items():
        if name != "web":
            assert "ports" not in service, name
        for volume in service.get("volumes", []):
            assert volume.startswith("./"), (name, volume)  # 모의 환경 디렉터리 안에만 쓴다
    assert sim["services"]["web"]["ports"] == ["${SIM_WEB_PORT:-23002}:80"]
    # Synology의 docker-compose에서도 돌도록 최신 compose 전용 문법을 쓰지 않는다
    assert "name" not in sim and "!reset" not in raw


@pytest.mark.parametrize("name", ["api", "worker"])
def test_sim_runs_airgap_profile_with_openrouter(sim, name):
    service = sim["services"][name]
    env = service["environment"]
    assert (env["APP_PROFILE"], env["AIRGAP_STRICT"]) == ("airgap", "0")
    assert (env["LLM_SERVING"], env["LLM_BASE_URL"]) == ("openrouter", "https://openrouter.ai/api/v1")
    assert env["LLM_MAX_TOKENS"] == "${SIM_LLM_MAX_TOKENS:-8192}"
    assert all(env[key] == value for key, value in OFFLINE_ENV.items())  # 모델은 로컬 디렉터리에서만
    assert not [k for k in env if re.search(r"PASSWORD|SECRET|DATABASE_URL|API_KEY", k)]  # 비밀은 env_file
    assert service["env_file"] == ["./airgap-sim.env"]
    assert service["build"]["dockerfile"] == "Dockerfile.airgap"


def test_sim_worker_runs_on_cpu_with_uploaded_models(sim):
    worker = sim["services"]["worker"]
    env = worker["environment"]
    assert worker["build"]["target"] == "worker-cpu"
    assert (env["STT_PROVIDER"], env["DIARIZER"], env["STT_DEVICE"], env["STT_COMPUTE_TYPE"]) == ("faster_whisper", "pyannote", "cpu", "int8")
    assert env["WHISPER_MODEL_PATH"] == "/models/${SIM_WHISPER_MODEL_DIR:-faster-whisper-small}"
    assert "./models:/models:ro" in worker["volumes"]
    assert worker["cpus"] and worker["mem_limit"]  # public 서비스에 주는 영향을 줄인다
    assert "deploy" not in worker  # GPU 없음


def test_sim_web_serves_the_airgap_build_over_http(sim):
    web = sim["services"]["web"]
    assert web["volumes"] == [
        "./client/dist-airgap:/usr/share/nginx/html:ro",
        "./client/nginx.airgap-http.conf:/etc/nginx/conf.d/default.conf:ro",
        "./client/nginx.airgap.locations.conf:/etc/nginx/reco-act/locations.conf:ro",
    ]


@pytest.fixture(scope="module")
def sim_workflow():
    text = (REPO / ".github" / "workflows" / "deploy-airgap-sim.yml").read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_sim_workflow_is_manual_and_main_only(sim_workflow):
    text, workflow = sim_workflow
    assert workflow[True] == {"workflow_dispatch": None}  # YAML이 on을 True로 읽는다
    assert workflow["jobs"]["build"]["if"] == "github.ref == 'refs/heads/main'"
    assert workflow["jobs"]["deploy"]["needs"] == "build"


def test_sim_workflow_stays_out_of_public(sim_workflow):
    text, workflow = sim_workflow
    assert "secrets.ENV_FILE" not in text and "server/.env" not in text  # 운영 비밀을 쓰지 않는다
    assert "secrets.AIRGAP_SIM_ENV" in text
    assert text.count("format('{0}/reco-act-airgap-sim', secrets.SERVICE_ROOT)") == 2  # 운영(reco-act)과 다른 디렉터리
    assert "docker-compose -p reco-act-airgap-sim -f docker-compose.airgap-sim.yaml --env-file airgap-sim.env" in text
    assert "npm run build:airgap" in text  # dist 검사를 통과해야 배포한다


def test_sim_workflow_transfers_existing_files(sim_workflow):
    text, workflow = sim_workflow
    step = next(s for s in workflow["jobs"]["build"]["steps"] if s.get("name") == "Transfer files")
    built = {"airgap-sim.env", "client/dist-airgap"}  # 워크플로가 만든다
    for source in step["with"]["source"].split(","):
        if source not in built:
            assert list(REPO.glob(source)), source
