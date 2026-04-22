from fastapi import APIRouter

from api.v1 import meeting_endpoints, summary_endpoints, domain_endpoints

api_router = APIRouter()
api_router.include_router(meeting_endpoints.router, tags=["meeting"])
api_router.include_router(summary_endpoints.router, tags=["summary"])
api_router.include_router(domain_endpoints.router, tags=["domain"])