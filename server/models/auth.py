"""인증/사용자 관련 요청/응답 DTO 모델."""
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=8)
    password_confirm: str = Field(..., min_length=8)
    code: str = Field(..., min_length=6, max_length=6)

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "name": "홍길동",
                "password": "password123",
                "password_confirm": "password123",
                "code": "123456",
            }
        }


class VerificationRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "code": "123456",
            }
        }


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "password123",
            }
        }


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict

    class Config:
        json_schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "user": {
                    "email": "user@example.com",
                    "name": "홍길동",
                },
            }
        }


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class RefreshTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


class UserResponse(BaseModel):
    email: str
    name: str
    created_at: str
    domain_id: Optional[int] = None

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "name": "홍길동",
                "created_at": "2024-01-01T12:00:00",
                "domain_id": 1,
            }
        }


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    domain_id: Optional[int] = None

    class Config:
        json_schema_extra = {
            "example": {
                "name": "김길동",
                "domain_id": 1,
            }
        }


class EmailRequest(BaseModel):
    """이메일만 단일 필드로 받는 요청 (중복확인, 인증코드 발송, 비밀번호 재설정 요청)."""
    email: EmailStr

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
            }
        }


class PasswordResetConfirmRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8)
    password_confirm: str = Field(..., min_length=8)

    class Config:
        json_schema_extra = {
            "example": {
                "email": "user@example.com",
                "code": "123456",
                "new_password": "newpassword123",
                "password_confirm": "newpassword123",
            }
        }


class DeleteAccountRequest(BaseModel):
    password: str

    class Config:
        json_schema_extra = {
            "example": {
                "password": "password123",
            }
        }
