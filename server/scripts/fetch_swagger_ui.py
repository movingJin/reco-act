"""Swagger UI 정적 파일을 받아 둔다 (airgap, 2026-10-24 결정). 이미지를 빌드할 때(인터넷 쪽) 한 번 실행한다.

airgap API는 Swagger UI를 CDN 대신 이 파일들로 띄운다(main.py). FastAPI 0.104의 기본 CDN과 같은 swagger-ui-dist 5.9.0이다.
npm 레지스트리의 tarball을 받아 SHA256을 대조하고, 필요한 파일만 꺼낸다. 표준 라이브러리만 쓴다.

사용: python -m scripts.fetch_swagger_ui <대상 디렉터리, 기본 static/swagger-ui>
"""
import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = "5.9.0"
URL = f"https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-{VERSION}.tgz"
SHA256 = "09886a8f40a1cc2ebc5e87374b4cb4026f7f21a4c7b7054b06ab630dddd134f9"
FILES = ("swagger-ui-bundle.js", "swagger-ui.css", "favicon-32x32.png", "LICENSE")  # LICENSE: Apache-2.0


def extract(tarball: bytes, target: Path) -> None:
    """tarball의 SHA256을 확인하고 FILES를 target에 쓴다. 다르면 아무것도 쓰지 않고 예외를 낸다."""
    digest = hashlib.sha256(tarball).hexdigest()
    if digest != SHA256:
        raise ValueError(f"swagger-ui-dist {VERSION} checksum mismatch: {digest}")
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        for name in FILES:
            (target / name).write_bytes(archive.extractfile(f"package/{name}").read())


def main(argv=None) -> None:
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0] if args else Path(__file__).resolve().parent.parent / "static" / "swagger-ui")
    with urllib.request.urlopen(URL, timeout=60) as response:
        extract(response.read(), target)
    print(f"swagger-ui-dist {VERSION} -> {target}")


if __name__ == "__main__":
    main()
