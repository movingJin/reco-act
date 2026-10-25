# reco-act 폐쇄망 설치

이 번들로 인터넷이 없는 서버에 reco-act(회의 녹취·AI 요약)를 설치한다.

- 서버: Linux x86_64, NVIDIA GPU(RTX 5090급), NVIDIA 드라이버와 NVIDIA Container Toolkit
- 소프트웨어: Docker Engine, Docker Compose v2(`docker compose`), bash, coreutils(`sha256sum`), tar
- 번들 크기는 수십 GB다. 설치할 디스크(기본 `/opt/reco-act`)에 번들의 두 배 이상 여유를 둔다.

## 번들 구성

| 경로 | 내용 |
|---|---|
| `manifest.json` | 항목별 종류, 이름, 버전, 출처, SHA256, 라이선스 |
| `SHA256SUMS` | 모든 파일의 SHA256(`sha256sum -c` 형식) |
| `images/` | 컨테이너 이미지(`docker save`, gzip) |
| `models/` | 모델(Whisper large-v3, pyannote community-1, Qwen3-32B AWQ) |
| `source/` | 앱 소스(compose 파일, 설정 견본) |
| `wheels/`, `npm/` | 다시 빌드할 때만 쓰는 Python·npm 패키지 |
| `NOTICE` | 모델 저작자 표시 |

`manifest.json`의 `skipped`가 비어 있지 않으면 리허설용 번들이다. 설치하지 않는다.

## 1. 무결성 확인

번들을 만든 사람에게서 **`SHA256SUMS`의 sha256 값을 번들과 따로**(문서, 메일 등) 받는다.

```bash
cd <번들 디렉터리>
./install.sh --verify-only --sums-sha256 <받은 값>
```

모든 파일을 읽으므로 몇 분 걸린다. `bundle OK`가 나오지 않으면 설치하지 않는다.
SHA256SUMS에 없는 파일이 있으면 경고만 하고 쓰지 않는다.

## 2. 설치

```bash
sudo ./install.sh --sums-sha256 <받은 값>            # 설치 위치를 바꾸려면 --home <디렉터리>
```

1번 확인을 다시 하고 아래를 한다. 기존 설정 파일과 데이터(`records/`, `pgdata/`, `redis/`)는 건드리지 않는다.

- `docker load`로 이미지를 올린다.
- 소스를 `/opt/reco-act/app/reco-act-<버전>/`에 푼다.
- 모델을 `/opt/reco-act/models/`에 복사하고 SHA256을 대조한다. 이미 같은 파일이 있으면 건너뛴다.
- 데이터 디렉터리를 만든다. 설정 파일 견본(`compose.env`, `airgap.env`, `db.env`)은 없을 때만 만든다(권한 600).

## 3. 설정 (처음 한 번)

| 파일 | 채울 것 |
|---|---|
| `/opt/reco-act/db.env` | `POSTGRES_PASSWORD` |
| `/opt/reco-act/airgap.env` | `DATABASE_URL`의 비밀번호(db.env와 같게), `SECRET_KEY`(`openssl rand -hex 32`), 사내 메일 서버(`SMTP_HOST`, `SMTP_SECURITY`, `SMTP_PORT`, `SENDER_EMAIL`, `SENDER_PASSWORD`), 메일 서버가 FQDN이면 `AIRGAP_INTERNAL_DOMAINS` |
| `/opt/reco-act/compose.env` | 포트(`WEB_HTTPS_PORT`, `WEB_HTTP_PORT`), vLLM 메모리·컨텍스트 값. `RECO_ACT_VERSION`은 설치한 번들 버전 |
| `/opt/reco-act/tls/server.crt`, `server.key` | 내부 CA가 발급한 서버 인증서와 키. 브라우저 녹음에 HTTPS가 필요하다 |

## 4. 기동과 확인

```bash
cd /opt/reco-act/app/reco-act-<버전>
C="docker compose -f docker-compose.airgap.yaml --env-file /opt/reco-act/compose.env"
$C up -d --no-build
$C ps                                     # llm은 모델을 올리느라 수 분 걸린다
$C logs api worker | grep '\[airgap\]'    # startup checks passed (api, worker)
```

브라우저로 `https://<서버 주소>`에 접속해 가입한다(사내 메일로 인증코드가 온다).
메일 서버 없이 첫 계정을 만들어야 하면 아래를 쓴다(비밀번호는 화면에 입력).

```bash
$C exec api python -c "import getpass; \
from database import SessionLocal, User; from utils.auth import hash_password; e = input('email: '); p = getpass.getpass('password: '); \
s = SessionLocal(); s.add(User(email=e, name=e.split('@')[0], password_hash=hash_password(p), is_active=True)); s.commit(); print('created', e)"
```

## 5. 업그레이드

1. 새 번들로 1·2번을 한다. 이미지에는 버전 태그가 붙어 있어 이전 버전과 함께 올라간다.
2. `/opt/reco-act/compose.env`의 `RECO_ACT_VERSION`을 새 버전으로 바꾼다.
3. 새 소스 디렉터리(`app/reco-act-<새 버전>`)에서 4번의 `up -d --no-build`를 한다. 데이터와 설정은 그대로 이어진다.

## 다시 빌드할 때만

이미지에 패키지가 이미 들어 있으므로 평소에는 필요 없다.

- Python: `pip install --no-index --find-links wheels --require-hashes -r wheels/requirements-worker.lock.txt` (api는 `requirements-api.lock.txt`)
- 웹: `tar -xzf npm/npm-cache.tar.gz -C /tmp`, 소스의 `client/`에서 `npm ci --offline --cache /tmp/npm-cache && npm run build:airgap`
- 베이스 이미지(python, nvidia/cuda, node, nginx)는 번들을 `--base-images`로 만든 경우에만 `images/`에 있다.
