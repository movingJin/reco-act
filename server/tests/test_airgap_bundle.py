"""반입 번들 도구(scripts/airgap/bundle.py, install.sh)의 검사 (01-plan 6장, PR-16). 네트워크·Docker 없이 돈다.

- 설정 일치: compose의 이미지·build 항목, Dockerfile의 pip 인덱스, 모델 디렉터리 이름(compose.env), 어댑터의 필수 파일, NOTICE
- HF 파일 대조(git blob, LFS 포인터), 다운로드(이어 받기, 토큰은 같은 호스트에만), wheel·npm 목록, 소스 사본(추적 파일만)
- manifest·SHA256SUMS 쓰기와 검증, 이미지 단계(가짜 docker), install.sh(확인, 모델 복사, 설정 견본, docker load)
"""
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("airgap_bundle", REPO / "scripts/airgap/bundle.py")
bundle = importlib.util.module_from_spec(_spec)
sys.modules["airgap_bundle"] = bundle  # dataclass가 모듈을 찾는다
_spec.loader.exec_module(bundle)

needs_shell = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("sha256sum")), reason="bash, sha256sum")


class ComposeLoader(yaml.SafeLoader):
    pass


ComposeLoader.add_constructor("!reset", lambda loader, node: None)


def compose_services():
    return yaml.load((REPO / bundle.COMPOSE_FILE).read_text(encoding="utf-8"), Loader=ComposeLoader)["services"]


def env_example(name):
    text = (REPO / f"deploy/airgap/{name}.env.example").read_text(encoding="utf-8")
    return dict(re.findall(r"^(\w+)=(.*)$", text, re.MULTILINE))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_oid(tmp_path, data: bytes) -> str:
    path = tmp_path / "blob"
    path.write_bytes(data)
    return subprocess.run(["git", "hash-object", str(path)], capture_output=True, text=True, check=True).stdout.strip()


# ==================== 설정 일치 ====================

def test_compose_images_resolve_like_compose():
    refs = bundle.compose_images((REPO / bundle.COMPOSE_FILE).read_text(encoding="utf-8"), {"RECO_ACT_VERSION": "9.9"})
    assert len(refs) == len({s["image"] for s in compose_services().values()}) == 6
    assert not [r for r in refs if "$" in r]
    assert [r for r in refs if r.startswith("reco-act-")] == ["reco-act-web:9.9", "reco-act-api-airgap:9.9", "reco-act-worker:9.9"]
    # 받는 이미지의 버전은 compose 기본값이고, 현장 compose.env 견본도 같은 값이다
    assert f"vllm/vllm-openai:{env_example('compose')['VLLM_VERSION']}" in refs
    assert {"postgres:16.10-bookworm", "redis:7.2.10-alpine"} <= set(refs)


def test_builds_match_compose_build_sections():
    builds = {}
    for service in compose_services().values():
        if "build" in service:
            build = service["build"]
            assert build["dockerfile"] == bundle.DOCKERFILE
            builds[service["image"].split(":")[0]] = (build["context"].lstrip("./"), build.get("target"))
    assert builds == bundle.BUILDS


def test_wheel_sets_install_like_the_dockerfile():
    text = (REPO / "server" / bundle.DOCKERFILE).read_text(encoding="utf-8")
    api, worker = text.split("FROM api AS worker-cpu")[0], text.split("AS worker\n")[1]
    sets = {name: (req, image, indexes) for name, req, image, indexes in bundle.WHEEL_SETS}
    assert "RUN pip install -r requirements.txt\n" in api and "--index-url" not in api
    assert sets["api"] == ("server/requirements.txt", "reco-act-api-airgap", (bundle.PYPI,))
    assert re.search(r"-r requirements-airgap\.txt \\\n\s+--index-url (\S+) --extra-index-url (\S+)", worker).groups() == sets["worker"][2]
    assert sets["worker"][:2] == ("server/requirements-airgap.txt", "reco-act-worker")
    # glibc: worker의 Ubuntu 22.04(2.35)가 가장 낮다
    assert "ubuntu22.04" in bundle.stage_base(text, "worker") and bundle.PLATFORMS[0] == "manylinux_2_35_x86_64"
    assert "manylinux2014_x86_64" in bundle.PLATFORMS and "manylinux_2_17_x86_64" in bundle.PLATFORMS


def test_models_match_compose_env_and_adapters():
    from diarization.pyannote import REQUIRED_PIPELINE_FILES
    from stt.faster_whisper import REQUIRED_MODEL_FILES

    models = {m.dir: m for m in bundle.MODELS}
    example = env_example("compose")
    assert sorted(models) == sorted([example["WHISPER_MODEL_DIR"], example["PYANNOTE_PIPELINE_DIR"], example["LLM_MODEL_DIR"]])
    compose = (REPO / bundle.COMPOSE_FILE).read_text(encoding="utf-8")
    assert all(f":-{name}}}" in compose for name in models)  # compose 기본값도 같다
    assert set(REQUIRED_MODEL_FILES) <= set(models["faster-whisper-large-v3"].required)
    assert "preprocessor_config.json" in models["faster-whisper-large-v3"].required  # large-v3는 멜 128개(9.2)
    assert set(REQUIRED_PIPELINE_FILES) <= set(models["speaker-diarization-community-1"].required)
    assert all(re.fullmatch(r"[0-9a-f]{40}", m.revision) for m in bundle.MODELS)


def test_notice_attributes_every_bundled_model():
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    for model in bundle.MODELS:
        assert f"https://huggingface.co/{model.repo}" in notice and model.revision in notice
    assert "CC BY 4.0" in notice and "https://creativecommons.org/licenses/by/4.0/" in notice
    assert "Hervé Bredin" in notice and "Wespeaker" in notice and "VBx" in notice  # 모델 카드의 Citations


def test_dockerfile_base_images():
    server = (REPO / "server" / bundle.DOCKERFILE).read_text(encoding="utf-8")
    client = (REPO / "client" / bundle.DOCKERFILE).read_text(encoding="utf-8")
    assert bundle.base_images(server) == ["python:3.10.18-slim-bookworm", "nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04"]
    assert bundle.base_images(client) == ["node:22.23.3-alpine", "nginx:1.28.0-alpine"]
    assert bundle.stage_base(server, "worker-cpu") == bundle.stage_base(server, "api") == "python:3.10.18-slim-bookworm"
    assert bundle.stage_base(client) == "nginx:1.28.0-alpine"


def test_image_name_helpers():
    assert bundle.split_ref("vllm/vllm-openai:v0.31.0") == ("vllm/vllm-openai", "v0.31.0")
    assert bundle.split_ref("registry:5000/x") == ("registry:5000/x", "latest")
    assert bundle.image_file("vllm/vllm-openai:v0.31.0") == "vllm_vllm-openai_v0.31.0.tar.gz"
    assert bundle.image_license("redis:7.2.10-alpine") == "BSD-3-Clause"
    assert bundle.image_license("example/unknown:1") == "확인 필요"


# ==================== HF 파일 대조 ====================

def test_hf_file_matches_git_blob(tmp_path):
    data = b"pipeline:\n  name: x\n"
    path = tmp_path / "config.yaml"
    path.write_bytes(data)
    entry = {"size": len(data), "oid": git_blob_oid(tmp_path, data)}
    assert bundle.hf_file_sha256(entry, path) == sha256(data)
    assert bundle.hf_file_sha256(dict(entry, oid="0" * 40), path) is None
    assert bundle.hf_file_sha256(dict(entry, size=len(data) + 1), path) is None
    assert bundle.hf_file_sha256(entry, tmp_path / "missing") is None


def test_hf_lfs_file_matches_pointer_when_sha256_is_hidden(tmp_path):
    data = os.urandom(5000)
    path = tmp_path / "pytorch_model.bin"
    path.write_bytes(data)
    pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{sha256(data)}\nsize {len(data)}\n".encode()
    # 게이트 저장소는 비로그인 응답에서 lfs.oid를 가린다. git blob oid(포인터 파일의 해시)로 대조한다
    masked = {"size": len(data), "oid": git_blob_oid(tmp_path, pointer), "lfs": {"oid": "*" * 16, "size": len(data)}}
    assert bundle.hf_file_sha256(masked, path) == sha256(data)
    assert bundle.hf_file_sha256(dict(masked, oid="0" * 40), path) is None
    visible = {"size": len(data), "oid": "ignored", "lfs": {"oid": sha256(data)}}
    assert bundle.hf_file_sha256(visible, path) == sha256(data)
    assert bundle.hf_file_sha256(dict(visible, lfs={"oid": "f" * 64}), path) is None


# ==================== 다운로드 ====================

class FileServer:
    """Range를 지원하는 작은 HTTP 서버. 받은 요청의 경로와 헤더를 남긴다."""

    def __init__(self, data: bytes, other_port=None):
        self.data, self.requests, self.honor_range, self.fail_first, self.cut_first = data, [], True, 0, 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                server.requests.append((self.path, dict(self.headers)))
                if self.path == "/redirect-same":
                    return self.redirect("/file")
                if self.path == "/redirect-other":
                    return self.redirect(f"http://localhost:{other_port}/file")
                if self.path != "/file":
                    return self.send_error(404)
                if server.fail_first:
                    server.fail_first -= 1
                    return self.send_error(503)
                start = 0
                found = re.fullmatch(r"bytes=(\d+)-", self.headers.get("Range", ""))
                if found and server.honor_range:
                    start = int(found.group(1))
                    self.send_response(206)
                else:
                    self.send_response(200)
                body = server.data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if server.cut_first:  # 길이를 알린 뒤 절반만 보내고 끊는다
                    server.cut_first -= 1
                    body = body[: len(body) // 2]
                self.wfile.write(body)

            def redirect(self, location):
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path, host="127.0.0.1"):
        return f"http://{host}:{self.port}{path}"


@pytest.fixture
def servers(monkeypatch):
    monkeypatch.setattr(bundle, "RETRY_DELAY", 0)
    data = os.urandom(300_000)
    other = FileServer(data)
    main = FileServer(data, other_port=other.port)
    yield main, other
    main.httpd.shutdown()
    other.httpd.shutdown()


def test_download_and_resume(servers, tmp_path):
    main, _ = servers
    dest = tmp_path / "f.bin"
    assert bundle.download(main.url("/file"), dest) == sha256(main.data) and dest.read_bytes() == main.data

    resumed = tmp_path / "r.bin"
    (tmp_path / "r.bin.part").write_bytes(main.data[:1000])
    assert bundle.download(main.url("/file"), resumed) == sha256(main.data)
    assert main.requests[-1][1].get("Range") == "bytes=1000-"
    assert not (tmp_path / "r.bin.part").exists()

    main.cut_first = 1  # 연결이 중간에 끊기면 받은 데까지 두고 이어 받는다
    cut = tmp_path / "c.bin"
    assert bundle.download(main.url("/file"), cut) == sha256(main.data)
    assert main.requests[-1][1].get("Range") == f"bytes={len(main.data) // 2}-"

    main.honor_range = False  # Range를 무시하는 서버면 처음부터 다시 쓴다
    restarted = tmp_path / "s.bin"
    (tmp_path / "s.bin.part").write_bytes(b"junk")
    assert bundle.download(main.url("/file"), restarted) == sha256(main.data)


def test_download_retries_server_errors_but_not_missing_files(servers, tmp_path):
    main, _ = servers
    main.fail_first = 2
    assert bundle.download(main.url("/file"), tmp_path / "a") == sha256(main.data)
    before = len(main.requests)
    with pytest.raises(bundle.BundleError, match="HTTP 404"):
        bundle.download(main.url("/missing"), tmp_path / "b")
    assert len(main.requests) == before + 1


def test_token_is_sent_only_to_the_same_host(servers, tmp_path):
    main, other = servers
    bundle.download(main.url("/redirect-same"), tmp_path / "a", token="secret")
    assert [h.get("Authorization") for _, h in main.requests] == ["Bearer secret", "Bearer secret"]
    bundle.download(main.url("/redirect-other"), tmp_path / "b", token="secret")
    assert main.requests[-1][1].get("Authorization") == "Bearer secret"
    assert other.requests[-1][0] == "/file" and "Authorization" not in other.requests[-1][1]


# ==================== wheels ====================

def report_entry(name, version, filename, *, license_meta=None, sha="a" * 64, base="https://files.example/"):
    return {
        "metadata": dict({"name": name, "version": version}, **(license_meta or {})),
        "download_info": {"url": base + filename, "archive_info": {"hashes": {"sha256": sha}}},
    }


def test_wheels_from_report_and_lock():
    report = {"version": "1", "install": [
        report_entry("torch", "2.8.0+cu128", "torch-2.8.0%2Bcu128-cp310-cp310-manylinux_2_28_x86_64.whl",
                     license_meta={"license": "BSD-3-Clause"}),
        report_entry("Alpha_Pkg", "1.0", "alpha_pkg-1.0-py3-none-any.whl", sha="b" * 64,
                     license_meta={"license_expression": "MIT", "license": "long\ntext"}),
        report_entry("numpy", "2.2.6", "numpy-2.2.6-cp310-cp310-manylinux_2_17_x86_64.whl",
                     license_meta={"license": "Copyright (c) 2005\nNumPy Developers\n...",
                                   "classifier": ["License :: OSI Approved :: BSD License", "Topic :: X"]}),
        dict(report_entry("bare", "1", "bare-1-py3-none-any.whl"), is_yanked=True),
        report_entry("tiktoken", "0.14.0", "tiktoken-0.14.0-cp310-cp310-manylinux_2_28_x86_64.whl",
                     license_meta={"license": "MIT License\n        \n        Copyright (c) 2022 OpenAI"}),
    ]}
    wheels = bundle.wheels_from_report(report)[:4]
    assert wheels[0].filename == "torch-2.8.0+cu128-cp310-cp310-manylinux_2_28_x86_64.whl"
    assert [w.license for w in wheels] == ["BSD-3-Clause", "MIT", "BSD License", "UNKNOWN"]
    assert [w.yanked for w in wheels] == [False, False, False, True]
    assert bundle.wheels_from_report(report)[4].license == "MIT License"
    lock = bundle.lock_text("worker", "test", wheels).splitlines()
    assert lock[0].startswith("# ") and "--require-hashes" in lock[1]
    assert lock[2:] == [f"Alpha_Pkg==1.0 --hash=sha256:{'b' * 64}", f"bare==1 --hash=sha256:{'a' * 64}",
                        f"numpy==2.2.6 --hash=sha256:{'a' * 64}", f"torch==2.8.0+cu128 --hash=sha256:{'a' * 64}"]


def test_license_from_the_wheel_when_the_report_has_none(tmp_path):
    def make(name, files):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as wheel:
            for member, text in files.items():
                wheel.writestr(member, text)
        return path

    # pip 23(서버의 python:3.10 이미지)은 보고서에 License-Expression을 넣지 않는다. click·pillow 등은 이 필드에만 적는다
    metadata = "Metadata-Version: 2.4\nName: click\nVersion: 8.3.0\nLicense-Expression: BSD-3-Clause\nLicense-File: LICENSE.txt\n\nbody"
    assert bundle.wheel_license(make("m.whl", {"click-8.3.0.dist-info/METADATA": metadata,
                                               "click-8.3.0.dist-info/licenses/LICENSE.txt": "Copyright 2014 Pallets"})) == "BSD-3-Clause"
    classified = "Metadata-Version: 2.1\nName: x\nClassifier: License :: OSI Approved :: MIT License\n\n"
    assert bundle.wheel_license(make("k.whl", {"x-1.dist-info/METADATA": classified})) == "MIT License"
    # pyannote 계열·torchcodec은 메타데이터에 라이선스 없이 License-File만 둔다(PEP 639)
    found = make("a.whl", {"pkg/x.py": "", "pkg/vendor/LICENSE": "Apache License", "a-1.dist-info/licenses/": "",
                           "a-1.dist-info/METADATA": "Name: a\nLicense-File: LICENSE\n\n",
                           "a-1.dist-info/licenses/LICENSE": "\nThe MIT License (MIT)\n\nCopyright"})
    assert bundle.wheel_license(found) == "The MIT License (MIT) (LICENSE 파일)"
    assert bundle.wheel_license(make("b.whl", {"b-1.dist-info/LICENSE.txt": "Copyright (c) 2020 someone\n"})) is None
    assert bundle.wheel_license(make("c.whl", {"c-1.dist-info/METADATA": "Name: c"})) is None


def test_report_needs_wheels_with_sha256():
    with pytest.raises(bundle.BundleError, match="need a wheel"):
        bundle.wheels_from_report({"version": "1", "install": [report_entry("x", "1", "x-1.tar.gz")]})
    with pytest.raises(bundle.BundleError, match="need a wheel"):
        bundle.wheels_from_report({"version": "1", "install": [report_entry("x", "1", "x-1-py3-none-any.whl", sha="")]})
    with pytest.raises(bundle.BundleError, match="report version"):
        bundle.wheels_from_report({"version": "0", "install": []})


def test_parse_freeze():
    assert bundle.parse_freeze("# c\nfastapi==0.104.1\ntorch==2.8.0+cu128\n\n") == ["fastapi==0.104.1", "torch==2.8.0+cu128"]
    for bad in ("-e git+https://x#egg=y", "pkg @ file:///tmp/pkg", "pkg>=1"):
        with pytest.raises(bundle.BundleError, match="not name==version"):
            bundle.parse_freeze(bad)


def test_collect_wheels_pins_to_the_built_images(tmp_path, monkeypatch):
    content = {"common-1-py3-none-any.whl": b"common", "torch-2-cp310-linux.whl": b"torch", "fastapi-1-py3-none-any.whl": b"api"}

    def wheel(filename):
        name, version = filename.split("-")[:2]
        return bundle.Wheel(name, version, filename, f"https://files.example/{filename}", sha256(content[filename]), "MIT",
                            yanked=name == "fastapi")

    calls = []

    def fake_resolve(python, requirements, indexes, no_deps, workdir):
        calls.append((requirements.name, indexes, no_deps, requirements.read_text() if no_deps else None))
        if "api" in requirements.name or requirements.name == "requirements.txt":
            return [wheel("fastapi-1-py3-none-any.whl"), wheel("common-1-py3-none-any.whl")]
        return [wheel("torch-2-cp310-linux.whl"), wheel("common-1-py3-none-any.whl")]

    def fake_download(url, dest, token=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content[url.rsplit("/", 1)[1]])
        return sha256(dest.read_bytes())

    monkeypatch.setattr(bundle, "check_pip_python", lambda python: None)
    monkeypatch.setattr(bundle, "resolve_wheels", fake_resolve)
    monkeypatch.setattr(bundle, "download", fake_download)
    monkeypatch.setattr(bundle, "image_freeze", lambda ref: {"reco-act-api-airgap:9": ["fastapi==1", "common==1"]}[ref])
    out, cache, work = tmp_path / "out", tmp_path / "cache", tmp_path / "work"
    work.mkdir()

    # api 이미지만 빌드했다: api는 이미지의 pip freeze(의존성 해석 없이), worker는 요구 파일을 해석한다
    items = bundle.collect_wheels("py", REPO, out, cache, "9", {"reco-act-api-airgap": "reco-act-api-airgap:9"}, work)
    assert calls == [("freeze-api.txt", (bundle.PYPI,), True, "fastapi==1\ncommon==1\n"),
                     ("requirements-airgap.txt", (bundle.TORCH_CU128, bundle.PYPI), False, None)]
    wheels = {i.path: i for i in items if i.type == "wheel"}
    assert sorted(wheels) == ["wheels/common-1-py3-none-any.whl", "wheels/fastapi-1-py3-none-any.whl", "wheels/torch-2-cp310-linux.whl"]
    assert wheels["wheels/common-1-py3-none-any.whl"].notes == "api, worker 이미지"
    assert wheels["wheels/fastapi-1-py3-none-any.whl"].notes == "api 이미지. 인덱스에서 철회(yanked)된 버전"
    assert (out / "wheels/torch-2-cp310-linux.whl").read_bytes() == b"torch"
    lock = (out / "wheels/requirements-api.lock.txt").read_text()
    assert "reco-act-api-airgap:9 이미지의 pip freeze" in lock and f"fastapi==1 --hash=sha256:{sha256(b'api')}" in lock

    # 이미지에 있는데 wheel을 찾지 못한 패키지가 있으면 멈춘다
    monkeypatch.setattr(bundle, "image_freeze", lambda ref: ["fastapi==1", "common==1", "lost==3"])
    with pytest.raises(bundle.BundleError, match="lost"):
        bundle.collect_wheels("py", REPO, tmp_path / "out2", cache, "9", {"reco-act-api-airgap": "x"}, work)


# ==================== npm ====================

def test_npm_packages_take_every_platform():
    lock = {"packages": {
        "": {"name": "client", "version": "1.0.0"},
        "node_modules/@esbuild/darwin-arm64": {"version": "0.21.5", "resolved": "https://r/esb.tgz", "integrity": "sha512-a",
                                               "license": "MIT", "optional": True, "os": ["darwin"]},
        "node_modules/a/node_modules/b": {"version": "2.0.0", "resolved": "https://r/b.tgz", "integrity": "sha512-b", "license": "ISC"},
        "node_modules/alias": {"name": "real-name", "version": "3.0.0", "resolved": "https://r/c.tgz", "integrity": "sha512-c"},
        "node_modules/local": {"link": True, "resolved": "../local"},
    }}
    packages = bundle.npm_packages(lock)
    assert [(p.name, p.license) for p in packages] == [("@esbuild/darwin-arm64", "MIT"), ("b", "ISC"), ("real-name", "UNKNOWN")]
    assert bundle.license_summary(["MIT", "ISC", "MIT"]) == "MIT 2, ISC 1"
    lock["packages"]["node_modules/bad"] = {"version": "1", "resolved": "https://r/bad.tgz"}
    with pytest.raises(bundle.BundleError, match="bad has no resolved/integrity"):
        bundle.npm_packages(lock)


FAKE_NPM = '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
cache = Path(args[args.index("--cache") + 1])
state = cache / "_cacache" / "keys.json"
keys = json.loads(state.read_text()) if state.exists() else []
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps([os.getcwd()] + args) + "\\n")
if args[:2] == ["cache", "add"]:
    keys += [a for a in args[2:] if a.startswith("https://") and a not in os.environ.get("FAKE_DROP", "")]
elif args[:2] == ["cache", "ls"]:
    print("\\n".join("make-fetch-happen:request-cache:" + k for k in keys))
elif args[0] == "ci":
    (cache / "_logs").mkdir(parents=True, exist_ok=True)
    (cache / "_logs" / "debug.log").write_text("log")
state.parent.mkdir(parents=True, exist_ok=True)
state.write_text(json.dumps(keys))
'''


def fake_tool(tmp_path, name, source):
    path = tmp_path / "bin" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(0o755)
    return path


def test_collect_npm_fills_and_checks_the_cache(tmp_path, monkeypatch):
    log = tmp_path / "npm.log"
    monkeypatch.setattr(bundle, "NPM", str(fake_tool(tmp_path, "npm", FAKE_NPM)))
    monkeypatch.setenv("FAKE_LOG", str(log))
    out, work = tmp_path / "out", tmp_path / "work"
    work.mkdir()

    items = bundle.collect_npm(REPO, out, work)

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0][1:2] == ["ci"] and calls[0][0].endswith("npm-online")  # 온라인 npm ci로 메타데이터까지 채운다
    assert calls[-1][1:3] == ["ci", "--offline"] and calls[-1][0].endswith("npm-offline")
    lock = json.loads((REPO / "client/package-lock.json").read_text(encoding="utf-8"))
    urls = {e["resolved"] for k, e in lock["packages"].items() if k}
    added = {a for c in calls if c[1:3] == ["cache", "add"] for a in c[5:]}
    assert added == urls  # 이 PC와 다른 플랫폼의 tarball까지
    with tarfile.open(out / "npm/npm-cache.tar.gz") as tar:
        names = tar.getnames()
    assert "npm-cache/_cacache/keys.json" in names and not [n for n in names if "_logs" in n]
    rows = (out / "npm/packages.tsv").read_text().splitlines()
    assert rows[0] == "name\tversion\tlicense\tresolved\tintegrity" and len(rows) - 1 == len(urls)
    assert [i.path for i in items] == ["npm/npm-cache.tar.gz", "npm/packages.tsv"]

    monkeypatch.setenv("FAKE_DROP", sorted(urls)[0])
    work2 = tmp_path / "work2"
    work2.mkdir()
    with pytest.raises(bundle.BundleError, match="missing 1 tarballs"):
        bundle.collect_npm(REPO, tmp_path / "out2", work2)


# ==================== 소스 ====================

def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_snapshot_takes_tracked_files_only(tmp_path):
    repo = tmp_path / "repo"
    (repo / "server").mkdir(parents=True)
    (repo / "server/app.py").write_text("v1")
    (repo / "gone.txt").write_text("x")
    (repo / ".gitignore").write_text("*.env\nrecords/\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    (repo / "server/prod.env").write_text("SECRET=1")
    (repo / "records").mkdir()
    (repo / "records/a.m4a").write_bytes(b"voice")
    (repo / "notes.xlsx").write_text("untracked")

    commit, dirty = bundle.snapshot(repo, tmp_path / "s1", allow_dirty=False)
    assert re.fullmatch(r"[0-9a-f]{40}", commit) and dirty is False
    assert sorted(p.relative_to(tmp_path / "s1").as_posix() for p in (tmp_path / "s1").rglob("*") if p.is_file()) == [
        ".gitignore", "gone.txt", "server/app.py"]

    (repo / "server/app.py").write_text("v2")
    (repo / "gone.txt").unlink()
    with pytest.raises(bundle.BundleError, match="uncommitted"):
        bundle.snapshot(repo, tmp_path / "s2", allow_dirty=False)
    assert bundle.snapshot(repo, tmp_path / "s3", allow_dirty=True)[1] is True
    assert (tmp_path / "s3/server/app.py").read_text() == "v2" and not (tmp_path / "s3/gone.txt").exists()


# ==================== 이미지(가짜 docker) ====================

FAKE_DOCKER = '''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[0] == "build":
    with open(os.environ["FAKE_LOG"] + ".buildkit", "a") as log:
        log.write(os.environ.get("DOCKER_BUILDKIT", "-") + "\\n")
if args[:2] == ["image", "inspect"]:
    ref = args[-1]
    print(json.dumps({"Id": "sha256:" + "1" * 64, "RepoDigests": [] if ref.startswith("reco-act-") else [ref.split(":")[0] + "@sha256:" + "2" * 64],
                      "Os": "linux", "Architecture": os.environ.get("FAKE_ARCH", "amd64")}))
elif args[0] == "save":
    sys.stdout.write("saved " + args[1])
elif args[0] == "load":
    pass
elif args[0] == "run":
    print("fastapi==0.104.1")
'''


@pytest.fixture
def docker_log(tmp_path, monkeypatch):
    log = tmp_path / "docker.log"
    monkeypatch.setattr(bundle, "DOCKER", str(fake_tool(tmp_path, "docker", FAKE_DOCKER)))
    monkeypatch.setenv("FAKE_LOG", str(log))
    return log


def test_collect_images_builds_from_the_snapshot_and_saves_all(tmp_path, docker_log, monkeypatch):
    monkeypatch.delenv("DOCKER_BUILDKIT", raising=False)
    out = tmp_path / "out"
    items, built = bundle.collect_images(REPO, out, "9.9", "c" * 40, with_base=False)

    assert built == {"reco-act-web": "reco-act-web:9.9", "reco-act-api-airgap": "reco-act-api-airgap:9.9",
                     "reco-act-worker": "reco-act-worker:9.9"}
    calls = [json.loads(line) for line in docker_log.read_text().splitlines()]
    builds = [c for c in calls if c[0] == "build"]
    api = next(c for c in builds if "reco-act-api-airgap:9.9" in c)
    assert api[-1] == str(REPO / "server") and api[api.index("-f") + 1] == str(REPO / "server" / bundle.DOCKERFILE)
    assert api[api.index("--target") + 1] == "api" and f"org.opencontainers.image.revision={'c' * 40}" in api
    assert "--target" not in next(c for c in builds if "reco-act-web:9.9" in c)
    assert all(c[1:3] == ["--platform", "linux/amd64"] for c in calls if c[0] in ("build", "pull"))
    # BuildKit을 강제하지 않는다(buildx가 없는 Docker에서도 기존 빌더로 빌드한다)
    assert Path(str(docker_log) + ".buildkit").read_text().split() == ["-", "-", "-"]
    assert sorted(c[-1] for c in calls if c[0] == "pull") == ["postgres:16.10-bookworm", "redis:7.2.10-alpine", "vllm/vllm-openai:v0.31.0"]

    assert len(items) == 6
    for item in items:
        with gzip.open(out / item.path) as f:
            assert f.read().decode() == f"saved {item.name}:{item.version}"
    by_name = {i.name: i for i in items}
    assert by_name["vllm/vllm-openai"].source_url == "vllm/vllm-openai@sha256:" + "2" * 64
    assert by_name["vllm/vllm-openai"].license == "Apache-2.0"
    assert by_name["reco-act-worker"].source_url == f"git {'c' * 40} server/{bundle.DOCKERFILE} --target worker"
    assert "nvidia/cuda" in by_name["reco-act-worker"].license


def test_collect_images_with_base_images_and_wrong_arch(tmp_path, docker_log, monkeypatch):
    items, _ = bundle.collect_images(REPO, tmp_path / "out", "9.9", "c" * 40, with_base=True)
    assert {i.name for i in items} >= {"python", "nvidia/cuda", "node", "nginx"} and len(items) == 10
    monkeypatch.setenv("FAKE_ARCH", "arm64")  # Apple Silicon 등에서 빌드하면 쓸 수 없다
    with pytest.raises(bundle.BundleError, match="linux/arm64"):
        bundle.collect_images(REPO, tmp_path / "out2", "9.9", "c" * 40, with_base=False)


def test_image_freeze_skips_the_entrypoint(docker_log):
    assert bundle.image_freeze("reco-act-worker:9") == ["fastapi==0.104.1"]
    call = json.loads(docker_log.read_text().splitlines()[-1])
    assert call[:6] == ["run", "--rm", "--network", "none", "--entrypoint", "python"]
    assert call[6:] == ["reco-act-worker:9", "-m", "pip", "freeze", "--all"]


# ==================== manifest·검증 ====================

def make_bundle(root: Path, files: dict, version="9.9"):
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
        kind = path.split("/")[0] if "/" in path else "doc"
        items.append(bundle.Item(kind, path, version, "https://example/" + path, license="MIT", notes="n", path=path))
    meta = {"name": "reco-act-airgap", "version": version, "git_commit": "c" * 40, "git_dirty": False, "skipped": []}
    return bundle.write_manifest(root, items, meta)


def test_manifest_lists_every_file_with_its_hash(tmp_path):
    files = {"images/a.tar.gz": b"image", "models/m/model.bin": b"weights", "NOTICE": b"notice"}
    sums_sha = make_bundle(tmp_path / "b", files)
    manifest = json.loads((tmp_path / "b/manifest.json").read_text())
    assert manifest["version"] == "9.9" and manifest["git_commit"] == "c" * 40
    assert [i["path"] for i in manifest["items"]] == sorted(files)
    for item in manifest["items"]:
        assert {"type", "name", "version", "source_url", "sha256", "license", "notes", "path", "size"} <= set(item)
        assert item["sha256"] == sha256(files[item["path"]]) and item["size"] == len(files[item["path"]])
    sums = (tmp_path / "b/SHA256SUMS").read_text().splitlines()
    assert [line.split("  ")[1] for line in sums] == sorted(list(files) + ["manifest.json"])
    assert sums_sha == sha256((tmp_path / "b/SHA256SUMS").read_bytes())
    if shutil.which("sha256sum"):
        subprocess.run(["sha256sum", "--quiet", "--strict", "-c", "SHA256SUMS"], cwd=tmp_path / "b", check=True)


def test_manifest_refuses_unknown_or_changed_files(tmp_path):
    root = tmp_path / "b"
    root.mkdir()
    (root / "stray.txt").write_text("x")
    with pytest.raises(bundle.BundleError, match="unexpected"):
        bundle.write_manifest(root, [], {})
    item = bundle.Item("doc", "stray", "1", sha256="0" * 64, path="stray.txt")
    with pytest.raises(bundle.BundleError, match="changed after"):
        bundle.write_manifest(root, [item], {})
    with pytest.raises(bundle.BundleError, match="unsafe path"):
        bundle.write_manifest(root, [bundle.Item("doc", "x", "1", path="../x")], {})


def test_verify_catches_tampering(tmp_path, capsys):
    root = tmp_path / "b"
    sums_sha = make_bundle(root, {"models/m/model.bin": b"weights", "NOTICE": b"notice"})
    assert bundle.verify(root, sums_sha) == 0

    (root / "extra.txt").write_text("x")  # 목록에 없는 파일은 경고만
    assert bundle.verify(root) == 0 and "not in SHA256SUMS (ignored): extra.txt" in capsys.readouterr().err
    assert bundle.verify(root, "0" * 64) == 1

    (root / "models/m/model.bin").write_bytes(b"tampered")
    assert bundle.verify(root) == 1 and "sha256 mismatch: models/m/model.bin" in capsys.readouterr().err
    (root / "models/m/model.bin").unlink()
    assert bundle.verify(root) == 1 and "missing: models/m/model.bin" in capsys.readouterr().err


def test_verify_checks_manifest_against_sums(tmp_path, capsys):
    root = tmp_path / "b"
    make_bundle(root, {"NOTICE": b"notice"})
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["items"][0]["sha256"] = "0" * 64
    (root / "manifest.json").write_text(json.dumps(manifest))
    sums = (root / "SHA256SUMS").read_text()
    sums = re.sub(r"^[0-9a-f]{64}(  manifest\.json)$", sha256((root / "manifest.json").read_bytes()) + r"\1", sums, flags=re.M)
    (root / "SHA256SUMS").write_text(sums + f"{'0' * 64}  ../outside\n")
    assert bundle.verify(root) == 1
    err = capsys.readouterr().err
    assert "list different files or hashes" in err and "line 3 is invalid" in err


# ==================== build ====================

def test_build_refuses_bad_arguments(tmp_path, capsys):
    assert bundle.main(["build", "--out", str(REPO / "bundle")]) == 1
    assert "outside the repository" in capsys.readouterr().err
    (tmp_path / "used").mkdir()
    (tmp_path / "used/x").write_text("x")
    assert bundle.main(["build", "--out", str(tmp_path / "used")]) == 1
    assert "not empty" in capsys.readouterr().err
    assert bundle.main(["build", "--out", str(tmp_path / "a"), "--skip", "imagez"]) == 1
    assert bundle.main(["build", "--out", str(tmp_path / "a"), "--models", "whisper"]) == 1
    assert bundle.main(["build", "--out", str(tmp_path / "a"), "--version", "bad tag"]) == 1


@needs_shell
def test_build_source_only_bundle_and_install(tmp_path):
    """네트워크 단계를 모두 빼고 실제 레포로 번들을 만든 뒤 install.sh로 설치한다(이미지·모델 없음)."""
    out, home = tmp_path / "bundle", tmp_path / "opt"
    args = ["build", "--out", str(out), "--version", "9.9", "--skip", "images,wheels,npm,models", "--allow-dirty",
            "--cache-dir", str(tmp_path / "cache")]
    assert bundle.main(args) == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["skipped"] == ["images", "models", "npm", "wheels"]
    assert sorted(i["path"] for i in manifest["items"]) == ["INSTALL.md", "NOTICE", "install.sh", "source/reco-act-9.9.tar.gz"]
    with tarfile.open(out / "source/reco-act-9.9.tar.gz") as tar:
        names = set(tar.getnames())
    assert "reco-act-9.9/docker-compose.airgap.yaml" in names and "reco-act-9.9/deploy/airgap/airgap.env.example" in names
    # 미추적·ignore 파일(비밀, 녹음, CLAUDE.md)은 들어가지 않는다
    assert not [n for n in names if re.search(r"(^|/)(\.env|prod\.env|prompt\.txt|CLAUDE\.md)$|/records/|\.(m4a|wav)$", n)]
    assert not (tmp_path / "cache/work").exists()
    assert bundle.verify(out) == 0

    result = subprocess.run(["bash", str(out / "install.sh"), "--no-load", "--home", str(home)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "incomplete bundle" in result.stderr
    assert (home / "app/reco-act-9.9/docker-compose.airgap.yaml").is_file()
    compose_env = (home / "compose.env").read_text()
    assert "RECO_ACT_VERSION=9.9\n" in compose_env and f"RECO_ACT_HOME={home}\n" in compose_env
    assert oct((home / "airgap.env").stat().st_mode)[-3:] == "600"


@needs_shell
def test_install_sh_checks_copies_models_and_keeps_settings(tmp_path):
    root, home = tmp_path / "b", tmp_path / "opt"
    source = tmp_path / "src/reco-act-9.9"
    (source / "deploy/airgap").mkdir(parents=True)
    (source / "docker-compose.airgap.yaml").write_text("services: {}\n")
    for name in ("compose", "airgap", "db"):
        shutil.copy2(REPO / f"deploy/airgap/{name}.env.example", source / f"deploy/airgap/{name}.env.example")
    root.mkdir()
    (root / "source").mkdir()
    with tarfile.open(root / "source/reco-act-9.9.tar.gz", "w:gz") as tar:
        tar.add(source, arcname="reco-act-9.9")
    shutil.copy2(REPO / "scripts/airgap/install.sh", root / "install.sh")
    files = {"images/web.tar.gz": b"img", "models/m/model.bin": b"weights", "models/m/sub dir/a b.json": b"{}"}
    items = [bundle.Item("source", "s", "9.9", path="source/reco-act-9.9.tar.gz"), bundle.Item("script", "i", "9.9", path="install.sh")]
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
        items.append(bundle.Item(path.split("/")[0], path, "9.9", path=path))
    sums_sha = bundle.write_manifest(root, items, {"name": "reco-act-airgap", "version": "9.9", "skipped": []})
    log = tmp_path / "docker.log"
    env = dict(os.environ, FAKE_LOG=str(log), PATH=f"{fake_tool(tmp_path, 'docker', FAKE_DOCKER).parent}:{os.environ['PATH']}")

    def install(*args):
        # --home은 상대 경로로 준다(번들 디렉터리로 옮겨 가도 실행한 곳 기준)
        return subprocess.run(["bash", str(root / "install.sh"), "--home", "opt", *args], capture_output=True, text=True,
                              env=env, cwd=tmp_path)

    assert install("--verify-only", "--sums-sha256", sums_sha.upper()).returncode == 0 and not home.exists()
    wrong = install("--sums-sha256", "0" * 64)
    assert wrong.returncode == 1 and "Do not install" in wrong.stderr and not home.exists()

    first = install("--sums-sha256", sums_sha)
    assert first.returncode == 0, first.stderr
    assert "--sums-sha256 not given" not in first.stderr and "(2 copied, 0 already there)" in first.stdout
    assert json.loads(log.read_text().splitlines()[0]) == ["load", "-i", "images/web.tar.gz"]
    assert (home / "models/m/model.bin").read_bytes() == b"weights" and (home / "models/m/sub dir/a b.json").read_bytes() == b"{}"
    for name in ("records", "pgdata", "redis", "tls"):
        assert (home / name).is_dir()
    assert "set RECO_ACT_VERSION" not in first.stdout and "server.crt" in first.stdout

    (home / "airgap.env").write_text("SECRET_KEY=mine\n")  # 현장에서 채운 설정은 그대로 둔다
    (home / "compose.env").write_text("RECO_ACT_VERSION=9.8\n")
    (root / "notes.txt").write_text("copied by someone")
    second = install("--no-load")
    assert second.returncode == 0, second.stderr
    assert "(0 copied, 2 already there)" in second.stdout and "set RECO_ACT_VERSION=9.9" in second.stdout
    assert "notes.txt" in second.stderr and "--sums-sha256 not given" in second.stderr
    assert (home / "airgap.env").read_text() == "SECRET_KEY=mine\n"

    (root / "models/m/model.bin").write_bytes(b"tampered")
    third = install("--no-load")
    assert third.returncode == 1 and "bundle check failed" in third.stderr
    assert (home / "models/m/model.bin").read_bytes() == b"weights"
