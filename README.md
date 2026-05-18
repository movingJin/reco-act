# Reco-Act

회의 녹음을 STT(Speech-to-Text)로 전사하고, LLM 기반 요약 및 도메인 관리 기능을 제공하는 웹 애플리케이션입니다.

## 개요

Reco-Act는 회의 음성을 자동으로 텍스트로 변환하고, 화자 분리(Diarization), 도메인별 요약, 회의록 관리 등을 지원하는 회의 기록 자동화 플랫폼입니다.
- **백엔드**: FastAPI 기반 REST API 서버 (Python)
- **프론트엔드**: React + TypeScript + Vite SPA
- **STT**: Naver CLOVA Speech 연동
- **요약 워크플로우**: LangGraph + OpenAI 기반 요약 파이프라인
- **데이터베이스**: PostgreSQL (+ pgvector)

## 주요 기능

- **회의 녹취 및 STT 전사**: 브라우저에서 직접 녹음하거나 오디오 파일을 업로드하여 텍스트로 전사
- **화자 분리**: STT 결과를 화자(speaker)별로 자동 구분
- **회의록 편집**: 전사된 텍스트와 화자명 수정 가능
- **AI 회의 요약**: LangGraph 워크플로우를 활용한 회의 내용 자동 요약
- **도메인 관리**: 회의를 도메인별로 분류하고 도메인별 맞춤 요약 프롬프트 적용
- **사용자 인증**: 회원가입 / 로그인 / 비밀번호 찾기 (이메일 인증 + JWT)

## 프로젝트 구조

```
reco-act/
├── docker-compose.yaml          # 운영 배포용 Docker Compose 설정
│
├── server/                      # FastAPI 백엔드
│   ├── main.py                  # FastAPI 엔트리포인트, CORS / 라우터 등록
│   ├── database.py              # SQLAlchemy 엔진/세션, init_db
│   ├── clova_stt.py             # Naver CLOVA Speech STT 연동
│   ├── requirements.txt         # Python 의존성
│   ├── Dockerfile
│   ├── prompt.txt               # 요약 프롬프트 템플릿
│   │
│   ├── api/
│   │   ├── routers.py           # 최상위 APIRouter (auth/meeting/summary/domain)
│   │   └── v1/
│   │       ├── auth_endpoints.py     # 인증 관련 엔드포인트
│   │       ├── meeting_endpoints.py  # 회의 CRUD, 녹취 업로드/STT
│   │       ├── summary_endpoints.py  # 회의 요약 생성/조회
│   │       └── domain_endpoints.py   # 도메인 CRUD
│   │
│   ├── services/
│   │   ├── auth_service.py      # 사용자/인증 비즈니스 로직
│   │   ├── meeting_service.py   # 회의/전사 비즈니스 로직
│   │   ├── summary_service.py   # 요약 비즈니스 로직
│   │   └── domain_service.py    # 도메인 비즈니스 로직
│   │
│   ├── models/
│   │   ├── auth.py              # User 등 인증 관련 모델
│   │   ├── meeting.py           # Meeting / Transcript 모델
│   │   └── summary.py           # Summary 모델
│   │
│   ├── graph/
│   │   └── summary_workflow.py  # LangGraph 기반 요약 워크플로우
│   │
│   ├── utils/
│   │   ├── auth.py              # JWT / 비밀번호 해싱 유틸
│   │   ├── config.py            # 환경설정 로더
│   │   ├── email.py             # 이메일 발송 유틸
│   │   └── verification_store.py # 인증코드 저장소(Redis)
│   │
│   └── records/                 # 업로드된 오디오 파일 저장소
│
└── client/                      # React + TypeScript 프론트엔드
    ├── package.json
    ├── vite.config.ts
    ├── nginx.conf               # 운영용 Nginx 설정
    ├── Dockerfile
    │
    └── src/
        ├── main.tsx             # React 엔트리포인트
        ├── App.tsx              # 라우팅 / 메인 레이아웃
        │
        ├── api/
        │   └── authApi.ts       # axios 인스턴스, 인증 API 호출
        │
        ├── pages/
        │   ├── Login.tsx
        │   ├── Signup.tsx
        │   ├── ForgotPassword.tsx
        │   └── Profile.tsx
        │
        ├── components/
        │   ├── MeetingList.tsx       # 좌측 회의 목록
        │   ├── MeetingDetail.tsx     # 우측 회의 상세
        │   ├── MeetingSettings.tsx   # 회의 설정
        │   ├── DomainSettings.tsx    # 도메인 설정 모달
        │   ├── RecorderControls.tsx  # 녹취 컨트롤
        │   ├── TranscriptEditor.tsx  # 전사 텍스트 편집기
        │   ├── SummaryPanel.tsx      # 요약 패널
        │   └── ProtectedRoute.tsx    # 인증 보호 라우트
        │
        ├── contexts/
        │   └── AuthContext.tsx  # 전역 인증 상태
        │
        ├── types/
        │   └── auth.ts          # 인증 관련 타입 정의
        │
        └── styles/              # 컴포넌트별 CSS
```

## 설치 및 실행 (로컬)

### Server

```bash
cd server

# 가상환경 생성 (선택사항이지만 권장)
python3 -m venv .venv
source .venv/bin/activate

# 의존성 설치
pip install -r requirements.txt

# 서버 실행
python -m main
# 또는
uvicorn main:app --reload
```

서버는 `http://localhost:8000`에서 실행됩니다. (API 문서: `http://localhost:8000/docs`)

> 실행 전 `server/.env` 파일에 데이터베이스 접속 정보, CLOVA STT / OpenAI API Key, JWT Secret, 이메일 발송 설정 등이 구성되어 있어야 합니다.

### Client

```bash
cd client

# Node.js 버전 확인 (v22.12.0 권장)
node --version

# 의존성 설치
npm install

# 개발 서버 실행
npm run dev
```

프론트엔드는 `http://localhost:3000`에서 실행됩니다.

## 운영 배포

Docker Compose를 통해 client(Nginx) + server(FastAPI) 컨테이너를 함께 기동합니다.

```bash
docker-compose build --no-cache
docker-compose up -d
```

- 클라이언트: `http://<host>:23001`
- 서버: `http://<host>:8000`

서버 컨테이너는 `./server/records` 디렉토리를 볼륨으로 마운트하여 업로드된 녹취 파일을 영속화하며, `ENVIRONMENT=prod` 환경에서 `.env`의 설정을 사용합니다.

## Android 앱 빌드 및 배포

클라이언트는 [Capacitor](https://capacitorjs.com/) 7.x로 래핑되어 동일 React 코드베이스에서 Android 네이티브 앱을 빌드합니다. 네이티브 프로젝트는 [client/android/](client/android/)에 위치하며, Capacitor 설정은 [client/capacitor.config.ts](client/capacitor.config.ts)에 정의되어 있습니다.

### 사전 준비

- **Android Studio** (Hedgehog 이상 권장) 및 **Android SDK** (API 34+)
- **JDK 17** (Capacitor 7 요구사항)
- `client/android/local.properties`에 `sdk.dir=<Android SDK 경로>` 설정 (Android Studio가 자동 생성)
- 릴리스 빌드용 keystore: [client/android/recoact-release.jks](client/android/recoact-release.jks) (저장소에 포함)

### 1. 웹 자산 빌드 및 Capacitor 동기화

네이티브 프로젝트를 빌드하기 전에 React 앱을 빌드하고 그 결과(`client/dist`)를 Android 프로젝트로 복사해야 합니다.

```bash
cd client

# 모바일 모드로 빌드 후 cap sync 일괄 수행
npm run build:mobile

# 또는 Android Studio까지 자동 오픈
npm run android
```

> `npm run build:mobile`은 내부적으로 `vite build --mode mobile && cap sync`를 실행합니다. `mobile` 모드에서는 [client/.env.mobile](client/.env.mobile) (있다면)의 API 엔드포인트 등이 적용됩니다.

`cap sync`가 완료되면 `client/android/app/src/main/assets/public/` 아래에 빌드된 웹 자산이 복사되고, Capacitor 플러그인 변경사항도 함께 반영됩니다.

### 2. USB 디버깅 모드로 실기기에 설치

핸드폰을 개발용 PC에 USB로 연결하고 디버그 빌드를 곧바로 설치하는 흐름입니다.

#### 2.1 핸드폰 설정
1. **설정 → 휴대전화 정보 → 빌드 번호**를 7회 탭하여 개발자 옵션 활성화
2. **개발자 옵션 → USB 디버깅** 켜기
3. USB 케이블로 PC와 연결 후, 핸드폰 화면의 "USB 디버깅 허용" 팝업에서 허용

#### 2.2 연결 확인

```bash
# Android SDK platform-tools가 PATH에 있어야 함
adb devices
```

`device` 상태로 표시되면 정상 연결입니다. (`unauthorized`이면 핸드폰에서 허용 팝업 확인)

#### 2.3 디버그 APK 빌드 & 설치

방법 A — Android Studio 사용 (권장):
```bash
cd client
npm run android         # Android Studio 자동 오픈
```
Android Studio에서 상단의 디바이스 목록에서 연결된 핸드폰을 선택한 뒤 ▶ (Run) 버튼 클릭.

방법 B — CLI:
```bash
cd client/android
./gradlew installDebug   # 빌드 + 연결된 기기에 자동 설치
```

설치 후 핸드폰 앱 서랍에서 **Reco-Act**를 실행할 수 있습니다.

#### 2.4 로그 확인

```bash
adb logcat | grep -iE "(capacitor|recoact|chromium)"
```

또는 PC의 Chrome에서 `chrome://inspect`로 접속하면 앱의 WebView를 원격 DevTools로 디버깅할 수 있습니다.

### 3. 릴리스 APK / AAB 빌드 (서명)

스토어 배포나 Firebase App Distribution 배포에는 서명된 릴리스 빌드가 필요합니다. Keystore 정보는 [client/android/app/build.gradle](client/android/app/build.gradle)에서 `keystore.properties` 파일 또는 환경변수로부터 읽어옵니다.

#### 3.1 keystore.properties 작성 (로컬 빌드 시)

`client/android/keystore.properties` 파일을 생성합니다. (이 파일은 `.gitignore` 처리 필요)

```properties
storeFile=recoact-release.jks
storePassword=<keystore 비밀번호>
keyAlias=<key alias>
keyPassword=<key 비밀번호>
```

> CI 환경에서는 파일 대신 환경변수 `RECOACT_KEYSTORE_FILE`, `RECOACT_KEYSTORE_PASSWORD`, `RECOACT_KEY_ALIAS`, `RECOACT_KEY_PASSWORD`로 전달할 수 있습니다.

#### 3.2 빌드 실행

```bash
cd client
npm run build:mobile          # 1) 웹 자산 빌드 + cap sync

cd android
./gradlew assembleRelease     # 2-a) 서명된 APK 생성
# 또는
./gradlew bundleRelease       # 2-b) Play Store용 AAB 생성
```

산출물 위치:
- APK: `client/android/app/build/outputs/apk/release/app-release.apk`
- AAB: `client/android/app/build/outputs/bundle/release/app-release.aab`

#### 3.3 versionCode / versionName 갱신

릴리스마다 [client/android/app/build.gradle](client/android/app/build.gradle)의 `versionCode`(정수, 단조 증가)와 `versionName`(사용자 노출 버전 문자열)을 올려야 스토어/Firebase 업로드가 가능합니다.

### 4. Firebase App Distribution으로 배포

내부 테스터에게 배포할 때는 Firebase App Distribution이 가장 간단합니다. 스토어 심사 없이 APK/AAB를 바로 배포할 수 있습니다.

#### 4.1 Firebase CLI 설치 및 로그인

```bash
npm install -g firebase-tools
firebase login                # 브라우저에서 Google 계정 인증
```

CI 환경에서는 서비스 계정 JSON을 사용해 비대화형으로 인증합니다.
```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/firebase-service-account.json
```

#### 4.2 App ID 확인

Firebase Console → 프로젝트 설정 → 일반 → "앱 ID"에서 다음과 같은 형식의 ID를 복사합니다.
```
1:1234567890:android:abcdef1234567890
```

#### 4.3 APK/AAB 업로드

```bash
cd client

# 1) 릴리스 빌드 (3장 참고)
npm run build:mobile
cd android && ./gradlew clean assembleRelease && cd ..

# 2) Firebase App Distribution 업로드
firebase appdistribution:distribute \
  android/app/build/outputs/apk/release/app-release.apk \
  --app 1:1234567890:android:abcdef1234567890 \
  --release-notes "이번 빌드의 변경사항 요약" \
  --groups "internal-testers"
```

옵션:
- `--release-notes-file <path>`: 릴리스 노트를 파일로 전달
- `--testers "a@x.com,b@x.com"`: 그룹 대신 개별 테스터 지정
- AAB도 동일하게 업로드 가능 (`app-release.aab`)

업로드가 성공하면 테스터에게 자동으로 초대 이메일이 발송되고, **App Tester** 앱 또는 메일의 링크를 통해 설치할 수 있습니다.

### 5. 트러블슈팅

| 증상 | 원인 / 조치 |
| --- | --- |
| `adb devices`에 핸드폰이 안 보임 | USB 케이블이 데이터 전송용인지 확인, 핸드폰의 USB 디버깅 허용 팝업 확인, `adb kill-server && adb start-server` |
| 앱에서 API 호출이 `localhost` 로 가서 실패 | `vite build --mode mobile`로 빌드했는지 확인. 모바일 빌드는 PC의 `localhost`가 아닌 운영 서버 URL을 가리켜야 함 |
| `cap sync` 후에도 변경사항이 앱에 반영 안 됨 | `npm run build:mobile`을 다시 실행했는지 확인. 단순 `cap sync`만으로는 `dist`가 갱신되지 않음 |
| `./gradlew assembleRelease` 시 keystore 관련 오류 | `keystore.properties` 또는 환경변수가 올바르게 설정되어 있는지, `storeFile` 경로가 `client/android/` 기준의 상대 경로인지 확인 |
| Firebase 업로드 시 `App not found` | `--app` 옵션의 App ID가 Console에 표시된 값과 정확히 일치하는지, 해당 Firebase 프로젝트에 App Distribution이 활성화되어 있는지 확인 |
| `versionCode` 중복 오류 | [client/android/app/build.gradle](client/android/app/build.gradle)의 `versionCode`를 직전 배포보다 큰 값으로 증가시킨 뒤 재빌드 |
