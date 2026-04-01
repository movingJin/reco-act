import json
import os
import time
from typing import Any, Dict, Optional
from dotenv import load_dotenv

load_dotenv()
import requests
import urllib3

# 경고 억제 (개발 환경에서만 사용)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

print(f"secret:{os.getenv('RTZR_CLIENT_SECRET')}, id:{os.getenv('RTZR_CLIENT_ID')}")

class RTZROpenAPIClient:
    """Minimal client for RTZR OpenAPI (auth + STT file).

    - Fetches JWT via /v1/authenticate using client_id/client_secret
    - Submits a file transcription job via /v1/transcribe
    - Polls /v1/transcribe/{id} every few seconds until completed/failed
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: str = "https://openapi.vito.ai",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or os.getenv("RTZR_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("RTZR_CLIENT_SECRET")
        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Missing credentials. Set RTZR_CLIENT_ID and RTZR_CLIENT_SECRET "
                "environment variables, or pass client_id/client_secret to RTZROpenAPIClient."
            )
        self._sess = requests.Session()
        self._sess.verify = False
        self._token: Optional[Dict[str, Any]] = None

    @property
    def token(self) -> str:
        # Renew if missing or expiring within 30 minutes
        if self._token is None or self._token.get("expire_at", 0) < time.time() - 1800:
            resp = self._sess.post(
                f"{self.base_url}/v1/authenticate",
                data={"client_id": self.client_id, "client_secret": self.client_secret},
            )
            resp.raise_for_status()
            self._token = resp.json()
        access = self._token.get("access_token")
        if not access:
            raise RuntimeError("authenticate: 'access_token' not found in response")
        return access

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def transcribe_file(self, file_path: str, config: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/v1/transcribe"
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f)}
            data = {"config": json.dumps(config)}
            resp = self._sess.post(url, headers=self._auth_headers(), files=files, data=data)
            resp.raise_for_status()
            return resp.json()

    def get_transcription(self, transcribe_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/v1/transcribe/{transcribe_id}"
        resp = self._sess.get(url, headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json()

    def wait_for_result(
        self,
        transcribe_id: str,
        poll_interval_sec: int = 5,
        timeout_sec: int = 3600,
    ) -> Dict[str, Any]:
        deadline = time.time() + timeout_sec
        while True:
            if time.time() > deadline:
                raise TimeoutError("Timed out waiting for transcription result")
            result = self.get_transcription(transcribe_id)
            status = result.get("status")
            if status in ("completed", "failed"):
                return result
            time.sleep(poll_interval_sec)


# Preset configurations
PRESETS: Dict[str, Dict[str, Any]] = {
    "sommers_basic": {  # 1) sommers without diarization
        "model_name": "sommers",
        "use_diarization": False,
        "domain": "GENERAL",
    },
    "sommers_call_diarization": {  # 2) sommers + diarization + CALL, spk_count=2
        "model_name": "sommers",
        "domain": "CALL",
        "use_diarization": True,
        "diarization": {"spk_count": 2},
    },
    "sommers_ja": {  # 3) sommers japanese without diarization
        "model_name": "sommers",
        "use_diarization": False,
        "domain": "GENERAL",
        "language": "ja",
    },
    "whisper_en_diarization": {  # 4) whisper + diarization, language=en
        "model_name": "whisper",
        "language": "en",
        "use_diarization": True,
    },
    # Additional commonly requested options
    "paragraph_split_80": {"use_paragraph_splitter": True, "paragraph_splitter": {"max": 80}},
    "keywords_example": {"keywords": ["stt", "returnzero", "api"]},
    "with_word_timestamps": {"use_word_timestamp": True},
    "disfluency_on": {"use_disfluency_filter": True},
    "profanity_on": {"use_profanity_filter": True},
    "whisper_detect_multi": {
        "model_name": "whisper",
        "language": "multi",
        "language_candidates": ["ko", "en"],
    },
}


def convert() -> list:
    audio_path = os.getenv("AUDIO_PATH", "Stt-테스트.wav")
    preset_name = os.getenv("PRESET", "sommers_basic")

    if preset_name not in PRESETS:
        raise ValueError(f"Unknown PRESET '{preset_name}'. Available: {sorted(PRESETS.keys())}")

    config = PRESETS[preset_name]

    client = RTZROpenAPIClient()

    submit = client.transcribe_file(audio_path, config)
    transcribe_id = submit.get("id")
    response = client.wait_for_result(transcribe_id, poll_interval_sec=5)
    return response["results"]["utterances"]


if __name__ == "__main__":
    print(convert())