"""Swagger UI 정적 파일 받기(scripts/fetch_swagger_ui.py). 네트워크 없이 체크섬 확인과 압축 풀기만 본다."""
import hashlib
import io
import tarfile

import pytest

from scripts import fetch_swagger_ui as fetch


def make_tarball(names):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in names:
            data = f"content of {name}".encode()
            info = tarfile.TarInfo(f"package/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_pinned_version_and_checksum():
    assert fetch.VERSION == "5.9.0"  # FastAPI 0.104 기본 CDN과 같은 버전
    assert fetch.URL.endswith("/swagger-ui-dist-5.9.0.tgz") and fetch.URL.startswith("https://registry.npmjs.org/")
    assert len(fetch.SHA256) == 64 and int(fetch.SHA256, 16) >= 0


def test_extracts_only_the_needed_files(tmp_path, monkeypatch):
    tarball = make_tarball(list(fetch.FILES) + ["index.html", "swagger-ui.js"])
    monkeypatch.setattr(fetch, "SHA256", hashlib.sha256(tarball).hexdigest())

    fetch.extract(tarball, tmp_path / "out")

    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(fetch.FILES)
    assert (tmp_path / "out" / "swagger-ui.css").read_text() == "content of swagger-ui.css"


def test_checksum_mismatch_writes_nothing(tmp_path):
    with pytest.raises(ValueError, match="checksum mismatch"):
        fetch.extract(make_tarball(fetch.FILES), tmp_path / "out")
    assert not (tmp_path / "out").exists()
