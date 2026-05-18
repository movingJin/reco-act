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
