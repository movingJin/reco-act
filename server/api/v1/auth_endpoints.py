"""인증/사용자 관리 API 엔드포인트."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import User, get_db
from models.auth import (
    DeleteAccountRequest,
    EmailRequest,
    LoginRequest,
    LoginResponse,
    PasswordResetConfirmRequest,
    SignupRequest,
    UpdateProfileRequest,
    UserResponse,
    VerificationRequest,
)
from services import auth_service
from utils.auth import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/check-email")
async def check_email(request: EmailRequest, db: Session = Depends(get_db)):
    """이메일 중복 여부를 확인합니다."""
    auth_service.check_email_available(request.email, db)
    return {"message": "사용 가능한 이메일입니다"}


@router.post("/send-verification-code")
async def send_verification_code(request: EmailRequest):
    """가입 전 인증코드를 전송합니다."""
    auth_service.send_signup_verification_code(request.email)
    return {"message": "인증코드가 이메일로 전송되었습니다"}


@router.post("/verify-code")
async def verify_code(request: VerificationRequest):
    """이메일과 인증코드의 유효성을 확인합니다."""
    auth_service.verify_code(request.email, request.code)
    return {"message": "인증코드가 확인되었습니다"}


@router.post("/signup")
async def signup(request: SignupRequest, db: Session = Depends(get_db)):
    """회원가입을 완료합니다."""
    user = auth_service.signup(
        email=request.email,
        name=request.name,
        password=request.password,
        code=request.code,
        db=db,
    )
    return {
        "message": "회원가입이 완료되었습니다",
        "user": {
            "email": user.email,
            "name": user.name,
        },
    }


@router.post("/login")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """로그인합니다."""
    user, access_token = auth_service.login(request.email, request.password, db)
    return LoginResponse(
        access_token=access_token,
        user={
            "email": user.email,
            "name": user.name,
            "domain_id": user.domain_id,
        },
    )


@router.post("/logout")
async def logout():
    """로그아웃합니다."""
    return {"message": "로그아웃되었습니다"}


@router.get("/me")
async def get_profile(user: User = Depends(get_current_user)):
    """현재 사용자 정보를 조회합니다."""
    return UserResponse(
        email=user.email,
        name=user.name,
        created_at=user.created_at.isoformat(),
        domain_id=user.domain_id,
    )


@router.put("/profile")
async def update_profile(
    request: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """사용자 정보를 업데이트합니다."""
    update_domain = "domain_id" in request.model_fields_set
    user = auth_service.update_user_profile(
        user,
        db,
        name=request.name,
        domain_id=request.domain_id,
        update_domain=update_domain,
    )
    return {
        "message": "사용자 정보가 업데이트되었습니다",
        "user": UserResponse(
            email=user.email,
            name=user.name,
            created_at=user.created_at.isoformat(),
            domain_id=user.domain_id,
        ),
    }


@router.post("/password-reset")
async def request_password_reset(request: EmailRequest, db: Session = Depends(get_db)):
    """비밀번호 재설정을 요청합니다."""
    auth_service.request_password_reset(request.email, db)
    return {"message": "비밀번호 재설정 인증코드가 이메일로 전송되었습니다"}


@router.post("/password-reset-confirm")
async def confirm_password_reset(
    request: PasswordResetConfirmRequest,
    db: Session = Depends(get_db),
):
    """비밀번호를 재설정합니다."""
    auth_service.confirm_password_reset(
        email=request.email,
        code=request.code,
        new_password=request.new_password,
        password_confirm=request.password_confirm,
        db=db,
    )
    return {"message": "비밀번호가 변경되었습니다"}


@router.delete("/account")
async def delete_account(
    request: DeleteAccountRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """사용자 계정과 관련된 모든 데이터를 영구 삭제합니다."""
    auth_service.delete_user_account(user, request.password, db)
    return {"message": "계정이 삭제되었습니다"}
