import os

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from api.routers import api_router
from database import init_db
from utils import config
from utils.airgap_check import enforce_airgap_runtime

# airgap은 FastAPI 기본 Swagger UI·ReDoc(CDN에서 자산을 받음)을 끄고, 이미지에 넣은 정적 파일로 Swagger UI를 띄운다(아래).
_docs = {"docs_url": None, "redoc_url": None} if config.APP_PROFILE == "airgap" else {}
app = FastAPI(title="Meeting Transcription API", version="1.0.0", max_request_size=500_000_000, **_docs)  # 500MB (2h 녹음까지 여유)

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
    enforce_airgap_runtime("api")  # airgap 외부 호출 0 조건. 어기면 기동을 멈춘다(public은 하지 않음)
    init_db()
    # 전사·요약은 워커(python -m worker)가 실행한다. 멈춘 작업 복구도 워커가 맡는다.

app.include_router(api_router)

# airgap Swagger UI: CDN 없이 SWAGGER_UI_DIR의 파일을 /docs-static으로 제공한다(2026-10-24 결정). ReDoc은 띄우지 않는다.
if config.APP_PROFILE == "airgap":
    _swagger_dir = Path(config.SWAGGER_UI_DIR)
    if (_swagger_dir / "swagger-ui-bundle.js").is_file() and (_swagger_dir / "swagger-ui.css").is_file():
        app.mount("/docs-static", StaticFiles(directory=_swagger_dir), name="docs-static")

        @app.get("/docs", include_in_schema=False)
        def swagger_ui():
            return get_swagger_ui_html(
                openapi_url=app.openapi_url,
                title=f"{app.title} - Swagger UI",
                swagger_js_url="/docs-static/swagger-ui-bundle.js",
                swagger_css_url="/docs-static/swagger-ui.css",
                swagger_favicon_url="/docs-static/favicon-32x32.png",
            )
    else:
        print(f"[airgap] Swagger UI files not found in {_swagger_dir}: /docs is off (run python -m scripts.fetch_swagger_ui)")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
