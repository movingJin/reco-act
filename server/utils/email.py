"""이메일 전송 관련 공통 유틸 (인증코드 생성, SMTP 발송)."""
import os
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from dotenv import load_dotenv

load_dotenv()

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")


def generate_verification_code() -> str:
    """6자리 숫자 인증코드를 생성합니다."""
    return ''.join([str(secrets.randbelow(10)) for _ in range(6)])


def send_verification_email(recipient_email: str, code: str) -> bool:
    """인증코드를 이메일로 전송합니다."""
    try:
        subject = "reco-act 이메일 인증코드"

        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <h2 style="color: #333; margin-bottom: 20px;">이메일 인증</h2>
                    <p style="color: #666; margin-bottom: 20px;">아래의 인증코드를 입력하여 회원가입을 완료해주세요.</p>

                    <div style="background-color: #f0f0f0; padding: 15px; border-radius: 5px; text-align: center; margin: 20px 0;">
                        <p style="font-size: 32px; font-weight: bold; color: #2563eb; margin: 0; letter-spacing: 5px;">{code}</p>
                    </div>

                    <p style="color: #999; font-size: 12px; margin-top: 20px;">
                        이 코드는 10분 동안 유효합니다.<br>
                        본인이 요청하지 않았다면 이 이메일을 무시하세요.
                    </p>
                </div>
            </body>
        </html>
        """

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = SENDER_EMAIL
        message["To"] = recipient_email

        part = MIMEText(html_body, "html")
        message.attach(part)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, recipient_email, message.as_string())

        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False
