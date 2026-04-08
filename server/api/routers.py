from fastapi import APIRouter

from api.v1 import meeting_endpoints, summary_endpoints

api_router = APIRouter()
api_router.include_router(meeting_endpoints.router, tags=["meeting"])
api_router.include_router(summary_endpoints.router, tags=["summary"])