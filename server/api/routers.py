from fastapi import APIRouter

from api.v1 import meeting_endpoints

api_router = APIRouter()
api_router.include_router(meeting_endpoints.router, tags=["meeting"])