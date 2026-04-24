"""
Naver Clova Speech-to-Text API Client
Return Zero STT를 대체하는 Clova STT 구현
"""

import json
import os
import requests
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv
from models.meeting import TranscriptSegment

load_dotenv()


class ClovaSpeechClient:
    """Naver Clova Speech Recognition API Client"""

    def __init__(
        self,
        invoke_url: Optional[str] = None,
        secret_key: Optional[str] = None,
    ) -> None:
        """
        Args:
            invoke_url: Clova Speech API URL
            secret_key: Clova API Secret Key
        """
        self.invoke_url = invoke_url or os.getenv("CLOVA_INVOKE_URL")
        self.secret_key = secret_key or os.getenv("CLOVA_SECRET_KEY")

        if not self.invoke_url or not self.secret_key:
            raise ValueError(
                "Missing credentials. Set CLOVA_INVOKE_URL and CLOVA_SECRET_KEY "
                "environment variables, or pass them to ClovaSpeechClient."
            )

    def convert_file_to_transcript(
        self,
        file_path: str,
        language: str = "ko-KR",
        domain_keywords: Optional[List[str]] = None,
    ) -> List[TranscriptSegment]:
        """
        음성 파일을 텍스트로 변환

        Args:
            file_path: WAV 파일 경로
            language: 언어 코드 (기본값: ko-KR)
            domain_keywords: 도메인 키워드 리스트 (boostings로 사용)

        Returns:
            TranscriptSegment 리스트
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        # Clova API 파라미터 구성
        params = {
            "language": language,
            "completion": "sync",
            "callback": "",
            "fullText": True,
        }

        # 도메인 키워드가 있으면 boostings 추가 (도메인별 키워드 인식율 향상)
        if domain_keywords:
            params["boostings"] = [
                {"words": ", ".join(domain_keywords), "weight": 1.0}
            ]

        # 요청 헤더 설정
        headers = {"X-CLOVASPEECH-API-KEY": self.secret_key}

        # 멀티파트 요청 생성
        try:
            with open(file_path, "rb") as audio_file:
                files = {
                    "media": (os.path.basename(file_path), audio_file),
                    "params": (None, json.dumps(params)),
                }

                response = requests.post(
                    f"{self.invoke_url}/recognizer/upload",
                    headers=headers,
                    files=files,
                    timeout=300,  # 5분 타임아웃
                )

            response.raise_for_status()
            result = response.json()

            # 응답 검증
            if result.get("result") != "COMPLETED":
                raise RuntimeError(
                    f"Clova API Error: {result.get('message', 'Unknown error')}"
                )

            # 응답을 TranscriptSegment로 변환
            segments = self._parse_clova_response(result)
            return segments

        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Clova API Request Error: {str(e)}")

    def _parse_clova_response(self, response: Dict[str, Any]) -> List[TranscriptSegment]:
        """
        Clova API 응답을 TranscriptSegment로 변환

        응답 구조에서:
        - segments.speaker.label: speaker_index
        - segments.textEdited: text
        - segments.start: start (밀리초)
        - segments.end: end (밀리초)

        Args:
            response: Clova API 응답

        Returns:
            TranscriptSegment 리스트
        """
        segments_data = response.get("segments", [])
        transcript_segments = []

        for segment in segments_data:
            try:
                # speaker.label을 speaker_index로 사용 (문자열 "1", "2" -> 정수로 변환)
                speaker_label = segment.get("speaker").get("label")
                speaker_index = int(speaker_label) - 1 # Clova speach는 index 번호가 1부터 시작

                # textEdited가 우선, 없으면 text 사용
                text = segment.get("textEdited")
                start = segment.get("start")
                end = segment.get("end")

                transcript_segment = TranscriptSegment(
                    speaker_index=speaker_index,
                    text=text,
                    start=start,
                    end=end,
                )
                transcript_segments.append(transcript_segment)

            except (ValueError, KeyError, TypeError) as e:
                print(f"Warning: Failed to parse segment: {e}")
                continue

        return transcript_segments


def convert(
    file_path: str,
    language: str = "ko-KR",
    domain_keywords: Optional[List[str]] = None,
) -> List[TranscriptSegment]:
    """
    편의 함수: 파일을 바로 변환

    Args:
        file_path: 음성 파일 경로
        language: 언어 코드
        domain_keywords: 도메인 키워드 리스트

    Returns:
        TranscriptSegment 리스트
    """
    client = ClovaSpeechClient()
    return client.convert_file_to_transcript(
        file_path, language, domain_keywords
    )
