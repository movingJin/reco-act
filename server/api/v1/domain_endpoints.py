"""
도메인 키워드 관리 엔드포인트
"""
from fastapi import APIRouter, HTTPException
from typing import List
from pydantic import BaseModel
from services.domain_service import (
    list_all_domains,
    get_domain,
    create_domain,
    update_domain,
    delete_domain,
)


router = APIRouter()


class DomainKeywordRequest(BaseModel):
    domain_name: str
    keywords: List[str]


class DomainKeywordResponse(BaseModel):
    id: int
    domain_name: str
    keywords: List[str]


@router.get("/api/domains", response_model=List[DomainKeywordResponse])
async def list_domains():
    """
    모든 도메인 키워드 목록 조회
    
    Returns:
        도메인 키워드 리스트
    """
    try:
        domains = list_all_domains()
        return [
            DomainKeywordResponse(
                id=domain["id"],
                domain_name=domain["domain_name"],
                keywords=domain["keywords"]
            )
            for domain in domains
        ]
    except Exception as e:
        print(f"Error listing domains: {e}")
        raise HTTPException(status_code=500, detail="Failed to list domains")


@router.get("/api/domains/{domain_name}", response_model=DomainKeywordResponse)
async def get_domain_endpoint(domain_name: str):
    """
    특정 도메인의 키워드 조회
    
    Args:
        domain_name: 도메인 이름
        
    Returns:
        도메인 키워드 정보
    """
    try:
        domain = get_domain(domain_name)
        
        if not domain:
            raise HTTPException(status_code=404, detail="Domain not found")
        
        return DomainKeywordResponse(
            id=domain["id"],
            domain_name=domain["domain_name"],
            keywords=domain["keywords"]
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching domain {domain_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch domain")


@router.post("/api/domains", response_model=DomainKeywordResponse)
async def create_domain_endpoint(request: DomainKeywordRequest):
    """
    새 도메인 키워드 추가
    
    Args:
        request: 도메인 이름과 키워드 리스트
        
    Returns:
        생성된 도메인 키워드 정보
    """
    try:
        domain = create_domain(request.domain_name, request.keywords)
        
        if not domain:
            raise HTTPException(
                status_code=409, 
                detail=f"Domain '{request.domain_name}' already exists"
            )
        
        return DomainKeywordResponse(
            id=domain["id"],
            domain_name=domain["domain_name"],
            keywords=domain["keywords"]
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error creating domain: {e}")
        raise HTTPException(status_code=500, detail="Failed to create domain")


@router.put("/api/domains/{domain_name}", response_model=DomainKeywordResponse)
async def update_domain_endpoint(domain_name: str, request: DomainKeywordRequest):
    """
    도메인 키워드 업데이트
    
    Args:
        domain_name: 도메인 이름 (URL 경로)
        request: 업데이트할 도메인 정보
        
    Returns:
        업데이트된 도메인 키워드 정보
    """
    try:
        domain = update_domain(domain_name, request.domain_name, request.keywords)
        
        if not domain:
            if get_domain(domain_name) is None:
                raise HTTPException(status_code=404, detail="Domain not found")
            else:
                raise HTTPException(
                    status_code=409,
                    detail=f"Domain '{request.domain_name}' already exists"
                )
        
        return DomainKeywordResponse(
            id=domain["id"],
            domain_name=domain["domain_name"],
            keywords=domain["keywords"]
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating domain: {e}")
        raise HTTPException(status_code=500, detail="Failed to update domain")


@router.delete("/api/domains/{domain_name}")
async def delete_domain_endpoint(domain_name: str):
    """
    도메인 키워드 삭제
    
    Args:
        domain_name: 도메인 이름
        
    Returns:
        삭제 결과
    """
    try:
        success = delete_domain(domain_name)
        
        if not success:
            raise HTTPException(status_code=404, detail="Domain not found")
        
        return {"status": "ok", "message": f"Domain '{domain_name}' deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting domain: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete domain")
