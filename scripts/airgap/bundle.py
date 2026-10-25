#!/usr/bin/env python3
"""폐쇄망 반입 번들 도구 (01-plan 6장, PR-16). 인터넷 쪽 PC에서 번들을 만들고(build), 받는 쪽에서 검증한다(verify).

번들 디렉터리(--out)
  manifest.json  항목마다 type, name, version, source_url, sha256, license, notes, path, size
  SHA256SUMS     manifest.json을 포함한 모든 파일. 받는 쪽은 sha256sum -c로 확인한다(install.sh)
  NOTICE, INSTALL.md, install.sh
  source/        이 커밋의 추적 파일. 이미지도 이 사본에서 빌드하므로 작업 트리의 미추적 파일(비밀, 녹음)이 들어가지 않는다
  images/        docker save(gzip). reco-act-web·api·worker(빌드), vLLM·PostgreSQL·Redis(compose의 고정 버전), --base-images면 베이스 이미지
  wheels/        api·worker 이미지에 설치된 패키지 그대로의 wheel(Linux x86_64, CPython 3.10)과 해시 고정 lock
  npm/           client/package-lock.json의 모든 패키지(다른 플랫폼 선택 의존성 포함)를 담은 npm 캐시와 목록
  models/        HF 저장소를 고정 리비전 그대로. 파일마다 HF의 git blob 해시로 대조한다

사용(레포 루트). wheel 해석은 대상과 같은 CPython 3.10·Linux x86_64에서 해야 해서 server venv로 돌린다.
  server/.venv/bin/python scripts/airgap/bundle.py build --out /data/reco-act-bundle-2026.10.25
  python3 scripts/airgap/bundle.py verify /data/reco-act-bundle-2026.10.25
표준 라이브러리만 쓴다(Python 3.8 이상). build에는 git, docker, npm과 인터넷이 필요하다(BuildKit은 없어도 된다).
pyannote(HF 게이트 모델)는 동의한 계정의 HF_TOKEN env나 --models-from(미리 받은 디렉터리)으로 준다. 토큰 값은 출력하지 않는다.
받은 파일은 --cache-dir에 남겨 다시 만들 때 쓴다(sha256으로 확인).
"""
from __future__ import annotations

import argparse
import datetime
import email.parser
import gzip
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHUNK = 1 << 20
DOCKER, GIT, NPM = "docker", "git", "npm"  # 테스트에서 대역으로 바꾼다
STEPS = ("images", "wheels", "npm", "models")
USER_AGENT = "reco-act-bundle/1"
RETRY_DELAY = 2  # 초. 시도마다 늘린다


class BundleError(RuntimeError):
    """번들을 만들거나 검증하다 멈춘 이유. 메시지에 고칠 것을 담는다."""


@dataclass
class Item:
    """manifest 항목. 01-plan 6장의 필드에 번들 안 경로와 크기를 더했다."""
    type: str
    name: str
    version: str
    source_url: str = ""
    sha256: str = ""  # 받을 때 확인한 값. manifest를 쓸 때 다시 계산해 대조한다
    license: str = ""
    notes: str = ""
    path: str = ""
    size: int = 0


# ==================== 공통 ====================

def log(message: str) -> None:
    print(f"[bundle] {message}", flush=True)


def run(cmd, *, capture=False, cwd=None, env=None) -> str:
    try:
        result = subprocess.run(
            [str(c) for c in cmd], cwd=cwd, env=env, text=True, stdout=subprocess.PIPE if capture else None
        )
    except FileNotFoundError:
        raise BundleError(f"{cmd[0]} not found") from None
    if result.returncode != 0:
        raise BundleError(f"command failed ({result.returncode}): {' '.join(str(c) for c in cmd[:4])} ...")
    return result.stdout if capture else ""


def hash_file(path: Path, *, git_blob=False):
    """sha256과, git_blob이면 git blob sha1(헤더 포함)을 함께 낸다."""
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1(b"blob %d\0" % path.stat().st_size) if git_blob else None
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            sha256.update(chunk)
            if sha1:
                sha1.update(chunk)
    return sha256.hexdigest(), (sha1.hexdigest() if sha1 else None)


def sha256_file(path: Path) -> str:
    return hash_file(path)[0]


def link_or_copy(src: Path, dest: Path) -> None:
    """같은 파일 시스템이면 하드 링크(모델·wheel은 수십 GB), 아니면 복사."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def safe_relpath(path: str) -> str:
    """번들 안 상대 경로만 받는다. SHA256SUMS가 이스케이프하는 문자(\\, 줄바꿈)도 막는다."""
    parts = path.split("/")
    if not path or path.startswith("/") or ".." in parts or "" in parts or "\\" in path or "\n" in path:
        raise BundleError(f"unsafe path: {path!r}")
    return path


class _SameHostAuthRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트가 다른 호스트(CDN)로 가면 Authorization을 떼어 HF 토큰이 HF 밖으로 나가지 않게 한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(_SameHostAuthRedirect)


def _request(url: str, token=None, headers=None) -> urllib.request.Request:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    return request


def download(url: str, dest: Path, token=None, attempts=4) -> str:
    """url을 dest로 받고 sha256을 돌려준다. .part로 받다가 끊기면 이어 받는다(Range). 대조는 부르는 쪽이 한다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, attempts + 1):
        offset = part.stat().st_size if part.exists() else 0
        request = _request(url, token, {"Range": f"bytes={offset}-"} if offset else None)
        try:
            with _opener.open(request, timeout=60) as response:
                start = offset if offset and response.status == 206 else 0
                length = response.headers.get("Content-Length")
                with open(part, "ab" if start else "wb") as out:
                    shutil.copyfileobj(response, out, CHUNK)
            # http.client는 연결이 일찍 끊겨도 예외 없이 끝낼 수 있다. 모자라면 이어 받는다
            if length is not None and part.stat().st_size != start + int(length):
                raise http.client.IncompleteRead(b"")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 416:  # 이어 받을 위치가 맞지 않는다: 처음부터
                part.unlink()
            elif exc.code in (401, 403, 404) or attempt == attempts:
                raise BundleError(f"download failed: {url} (HTTP {exc.code})") from None
        except (OSError, http.client.HTTPException) as exc:
            if attempt == attempts:
                raise BundleError(f"download failed: {url} ({exc})") from None
        if attempt < attempts:
            log(f"retry {attempt}/{attempts - 1}: {url}")
            time.sleep(RETRY_DELAY * attempt)
    else:
        raise BundleError(f"download failed: {url}")
    digest = sha256_file(part)
    part.replace(dest)
    return digest


# ==================== source: 이 커밋의 추적 파일 ====================

def snapshot(repo: Path, dest: Path, allow_dirty: bool):
    """추적 파일을 dest에 복사하고 (커밋, 변경 여부)를 돌려준다. 미추적·ignore 파일은 넣지 않는다."""
    commit = run([GIT, "rev-parse", "HEAD"], capture=True, cwd=repo).strip()
    dirty = bool(run([GIT, "status", "--porcelain", "--untracked-files=no"], capture=True, cwd=repo).strip())
    if dirty and not allow_dirty:
        raise BundleError("tracked files have uncommitted changes. Commit first (--allow-dirty is for rehearsals only)")
    for rel in run([GIT, "ls-files", "-z"], capture=True, cwd=repo).split("\0"):
        if not rel:
            continue
        src, target = repo / rel, dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), target)
        elif src.is_file():
            shutil.copy2(src, target)
        # 지운 추적 파일(--allow-dirty)은 넣지 않는다
    return commit, dirty


def collect_source(source_dir: Path, bundle: Path, version: str, commit: str, dirty: bool) -> Item:
    path = f"source/reco-act-{version}.tar.gz"
    (bundle / "source").mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle / path, "w:gz") as tar:
        tar.add(source_dir, arcname=f"reco-act-{version}")
    count = sum(1 for p in source_dir.rglob("*") if p.is_file())
    notes = f"추적 파일 {count}개. 이미지는 이 사본에서 빌드했다"
    if dirty:
        notes += ". 커밋하지 않은 변경 포함(--allow-dirty, 리허설용)"
    return Item("source", "reco-act", version, f"git {commit}", license="자체 코드", notes=notes, path=path)


# ==================== images ====================

COMPOSE_FILE = "docker-compose.airgap.yaml"
DOCKERFILE = "Dockerfile.airgap"
# compose의 reco-act-* 이미지는 source/ 사본에서 빌드한다(compose의 build 항목과 같다, 테스트로 확인). 나머지는 받는다.
BUILDS = {
    "reco-act-web": ("client", None),
    "reco-act-api-airgap": ("server", "api"),
    "reco-act-worker": ("server", "worker"),
}
IMAGE_LICENSES = {
    "vllm/vllm-openai": "Apache-2.0",
    "postgres": "PostgreSQL",
    "redis": "BSD-3-Clause",  # 7.2 계열. 7.4부터 RSALv2/SSPLv1(01-plan R14)
    "python": "PSF-2.0",
    "node": "MIT",
    "nginx": "BSD-2-Clause",
    "nvidia/cuda": "NVIDIA Deep Learning Container License(재배포 조건 확인 필요, 01-plan 9장)",
}


def compose_images(text: str, env: dict) -> list:
    """compose 파일의 image 값을 ${VAR:-기본값}을 풀어 등장 순서대로 돌려준다."""
    def substitute(match):
        return env.get(match.group(1)) or match.group(3) or ""

    refs = []
    for line in text.splitlines():
        found = re.match(r"\s+image:\s*(\S+)\s*$", line)
        if found:
            ref = re.sub(r"\$\{(\w+)(:-([^}]*))?\}", substitute, found.group(1).strip("\"'"))
            if ref not in refs:
                refs.append(ref)
    return refs


def dockerfile_stages(text: str) -> list:
    """[(FROM 이미지, 스테이지 이름 또는 None)]"""
    stages = []
    for line in text.splitlines():
        found = re.match(r"\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", line, re.IGNORECASE)
        if found:
            stages.append((found.group(1), found.group(2)))
    return stages


def base_images(text: str) -> list:
    """Dockerfile이 받는 외부 이미지(앞 스테이지를 가리키는 FROM은 뺀다)."""
    names, refs = set(), []
    for ref, stage in dockerfile_stages(text):
        if ref not in names and ref not in refs:
            refs.append(ref)
        if stage:
            names.add(stage)
    return refs


def stage_base(text: str, target=None) -> str:
    """target 스테이지(없으면 마지막)가 결국 올라탄 외부 이미지."""
    stages = dockerfile_stages(text)
    by_name = {stage: ref for ref, stage in stages if stage}
    ref = by_name[target] if target else stages[-1][0]
    while ref in by_name:
        ref = by_name[ref]
    return ref


def split_ref(ref: str):
    name, sep, tag = ref.rpartition(":")
    if not sep or "/" in tag:
        return ref, "latest"
    return name, tag


def image_file(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", ref) + ".tar.gz"


def image_license(ref: str) -> str:
    name = split_ref(ref)[0]
    return IMAGE_LICENSES.get(name.replace("docker.io/", "").replace("library/", ""), "확인 필요")


def docker_inspect(ref: str) -> dict:
    info = json.loads(run([DOCKER, "image", "inspect", "--format", "{{json .}}", ref], capture=True))
    if (info.get("Os"), info.get("Architecture")) != ("linux", "amd64"):
        raise BundleError(f"{ref} is {info.get('Os')}/{info.get('Architecture')}; the airgap server needs linux/amd64")
    return info


def save_image(ref: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with subprocess.Popen([DOCKER, "save", ref], stdout=subprocess.PIPE) as proc:
        with open(part, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1, mtime=0) as out:
            shutil.copyfileobj(proc.stdout, out, CHUNK)
    if proc.returncode != 0:
        part.unlink()
        raise BundleError(f"docker save {ref} failed ({proc.returncode})")
    part.replace(dest)


def collect_images(source_dir: Path, bundle: Path, version: str, commit: str, with_base: bool):
    """이미지를 빌드·받아 저장한다. (항목, {빌드한 이미지 이름: ref})"""
    refs = compose_images((source_dir / COMPOSE_FILE).read_text(encoding="utf-8"), {"RECO_ACT_VERSION": version})
    built = {}
    for ref in refs:
        name = split_ref(ref)[0]
        if name in BUILDS:
            context, target = BUILDS[name]
            log(f"build {ref}")
            cmd = [DOCKER, "build", "--platform", "linux/amd64", "-f", source_dir / context / DOCKERFILE, "-t", ref,
                   "--label", f"org.opencontainers.image.revision={commit}",
                   "--label", f"org.opencontainers.image.version={version}"]
            # 추적 파일 사본에서 빌드하므로 BuildKit 전용 ignore 파일이 없어도 된다. buildx가 없는 Docker(Synology 등)는
            # 기존 빌더로 빌드한다(worker를 빌드할 때 앞 스테이지까지 만든다). BuildKit을 강제하면 그런 곳에서 실패한다.
            run(cmd + (["--target", target] if target else []) + [source_dir / context])
            built[name] = ref
        else:
            log(f"pull {ref}")
            run([DOCKER, "pull", "--platform", "linux/amd64", ref])
    if set(built) != set(BUILDS):
        raise BundleError(f"{COMPOSE_FILE} images changed: built {sorted(built)}, expected {sorted(BUILDS)}")
    extra = []
    if with_base:
        for context in ("server", "client"):
            for ref in base_images((source_dir / context / DOCKERFILE).read_text(encoding="utf-8")):
                if ref not in refs + extra:
                    log(f"pull {ref} (base image)")
                    run([DOCKER, "pull", "--platform", "linux/amd64", ref])
                    extra.append(ref)

    items = []
    for ref in refs + extra:
        info = docker_inspect(ref)
        path = f"images/{image_file(ref)}"
        log(f"save {ref} -> {path}")
        save_image(ref, bundle / path)
        name, tag = split_ref(ref)
        if name in BUILDS:
            context, target = BUILDS[name]
            base = stage_base((source_dir / context / DOCKERFILE).read_text(encoding="utf-8"), target)
            source = f"git {commit} {context}/{DOCKERFILE}" + (f" --target {target}" if target else "")
            license = f"자체 코드 + 베이스 {base}({image_license(base)}). 포함 패키지는 wheel·npm 항목"
            notes = f"image id {info['Id']}"
        else:
            source = (info.get("RepoDigests") or [ref])[0]
            license = image_license(ref)
            notes = f"image id {info['Id']}" + (". 베이스 이미지(폐쇄망 안에서 다시 빌드할 때만)" if ref in extra else "")
        items.append(Item("image", name, tag, source, license=license, notes=notes, path=path))
    return items, built


# ==================== wheels ====================

PYPI = "https://pypi.org/simple"
TORCH_CU128 = "https://download.pytorch.org/whl/cu128"
# (이름, 요구 파일, 이미지, 인덱스). Dockerfile.airgap의 pip install과 같다(테스트로 확인).
# 이미지를 빌드했으면 그 안의 pip freeze를 기준으로 하고, 없으면(--skip images) 요구 파일을 새로 해석한다.
WHEEL_SETS = (
    ("api", "server/requirements.txt", "reco-act-api-airgap", (PYPI,)),
    ("worker", "server/requirements-airgap.txt", "reco-act-worker", (TORCH_CU128, PYPI)),
)
# 대상: api는 Debian bookworm(glibc 2.36), worker는 Ubuntu 22.04(glibc 2.35). pip의 --platform은 준 태그만 맞추므로
# (하위 glibc 태그로 넓히지 않는다, pip 25.1에서 확인) 2.35부터 아래 태그를 모두 준다.
PLATFORMS = [f"manylinux_2_{minor}_x86_64" for minor in range(35, 4, -1)] + [
    "manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"]


@dataclass
class Wheel:
    name: str
    version: str
    filename: str
    url: str
    sha256: str
    license: str
    yanked: bool = False


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def license_title(text: str):
    """라이선스 본문의 첫 줄이 이름("MIT License", "BSD 3-Clause License")이면 그 줄."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0] if lines and len(lines[0]) <= 80 and "license" in lines[0].lower() else None


def license_of(meta: dict) -> str:
    """패키지 메타데이터의 라이선스: License-Expression, 짧은 License, 분류자, License 본문의 첫 줄 순."""
    expression = (meta.get("license_expression") or "").strip()
    if expression:
        return expression
    text = (meta.get("license") or "").strip()
    if text and "\n" not in text and len(text) <= 80 and text.upper() != "UNKNOWN":
        return text
    classifiers = [c.split(" :: ")[-1] for c in meta.get("classifier", []) if c.startswith("License ::")]
    return "; ".join(classifiers) or license_title(text) or "UNKNOWN"


def wheel_license(path: Path):
    """pip 보고서에 라이선스가 없을 때 wheel에서 직접 읽는다.
    METADATA의 License-Expression 등(pip 23은 이 필드를 보고서에 넣지 않는다), 없으면 dist-info 라이선스 파일의 첫 줄(PEP 639의 License-File만 둔 wheel)."""
    with zipfile.ZipFile(path) as wheel:
        names = sorted(wheel.namelist())
        metadata = next((n for n in names if re.fullmatch(r"[^/]+\.dist-info/METADATA", n)), None)
        if metadata:
            headers = email.parser.HeaderParser().parsestr(wheel.read(metadata).decode("utf-8", "replace"))
            found = license_of({"license_expression": headers.get("License-Expression"), "license": headers.get("License"),
                                "classifier": headers.get_all("Classifier") or []})
            if found != "UNKNOWN":
                return found
        for name in names:
            if re.search(r"\.dist-info/(licenses/.+|LICEN[CS]E[^/]*|COPYING[^/]*)$", name) and not name.endswith("/"):
                title = license_title(wheel.read(name).decode("utf-8", "replace"))
                if title:
                    return f"{title} (LICENSE 파일)"
    return None


def wheels_from_report(report: dict) -> list:
    """pip install --report(형식 1)의 설치 목록을 Wheel로 바꾼다."""
    if str(report.get("version")) != "1":
        raise BundleError(f"unsupported pip report version {report.get('version')!r} (pip 23 or newer)")
    wheels = []
    for entry in report["install"]:
        meta, info = entry["metadata"], entry["download_info"]
        url = info["url"]
        archive = info.get("archive_info") or {}
        sha256 = (archive.get("hashes") or {}).get("sha256")
        if not sha256 and str(archive.get("hash", "")).startswith("sha256="):
            sha256 = archive["hash"].split("=", 1)[1]
        filename = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
        if not filename.endswith(".whl") or not sha256:
            raise BundleError(f"{meta['name']}: need a wheel with sha256, got {url}")
        wheels.append(Wheel(meta["name"], meta["version"], filename, url, sha256, license_of(meta), bool(entry.get("is_yanked"))))
    return wheels


def parse_freeze(text: str) -> list:
    reqs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==\S+", line):
            raise BundleError(f"pip freeze has an entry that is not name==version: {line}")
        reqs.append(line)
    return reqs


def check_pip_python(python: str) -> None:
    probe = "import platform, sys; print(sys.implementation.name, '%d.%d' % sys.version_info[:2], platform.system(), platform.machine())"
    found = run([python, "-c", probe], capture=True).split()
    if found != ["cpython", "3.10", "Linux", "x86_64"]:
        raise BundleError(
            f"wheel resolution needs CPython 3.10 on Linux x86_64 like the images (pip evaluates markers on the host), "
            f"got {' '.join(found)}. Run with server/.venv/bin/python or pass --pip-python")


def resolve_wheels(python: str, requirements: Path, indexes, no_deps: bool, workdir: Path) -> list:
    report = workdir / f"pip-report-{requirements.stem}.json"
    cmd = [python, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--quiet", "--disable-pip-version-check",
           "--report", report, "--target", workdir / "pip-target", "--only-binary=:all:",
           "--python-version", "3.10", "--implementation", "cp", "--abi", "cp310"]
    for platform in PLATFORMS:
        cmd += ["--platform", platform]
    cmd += ["--index-url", indexes[0]]
    for index in indexes[1:]:
        cmd += ["--extra-index-url", index]
    log(f"resolve {requirements.name}" + (" (pinned, no deps)" if no_deps else ""))
    run(cmd + (["--no-deps"] if no_deps else []) + ["-r", requirements])
    return wheels_from_report(json.loads(report.read_text(encoding="utf-8")))


def image_freeze(ref: str) -> list:
    # nvidia/cuda 베이스의 ENTRYPOINT는 배너를 stdout에 찍으므로 python을 entrypoint로 바로 부른다.
    return parse_freeze(run([DOCKER, "run", "--rm", "--network", "none", "--entrypoint", "python", ref,
                             "-m", "pip", "freeze", "--all"], capture=True))


def lock_text(set_name: str, basis: str, wheels) -> str:
    lines = [
        f"# reco-act {set_name} 이미지의 Python 패키지({basis}).",
        f"# 폐쇄망에서: pip install --no-index --find-links wheels --require-hashes -r wheels/requirements-{set_name}.lock.txt",
    ]
    lines += [f"{w.name}=={w.version} --hash=sha256:{w.sha256}" for w in sorted(wheels, key=lambda w: normalize(w.name))]
    return "\n".join(lines) + "\n"


def collect_wheels(python: str, source_dir: Path, bundle: Path, cache: Path, version: str, built: dict, workdir: Path):
    check_pip_python(python)
    items, files, sets = [], {}, {}
    for set_name, requirements, image, indexes in WHEEL_SETS:
        if image in built:
            pinned = image_freeze(built[image])
            req = workdir / f"freeze-{set_name}.txt"
            req.write_text("\n".join(pinned) + "\n", encoding="utf-8")
            wheels = resolve_wheels(python, req, indexes, True, workdir)
            missing = {normalize(r.split("==")[0]) for r in pinned} - {normalize(w.name) for w in wheels}
            if missing:
                raise BundleError(f"no wheel for {set_name} image packages: {sorted(missing)}")
            basis = f"{built[image]} 이미지의 pip freeze"
        else:
            wheels = resolve_wheels(python, source_dir / requirements, indexes, False, workdir)
            basis = f"{requirements}을 해석, 이미지 없이 만든 번들"
        lock = f"wheels/requirements-{set_name}.lock.txt"
        (bundle / "wheels").mkdir(parents=True, exist_ok=True)
        (bundle / lock).write_text(lock_text(set_name, basis, wheels), encoding="utf-8")
        items.append(Item("lock", f"requirements-{set_name}.lock", version, license="자체", notes=basis, path=lock))
        for wheel in wheels:
            previous = files.setdefault(wheel.filename, wheel)
            if previous.sha256 != wheel.sha256:
                raise BundleError(f"{wheel.filename}: different sha256 in api and worker resolutions")
            sets.setdefault(wheel.filename, []).append(set_name)

    log(f"download {len(files)} wheels")
    for filename, wheel in sorted(files.items()):
        cached = cache / "wheels" / filename
        if not (cached.is_file() and sha256_file(cached) == wheel.sha256):
            if download(wheel.url, cached) != wheel.sha256:
                cached.unlink()
                raise BundleError(f"sha256 mismatch: {wheel.url}")
        link_or_copy(cached, bundle / "wheels" / filename)
        license = wheel.license if wheel.license != "UNKNOWN" else wheel_license(cached) or "UNKNOWN"
        notes = f"{', '.join(sets[filename])} 이미지" + (". 인덱스에서 철회(yanked)된 버전" if wheel.yanked else "")
        items.append(Item("wheel", wheel.name, wheel.version, wheel.url, wheel.sha256, license, notes, f"wheels/{filename}"))
    return items


# ==================== npm ====================

@dataclass
class NpmPackage:
    name: str
    version: str
    license: str
    resolved: str
    integrity: str


def npm_packages(lock: dict) -> list:
    """package-lock.json(v2·v3)의 모든 패키지. 이 PC와 다른 플랫폼의 선택 의존성(esbuild, rollup 등)도 넣는다."""
    packages = []
    for key, entry in lock["packages"].items():
        if not key or entry.get("link"):
            continue  # 루트, 작업 공간 링크
        name = entry.get("name") or key.rsplit("node_modules/", 1)[-1]
        if not entry.get("resolved") or not entry.get("integrity"):
            raise BundleError(f"package-lock.json: {name} has no resolved/integrity")
        packages.append(NpmPackage(name, entry["version"], entry.get("license") or "UNKNOWN",
                                   entry["resolved"], entry["integrity"]))
    return packages


def license_summary(licenses) -> str:
    return ", ".join(f"{name} {count}" for name, count in Counter(licenses).most_common())


def collect_npm(source_dir: Path, bundle: Path, workdir: Path):
    """npm 캐시를 채운다. 온라인 npm ci가 받는 것(lock이 다 담지 못한 peer 의존성의 레지스트리 메타데이터 포함)에
    lock의 모든 tarball을 더하고, 새 디렉터리에서 npm ci --offline이 되는지 확인한다."""
    client = source_dir / "client"
    lock = json.loads((client / "package-lock.json").read_text(encoding="utf-8"))
    packages = npm_packages(lock)
    cache = workdir / "npm-cache"
    env = dict(os.environ, npm_config_update_notifier="false")
    flags = ["--ignore-scripts", "--no-audit", "--no-fund", "--cache", cache]

    def project(name):
        target = workdir / name
        target.mkdir()
        for file in ("package.json", "package-lock.json"):
            shutil.copy2(client / file, target / file)
        return target

    log("npm ci (fill cache)")
    run([NPM, "ci", *flags], cwd=project("npm-online"), env=env)
    urls = sorted({p.resolved for p in packages})
    log(f"npm cache add {len(urls)} tarballs")
    for start in range(0, len(urls), 40):
        run([NPM, "cache", "add", "--cache", cache, *urls[start:start + 40]], env=env)
    keys = run([NPM, "cache", "ls", "--cache", cache], capture=True, env=env).splitlines()
    cached = {key.strip().split("make-fetch-happen:request-cache:", 1)[-1] for key in keys}
    missing = [url for url in urls if url not in cached]
    if missing:
        raise BundleError(f"npm cache is missing {len(missing)} tarballs, e.g. {missing[0]}")
    log("npm ci --offline (check cache)")
    run([NPM, "ci", "--offline", *flags], cwd=project("npm-offline"), env=env)

    (bundle / "npm").mkdir(parents=True, exist_ok=True)
    skip = {"_logs", "_update-notifier-last-checked"}
    with tarfile.open(bundle / "npm/npm-cache.tar.gz", "w:gz") as tar:
        tar.add(cache, arcname="npm-cache",
                filter=lambda info: None if info.name.split("/")[1:2] and info.name.split("/")[1] in skip else info)
    rows = sorted({(p.name, p.version, p.license, p.resolved, p.integrity) for p in packages})
    (bundle / "npm/packages.tsv").write_text(
        "name\tversion\tlicense\tresolved\tintegrity\n" + "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    lock_sha = sha256_file(client / "package-lock.json")
    summary = license_summary(r[2] for r in rows)
    return [
        Item("npm-cache", "client", lock.get("version", ""), "https://registry.npmjs.org/ (package-lock.json의 resolved)",
             license=summary, path="npm/npm-cache.tar.gz",
             notes=f"package-lock.json(sha256 {lock_sha[:12]})의 패키지 {len(rows)}개, 모든 플랫폼. "
                   "웹 이미지에는 빌드한 dist만 들어 있고 이 캐시는 폐쇄망에서 다시 빌드할 때 쓴다(npm ci --offline --cache)"),
        Item("list", "npm packages", lock.get("version", ""), license=summary, path="npm/packages.tsv",
             notes="패키지마다 이름, 버전, 라이선스, 출처, integrity(sha512)"),
    ]


# ==================== models ====================

HF = "https://huggingface.co"


@dataclass(frozen=True)
class Model:
    dir: str  # RECO_ACT_HOME/models 아래 이름(compose.env의 *_DIR 기본값)
    repo: str
    revision: str  # HF 커밋. 바꿀 때는 모델 카드(라이선스)를 다시 확인하고 NOTICE도 고친다
    license: str  # 모델 카드의 license. 만들 때마다 카드와 대조해 다르면 멈춘다(00-decisions 3장)
    required: tuple  # 앱이 확인하는 파일(어댑터의 필수 파일 목록과 같다, 테스트로 확인)
    notes: str


MODELS = (
    Model("faster-whisper-large-v3", "Systran/faster-whisper-large-v3", "edaa852ec7e145841d8ffdb056a99866b5f0a478", "MIT",
          ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"),
          "STT. Whisper large-v3(OpenAI, 원 모델 카드 apache-2.0)의 CTranslate2 변환본. "
          "preprocessor_config.json(멜 128개)이 있어야 한다(01-plan 9.2)"),
    Model("speaker-diarization-community-1", "pyannote/speaker-diarization-community-1",
          "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee", "CC-BY-4.0",
          ("config.yaml", "segmentation/pytorch_model.bin", "embedding/pytorch_model.bin", "plda/plda.npz",
           "plda/xvec_transform.npz"),
          "화자분리. HF 게이트 모델(동의한 계정으로 받는다). 저작자 표시는 NOTICE"),
    Model("Qwen3-32B-AWQ", "Qwen/Qwen3-32B-AWQ", "0499c3ac83fdef8810b907a23894ba91e95eddd8", "Apache-2.0",
          ("config.json", "model.safetensors.index.json", "tokenizer.json", "LICENSE"),
          "LLM(vLLM). 공식 AWQ(INT4) 체크포인트"),
)


def hf_json(path: str, token=None):
    try:
        with _opener.open(_request(f"{HF}/api/{path}", token), timeout=60) as response:
            if 'rel="next"' in response.headers.get("Link", ""):
                raise BundleError(f"{path}: paginated listing is not supported")
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise BundleError(f"HF API {path}: HTTP {exc.code}") from None
    except (OSError, http.client.HTTPException) as exc:
        raise BundleError(f"HF API {path}: {exc}") from None


def hf_file_sha256(entry: dict, path: Path):
    """path가 HF tree 항목(git blob)과 같은 파일이면 sha256을, 아니면 None을 돌려준다.
    LFS 파일은 공개된 sha256과 대조하고, 게이트 저장소처럼 가려져 있으면 LFS 포인터를 다시 만들어 git blob 해시와 대조한다."""
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return None
    lfs = entry.get("lfs")
    sha256, blob = hash_file(path, git_blob=not lfs)
    if lfs:
        if re.fullmatch(r"[0-9a-f]{64}", lfs.get("oid", "")):
            return sha256 if lfs["oid"] == sha256 else None
        pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{sha256}\nsize {entry['size']}\n".encode()
        blob = hashlib.sha1(b"blob %d\0" % len(pointer) + pointer).hexdigest()
    return sha256 if blob == entry["oid"] else None


def collect_models(models, bundle: Path, cache: Path, sources, token):
    items = []
    for model in models:
        info = hf_json(f"models/{model.repo}/revision/{model.revision}", token)
        card_license = str((info.get("cardData") or {}).get("license") or "")
        if info.get("sha") != model.revision or card_license.lower() != model.license.lower():
            raise BundleError(f"{model.repo}@{model.revision[:12]}: revision {info.get('sha')}, model card license "
                              f"{card_license!r} (expected {model.license}). Check the card, then update MODELS and NOTICE")
        files = [e for e in hf_json(f"models/{model.repo}/tree/{model.revision}?recursive=true", token) if e["type"] == "file"]
        missing = sorted(set(model.required) - {e["path"] for e in files})
        if missing:
            raise BundleError(f"{model.repo}@{model.revision[:12]} has no {missing}")
        log(f"model {model.dir} ({len(files)} files, {sum(e['size'] for e in files) / 1e9:.2f} GB)")
        for entry in files:
            rel = safe_relpath(entry["path"])
            url = f"{HF}/{model.repo}/resolve/{model.revision}/{urllib.parse.quote(rel)}"
            cached = cache / "models" / model.repo / model.revision / rel
            for candidate in [Path(d).expanduser() / model.dir / rel for d in sources] + [cached]:
                sha256 = hf_file_sha256(entry, candidate)
                if sha256:
                    break
            else:
                if info.get("gated") and not token:
                    raise BundleError(f"{model.repo} is gated: set HF_TOKEN (an account that accepted its terms) "
                                      f"or pass --models-from with a downloaded {model.dir}/")
                log(f"download {model.dir}/{rel}")
                download(url, cached, token)
                sha256 = hf_file_sha256(entry, cached)
                if not sha256:
                    cached.unlink()
                    raise BundleError(f"downloaded file does not match the HF listing: {url}")
                candidate = cached
            path = f"models/{model.dir}/{rel}"
            link_or_copy(candidate, bundle / path)
            items.append(Item("model", model.dir, model.revision, url, sha256, model.license, model.notes, path))
    return items


# ==================== manifest ====================

BUNDLE_DOCS = (  # (번들 안 이름, 레포 경로, type)
    ("NOTICE", "NOTICE", "doc"),
    ("INSTALL.md", "scripts/airgap/INSTALL.md", "doc"),
    ("install.sh", "scripts/airgap/install.sh", "script"),
)
SUMS_LINE = re.compile(r"([0-9a-f]{64}) [ *](.+)")


def collect_docs(bundle: Path, version: str):
    items = []
    for name, source, kind in BUNDLE_DOCS:
        shutil.copy2(REPO / source, bundle / name)
        items.append(Item(kind, name, version, license="자체", path=name))
    (bundle / "install.sh").chmod(0o755)
    return items


def bundle_files(bundle: Path) -> list:
    return sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())


def write_manifest(bundle: Path, items, meta: dict) -> str:
    """manifest.json과 SHA256SUMS를 쓰고 SHA256SUMS의 sha256을 돌려준다. 번들의 파일과 항목이 정확히 맞아야 한다."""
    by_path = {}
    for item in items:
        if safe_relpath(item.path) in by_path:
            raise BundleError(f"two items for {item.path}")
        by_path[item.path] = item
    files = [p for p in bundle_files(bundle) if p not in ("manifest.json", "SHA256SUMS")]
    unexpected, missing = sorted(set(files) - set(by_path)), sorted(set(by_path) - set(files))
    if unexpected or missing:
        raise BundleError(f"bundle files and items differ: unexpected {unexpected[:5]}, missing {missing[:5]}")
    log(f"sha256 of {len(files)} files")
    for path in files:
        item = by_path[path]
        digest = sha256_file(bundle / path)
        if item.sha256 and item.sha256 != digest:
            raise BundleError(f"{path} changed after it was checked")
        item.sha256, item.size = digest, (bundle / path).stat().st_size
    manifest = dict(meta, items=[asdict(by_path[p]) for p in files])
    (bundle / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sums = {p: by_path[p].sha256 for p in files}
    sums["manifest.json"] = sha256_file(bundle / "manifest.json")
    (bundle / "SHA256SUMS").write_text("".join(f"{sums[p]}  {p}\n" for p in sorted(sums)), encoding="utf-8")
    return sha256_file(bundle / "SHA256SUMS")


def verify(bundle: Path, sums_sha256=None) -> int:
    """SHA256SUMS·manifest.json과 파일을 대조한다. 문제가 있으면 1. 목록에 없는 파일은 경고만 한다."""
    problems, sums_path = [], bundle / "SHA256SUMS"
    if not sums_path.is_file():
        print(f"verify: {sums_path} not found", file=sys.stderr)
        return 1
    if sums_sha256 and sha256_file(sums_path) != sums_sha256.strip().lower():
        print("verify: SHA256SUMS does not match --sums-sha256; nothing else can be trusted", file=sys.stderr)
        return 1
    entries = {}
    for number, line in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), 1):
        found = SUMS_LINE.fullmatch(line)
        try:
            entries[safe_relpath(found.group(2))] = found.group(1)
        except (AttributeError, BundleError):
            problems.append(f"SHA256SUMS line {number} is invalid: {line!r}")
    if "manifest.json" not in entries:
        problems.append("SHA256SUMS does not list manifest.json")
    else:
        try:
            listed = {i["path"]: i["sha256"] for i in json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["items"]}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            listed = None
            problems.append(f"manifest.json is unreadable: {exc}")
        if listed is not None and listed != {p: s for p, s in entries.items() if p != "manifest.json"}:
            problems.append("manifest.json and SHA256SUMS list different files or hashes")
    total = sum((bundle / p).stat().st_size for p in entries if (bundle / p).is_file())
    print(f"verify: {len(entries)} files, {total / 1e9:.2f} GB", flush=True)
    for path, expected in sorted(entries.items()):
        if not (bundle / path).is_file():
            problems.append(f"missing: {path}")
        elif sha256_file(bundle / path) != expected:
            problems.append(f"sha256 mismatch: {path}")
    extra = sorted(set(bundle_files(bundle)) - set(entries) - {"SHA256SUMS"})
    for path in extra:
        print(f"verify: warning: not in SHA256SUMS (ignored): {path}", file=sys.stderr)
    for problem in problems:
        print(f"verify: {problem}", file=sys.stderr)
    print("verify: FAILED" if problems else "verify: OK")
    return 1 if problems else 0


# ==================== build ====================

def build(args) -> int:
    out = args.out.expanduser().resolve()
    if out == REPO or REPO in out.parents:
        raise BundleError("--out must be outside the repository (the bundle is tens of GB)")
    if out.exists() and any(out.iterdir()):
        raise BundleError(f"{out} is not empty. Use a new directory (downloads are reused from --cache-dir)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.version):
        raise BundleError(f"--version {args.version!r} is not a valid image tag")
    skip = {s for s in args.skip.split(",") if s}
    names = [n for n in args.models.split(",") if n]
    unknown = (skip - set(STEPS)) | (set(names) - {m.dir for m in MODELS})
    if unknown:
        raise BundleError(f"unknown --skip/--models value: {sorted(unknown)}")
    models = [m for m in MODELS if m.dir in names]
    cache = args.cache_dir.expanduser().resolve()
    workdir = cache / "work"
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    out.mkdir(parents=True, exist_ok=True)
    try:
        source_dir = workdir / "source"
        commit, dirty = snapshot(REPO, source_dir, args.allow_dirty)
        log(f"source {commit[:12]}{' (dirty)' if dirty else ''}")
        items = [collect_source(source_dir, out, args.version, commit, dirty)]
        built = {}
        if "images" not in skip:
            image_items, built = collect_images(source_dir, out, args.version, commit, args.base_images)
            items += image_items
        if "wheels" not in skip:
            items += collect_wheels(args.pip_python, source_dir, out, cache, args.version, built, workdir)
        if "npm" not in skip:
            items += collect_npm(source_dir, out, workdir)
        if "models" not in skip:
            items += collect_models(models, out, cache, args.models_from, os.environ.get("HF_TOKEN") or None)
        items += collect_docs(out, args.version)
        skipped = sorted(skip) + (["model:" + m.dir for m in MODELS if m not in models] if "models" not in skip else [])
        meta = {
            "name": "reco-act-airgap",
            "version": args.version,
            "created_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "git_commit": commit,
            "git_dirty": dirty,
            "skipped": skipped,
        }
        sums = write_manifest(out, items, meta)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    size = sum((out / p).stat().st_size for p in bundle_files(out))
    log(f"done: {out} ({len(items) + 2} files, {size / 1e9:.2f} GB)")
    if skipped:
        log(f"INCOMPLETE bundle, not for installation: skipped {', '.join(skipped)}")
    log(f"sha256 of SHA256SUMS: {sums}")
    log("send this value separately from the bundle; the site checks it with install.sh --sums-sha256")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="reco-act airgap bundle: build on the internet side, verify on the site")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build a bundle into an empty directory outside the repository")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--version", default=datetime.date.today().strftime("%Y.%m.%d"), help="image tag (default: today)")
    b.add_argument("--skip", default="", help=f"comma-separated steps to leave out (rehearsals): {','.join(STEPS)}")
    b.add_argument("--models", default=",".join(m.dir for m in MODELS), help="comma-separated model dirs to include")
    b.add_argument("--models-from", type=Path, action="append", default=[],
                   help="directory with already downloaded model dirs (checked against HF); repeatable")
    b.add_argument("--base-images", action="store_true", help="also save base images (only for rebuilding on the site)")
    b.add_argument("--allow-dirty", action="store_true", help="allow uncommitted changes (rehearsals only)")
    b.add_argument("--pip-python", default=sys.executable, help="CPython 3.10 on Linux x86_64 that runs pip")
    b.add_argument("--cache-dir", type=Path, default=Path("~/.cache/reco-act/bundle"))
    v = sub.add_parser("verify", help="check a bundle against SHA256SUMS and manifest.json")
    v.add_argument("bundle", type=Path)
    v.add_argument("--sums-sha256", help="sha256 of SHA256SUMS received separately from the bundle")
    args = parser.parse_args(argv)
    try:
        return build(args) if args.command == "build" else verify(args.bundle, args.sums_sha256)
    except BundleError as exc:
        print(f"[bundle] failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
