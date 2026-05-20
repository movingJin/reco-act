import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import api_router
from database import init_db

app = FastAPI(title="Meeting Transcription API", version="1.0.0", max_request_size=500_000_000)  # 500MB (2h 녹음까지 여유)

# CORS 설정
# - 웹 개발: localhost:3000/5173
# - 모바일(Capacitor): iOS는 capacitor://localhost, Android는 https://localhost (Capacitor 5+)
# - 운영 웹: WEB_ORIGIN 환경변수로 도메인 주입
cors_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "capacitor://localhost",
    "https://localhost",
    "http://localhost",
]

prod_origin = os.environ.get("WEB_ORIGIN")
if prod_origin:
    cors_origins.append(prod_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 애플리케이션 시작 시 데이터베이스 테이블 초기화
@app.on_event("startup")
def startup_event():
    init_db()

app.include_router(api_router)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
