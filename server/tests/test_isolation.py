"""테스트가 실제 .env와 운영 접속 정보를 쓰지 않는지, 앱 import가 airgap 엔진 패키지를 올리지 않는지 확인한다."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FAKE_ENV, SECRET_ENV_KEYS

# requirements-airgap.txt에만 있는 엔진 패키지. public 이미지에는 없다.
ENGINE_MODULES = ("faster_whisper", "ctranslate2", "onnxruntime", "av", "torch", "torchaudio", "torchcodec", "pyannote")


def test_dotenv_is_blocked():
    import main  # noqa: F401  앱 전체 import (모든 모듈의 load_dotenv 호출 포함)

    # 실패해도 값이 출력되지 않도록 키 이름만 비교한다.
    leaked = [key for key in SECRET_ENV_KEYS if key in os.environ]
    assert leaked == []


def test_fake_connection_env():
    import database
    from utils import redis_client

    # 실패 메시지에 비밀번호가 찍히지 않도록 host와 database만 본다.
    assert (database.engine.url.host, database.engine.url.database) == ("127.0.0.1", "test")
    assert redis_client._REDIS_HOST == FAKE_ENV["REDIS_HOST"]


@pytest.mark.parametrize("profile", ["public", "airgap"])
def test_app_import_does_not_load_engine_packages(profile):
    """앱 서버와 워커를 import해도 엔진 패키지와 Clova 클라이언트를 올리지 않는다(프로바이더가 고를 때만 import).

    이미 import된 모듈이 섞이지 않게 별도 프로세스에서 확인한다. conftest처럼 load_dotenv를 막고 가짜 env만 준다.
    """
    modules = ENGINE_MODULES + ("clova_stt",)
    code = (
        "import dotenv, sys; dotenv.load_dotenv = lambda *a, **k: False\n"
        "import main, worker\n"
        f"print(sorted(m for m in {modules!r} if m in sys.modules))"
    )
    env = {**FAKE_ENV, "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), "APP_PROFILE": profile}
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).parent.parent, env=env, capture_output=True, text=True, timeout=120
    )

    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip().splitlines()[-1] == "[]"
