"""
Naver Clova Speech-to-Text API Client
Return Zero STT를 대체하는 Clova STT 구현
"""

import json
import os
import requests
from typing import Dict, List, Any, Optional, Tuple
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
    ) -> Tuple[List[TranscriptSegment], List[str]]:
        """
        음성 파일을 텍스트로 변환

        Args:
            file_path: WAV 파일 경로
            language: 언어 코드 (기본값: ko-KR)
            domain_keywords: 도메인 키워드 리스트 (boostings로 사용)

        Returns:
            (TranscriptSegment 리스트, 화자 이름 리스트). 화자 이름은 Clova가
            반환한 speakers[].label(1부터 시작) 오름차순으로 정렬되어 있으며,
            speaker_index(0-based)로 그대로 인덱싱할 수 있다.
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
                    timeout=1800,  # 30분 타임아웃 (1h+ 오디오는 Clova 처리에 10분 이상 걸릴 수 있음)
                )

            response.raise_for_status()
            result = response.json()

            # 응답 검증
            if result.get("result") != "COMPLETED":
                raise RuntimeError(
                    f"Clova API Error: {result.get('message', 'Unknown error')}"
                )

            # 응답을 TranscriptSegment 및 화자 이름 리스트로 변환
            segments = self._parse_clova_response(result)
            speaker_names = self._parse_clova_speakers(result)
            return segments, speaker_names

        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Clova API Request Error: {str(e)}")

    def submit_async(
        self,
        file_path: str,
        callback_url: str,
        language: str = "ko-KR",
        domain_keywords: Optional[List[str]] = None,
    ) -> str:
        """긴 오디오를 위한 비동기(콜백) 인식을 제출하고 job token을 반환한다.

        sync와 달리 처리 완료까지 커넥션을 붙들지 않는다(제출 자체는 수 초 내 끝남).
        실제 인식 결과는 Clova가 완료 후 callback_url로 POST 해준다.
        Clova 사양상 async 요청은 callback_url(또는 Object Storage 연동)이 반드시 있어야 한다.

        Returns:
            token: 이후 fetch_job_result(token)으로 상태를 조회하거나,
                   콜백 payload의 token과 대조하는 데 사용한다.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        params = {
            "language": language,
            "completion": "async",
            "callback": callback_url,
            "fullText": True,
        }
        if domain_keywords:
            params["boostings"] = [
                {"words": ", ".join(domain_keywords), "weight": 1.0}
            ]

        headers = {"X-CLOVASPEECH-API-KEY": self.secret_key}

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
                    timeout=120,  # 제출 자체는 짧게 끝남(긴 처리는 콜백으로 비동기 전달)
                )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Clova API Request Error: {str(e)}")

        token = result.get("token")
        if not token:
            raise RuntimeError(f"Clova API Error: 작업 토큰 발급 실패 ({result})")
        return token

    def fetch_job_result(self, token: str) -> Dict[str, Any]:
        """token으로 비동기 작업의 현재 상태/결과를 조회한다.

        주로 서버 재시작으로 콜백을 놓쳤을 수 있는 작업을 복구할 때 사용한다.
        응답의 "result" 필드는 WAITING|PROCESSING|COMPLETED|FAILED|TIMEOUT.
        """
        headers = {"X-CLOVASPEECH-API-KEY": self.secret_key}
        try:
            response = requests.get(
                f"{self.invoke_url}/recognizer/{token}",
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Clova API Request Error: {str(e)}")

    @staticmethod
    def parse_result(result: Dict[str, Any]) -> Tuple[List[TranscriptSegment], List[str]]:
        """COMPLETED 상태인 Clova 응답(sync 응답 또는 콜백/조회 결과 모두 동일 형식)을
        (TranscriptSegment 리스트, 화자 이름 리스트)로 변환한다."""
        if result.get("result") != "COMPLETED":
            raise RuntimeError(
                f"Clova API Error: {result.get('message', 'Unknown error')}"
            )
        segments = ClovaSpeechClient._parse_clova_response(result)
        speaker_names = ClovaSpeechClient._parse_clova_speakers(result)
        return segments, speaker_names

    @staticmethod
    def _parse_clova_speakers(response: Dict[str, Any]) -> List[str]:
        """Clova 응답의 speakers를 label(1-based) 오름차순 이름 리스트로 변환."""
        speakers_data = response.get("speakers") or []
        parsed: List[Tuple[int, str]] = []
        for spk in speakers_data:
            try:
                label = int(spk.get("label"))
            except (TypeError, ValueError):
                continue
            name = f"화자{spk.get('name')}" or f"화자{label}"
            parsed.append((label, name))

        parsed.sort(key=lambda x: x[0])
        return [name for _, name in parsed]

    @staticmethod
    def _parse_clova_response(response: Dict[str, Any]) -> List[TranscriptSegment]:
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
) -> Tuple[List[TranscriptSegment], List[str]]:
    """
    편의 함수: 파일을 바로 변환

    Args:
        file_path: 음성 파일 경로
        language: 언어 코드
        domain_keywords: 도메인 키워드 리스트

    Returns:
        (TranscriptSegment 리스트, 화자 이름 리스트)
    """
    client = ClovaSpeechClient()
    return client.convert_file_to_transcript(
        file_path, language, domain_keywords
    )


def submit_async(
    file_path: str,
    callback_url: str,
    language: str = "ko-KR",
    domain_keywords: Optional[List[str]] = None,
) -> str:
    """편의 함수: 비동기(콜백) 인식을 제출하고 job token을 반환."""
    client = ClovaSpeechClient()
    return client.submit_async(file_path, callback_url, language, domain_keywords)


def fetch_job_result(token: str) -> Dict[str, Any]:
    """편의 함수: token으로 작업 상태/결과를 조회."""
    client = ClovaSpeechClient()
    return client.fetch_job_result(token)


def parse_result(result: Dict[str, Any]) -> Tuple[List[TranscriptSegment], List[str]]:
    """편의 함수: COMPLETED 응답을 (segments, speaker_names)로 변환."""
    return ClovaSpeechClient.parse_result(result)
