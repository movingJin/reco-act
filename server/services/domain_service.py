"""
도메인 키워드 관리 서비스 레이어
"""
from typing import List, Optional
from database import SessionLocal, DomainKeywords as DBDomainKeywords


def get_db():
    """Get database session."""
    return SessionLocal()


def _sort_keywords(keywords: List[str]) -> List[str]:
    """
    키워드 리스트를 오름차순으로 정렬
    
    Args:
        keywords: 키워드 리스트
        
    Returns:
        정렬된 키워드 리스트
    """
    return sorted(keywords)


def list_all_domains() -> List[dict]:
    """
    모든 도메인 키워드 목록 조회
    
    Returns:
        도메인 정보 딕셔너리 리스트 (id, domain_name, keywords 포함)
    """
    db = get_db()
    try:
        domains = db.query(DBDomainKeywords).all()
        return [
            {
                "id": domain.id,
                "domain_name": domain.domain_name,
                "keywords": list(domain.keywords)
            }
            for domain in domains
        ]
    except Exception as e:
        print(f"Error listing domains: {e}")
        return []
    finally:
        db.close()


def get_domain(domain_name: str) -> Optional[dict]:
    """
    도메인 이름으로 도메인 조회
    
    Args:
        domain_name: 도메인 이름
        
    Returns:
        도메인 정보 딕셔너리 (id, domain_name, keywords 포함), 없으면 None
    """
    db = get_db()
    try:
        domain = db.query(DBDomainKeywords).filter(
            DBDomainKeywords.domain_name == domain_name
        ).first()
        
        if not domain:
            return None
        
        return {
            "id": domain.id,
            "domain_name": domain.domain_name,
            "keywords": list(domain.keywords)
        }
    except Exception as e:
        print(f"Error fetching domain {domain_name}: {e}")
        return None
    finally:
        db.close()


def get_domain_by_id(domain_id: int) -> Optional[dict]:
    """
    도메인 ID로 도메인 조회
    
    Args:
        domain_id: 도메인 ID (primary key)
        
    Returns:
        도메인 정보 딕셔너리 (id, domain_name, keywords 포함), 없으면 None
    """
    db = get_db()
    try:
        domain = db.query(DBDomainKeywords).filter(
            DBDomainKeywords.id == domain_id
        ).first()
        
        if not domain:
            return None
        
        return {
            "id": domain.id,
            "domain_name": domain.domain_name,
            "keywords": list(domain.keywords)
        }
    except Exception as e:
        print(f"Error fetching domain by id {domain_id}: {e}")
        return None
    finally:
        db.close()


def create_domain(domain_name: str, keywords: List[str]) -> Optional[dict]:
    """
    새 도메인 키워드 추가
    
    Args:
        domain_name: 도메인 이름
        keywords: 키워드 리스트
        
    Returns:
        생성된 도메인 정보 딕셔너리, 실패 시 None
    """
    db = get_db()
    try:
        # Check if domain already exists
        existing = db.query(DBDomainKeywords).filter(
            DBDomainKeywords.domain_name == domain_name
        ).first()
        
        if existing:
            return None  # Already exists
        
        # Sort keywords before saving
        sorted_keywords = _sort_keywords(keywords)
        
        # Create new domain
        domain = DBDomainKeywords(
            domain_name=domain_name,
            keywords=sorted_keywords
        )
        db.add(domain)
        db.commit()
        
        return {
            "id": domain.id,
            "domain_name": domain.domain_name,
            "keywords": list(domain.keywords)
        }
    except Exception as e:
        print(f"Error creating domain: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def update_domain(domain_name: str, new_domain_name: str, keywords: List[str]) -> Optional[dict]:
    """
    도메인 키워드 업데이트
    
    Args:
        domain_name: 현재 도메인 이름
        new_domain_name: 변경할 도메인 이름 (같을 수 있음)
        keywords: 새 키워드 리스트
        
    Returns:
        업데이트된 도메인 정보 딕셔너리, 실패 시 None
    """
    db = get_db()
    try:
        domain = db.query(DBDomainKeywords).filter(
            DBDomainKeywords.domain_name == domain_name
        ).first()
        
        if not domain:
            return None
        
        # Check if new domain_name already exists (if different)
        if new_domain_name != domain_name:
            existing = db.query(DBDomainKeywords).filter(
                DBDomainKeywords.domain_name == new_domain_name
            ).first()
            if existing:
                return None  # Already exists
            domain.domain_name = new_domain_name
        
        # Sort keywords before saving
        sorted_keywords = _sort_keywords(keywords)
        domain.keywords = sorted_keywords
        
        db.commit()
        
        return {
            "id": domain.id,
            "domain_name": domain.domain_name,
            "keywords": list(domain.keywords)
        }
    except Exception as e:
        print(f"Error updating domain: {e}")
        db.rollback()
        return None
    finally:
        db.close()


def delete_domain(domain_name: str) -> bool:
    """
    도메인 키워드 삭제
    
    Args:
        domain_name: 도메인 이름
        
    Returns:
        성공 여부
    """
    db = get_db()
    try:
        domain = db.query(DBDomainKeywords).filter(
            DBDomainKeywords.domain_name == domain_name
        ).first()
        
        if not domain:
            return False
        
        db.delete(domain)
        db.commit()
        return True
    except Exception as e:
        print(f"Error deleting domain: {e}")
        db.rollback()
        return False
    finally:
        db.close()
