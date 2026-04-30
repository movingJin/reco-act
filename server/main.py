from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import api_router
from database import init_db

app = FastAPI(title="Meeting Transcription API", version="1.0.0", max_request_size=100_000_000)  # 100MB

# CORS 설정 (개발 환경)
cors_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

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
