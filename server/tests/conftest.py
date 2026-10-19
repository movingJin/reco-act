"""테스트 공통 설정.

앱 모듈은 import 시점에 load_dotenv()로 server/.env(운영 비밀 포함)를 읽고,
DATABASE_URL·REDIS_PORT·SECRET_KEY가 없으면 import 자체가 실패한다.
그래서 앱 모듈을 import하기 전에 이 파일 최상단에서
1) load_dotenv를 막고 2) 가짜 env를 넣고 3) 결과를 바꿀 수 있는 env를 지운다.
"""
import importlib
import os

import dotenv
import pytest

# 앱 코드는 `from dotenv import load_dotenv`로 이 속성을 가져가므로 여기서 바꾸면 막힌다.
dotenv.load_dotenv = lambda *args, **kwargs: False

# 셸에 실제 값이 있어도 덮어쓴다. create_engine/redis 클라이언트는 연결을 미루므로 접속하지 않는다.
FAKE_ENV = {
    "DATABASE_URL": "postgresql://test:test@127.0.0.1:5432/test",
    "REDIS_HOST": "127.0.0.1",
    "REDIS_PORT": "6379",
    "SECRET_KEY": "test-secret-key",
}

# 테스트 결과를 바꾸거나 실제 외부 서비스를 가리킬 수 있는 env
SECRET_ENV_KEYS = [
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT_NAME",
    "AZURE_OPENAI_EMBEDDING",
    "CLOVA_INVOKE_URL",
    "CLOVA_SECRET_KEY",
    "SENDER_EMAIL",
    "SENDER_PASSWORD",
    "REDIS_PASSWORD",
    "LLM_API_KEY",
]
CLEARED_ENV_KEYS = SECRET_ENV_KEYS + [
    "APP_PROFILE",
    "ENVIRONMENT",  # prod면 RECORDS_DIR이 /app/records가 된다
    "AZURE_OPENAI_API_VERSION",
    "WEB_ORIGIN",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SECURITY",
    "EMAIL_DELIVERABILITY_CHECK",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_SERVING",
    "STT_PROVIDER",
    "DIARIZER",
    "WORKER_CONCURRENCY",
    "SUMMARY_WAIT_TIMEOUT",
    "SUMMARY_AUTO",
]

os.environ.update(FAKE_ENV)
for _key in CLEARED_ENV_KEYS:
    os.environ.pop(_key, None)

# -m external 테스트용 OpenRouter 키. 앱 코드가 보지 못하게 env에서 빼 둔다.
EXTERNAL_OPENROUTER_API_KEY = os.environ.pop("OPENROUTER_API_KEY", None)


def pytest_addoption(parser):
    parser.addoption(
        "--update-openapi-snapshot",
        action="store_true",
        default=False,
        help="tests/snapshots/openapi_public.json을 현재 앱 스키마로 다시 쓴다(의도한 API 변경일 때만)",
    )


@pytest.fixture
def reload_config(monkeypatch):
    """env를 바꿔 config를 다시 읽는다. 끝나면 env를 되돌리고 원래 상태로 다시 읽는다."""
    from utils import config

    def _reload(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    yield _reload
    monkeypatch.undo()
    importlib.reload(config)


# ==================== PostgreSQL (통합 테스트) ====================
# server/.env의 DATABASE_URL이 가리키는 서버에 테스트 전용 DB를 따로 만들어 쓴다. .env의 DB(앱 데이터)는 건드리지 않는다.
# .env 값은 os.environ에 넣지 않고 여기서만 읽는다. 앱 코드는 계속 위의 가짜 env를 본다.
TEST_DATABASE_NAME = "reco_act_test"


@pytest.fixture(scope="session")
def pg_engine():
    """실제 PostgreSQL. 큐(SKIP LOCKED)와 ARRAY·JSONB 컬럼은 SQLite로 검증할 수 없다.

    세션을 시작할 때 테스트 DB를 새로 만들고 끝나면 지운다(.env 계정에 CREATEDB 권한 필요).
    .env에 DATABASE_URL이 없거나 서버에 접속할 수 없으면 이 fixture를 쓰는 테스트는 건너뛴다.
    """
    from pathlib import Path

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import OperationalError

    import database

    env_url = dotenv.dotenv_values(Path(__file__).parent.parent / ".env").get("DATABASE_URL")
    if not env_url:
        pytest.skip("server/.env has no DATABASE_URL")
    app_url = make_url(env_url)
    assert app_url.database != TEST_DATABASE_NAME, "test database must differ from the app database"

    # 앱 DB에는 CREATE/DROP DATABASE를 보내려고 접속만 한다.
    admin = create_engine(app_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})

    def drop_test_database():
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE_NAME}" WITH (FORCE)'))

    try:
        drop_test_database()  # 이전 세션이 중간에 죽어 남은 것 정리
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE_NAME}"'))
            # 버리는 데이터라 commit마다 WAL 기록을 기다리지 않는다. 원격 서버에서는 commit이 수백 ms씩 걸린다.
            conn.execute(text(f'ALTER DATABASE "{TEST_DATABASE_NAME}" SET synchronous_commit = off'))
    except OperationalError as e:
        admin.dispose()
        pytest.skip(f"cannot reach PostgreSQL from server/.env ({type(e).__name__})")  # URL(비밀번호)은 출력하지 않는다

    engine = create_engine(app_url.set(database=TEST_DATABASE_NAME))
    database.Base.metadata.create_all(engine)
    yield engine
    engine.dispose()
    drop_test_database()
    admin.dispose()


@pytest.fixture
def db(pg_engine):
    """앱의 SessionLocal을 테스트 PostgreSQL에 붙이고 테이블을 비운다.

    SessionLocal을 import로 가져간 모듈(meeting_service 등)도 같은 sessionmaker 객체를 쓰므로 한 번의 configure로 모두 바뀐다.
    """
    from sqlalchemy import text

    import database

    original = database.SessionLocal.kw["bind"]
    database.SessionLocal.configure(bind=pg_engine)
    # 몇 행뿐이라 TRUNCATE(파일 교체)보다 DELETE가 빠르다. id는 이어서 증가하므로 테스트는 반환된 id를 쓴다.
    with pg_engine.begin() as conn:
        for table in ("processing_jobs", "transcripts", "paragraphs", "next_steps", "meetings", "users", "domain_keywords"):
            conn.execute(text(f"DELETE FROM {table}"))
    yield database.SessionLocal
    database.SessionLocal.configure(bind=original)
