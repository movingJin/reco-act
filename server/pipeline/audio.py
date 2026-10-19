"""오디오 정규화 (airgap, 01-plan 3.3).

업로드 원본을 ffmpeg로 mono 16kHz 16bit PCM WAV로 바꾼다. 이 파일을 STT·화자분리 입력과 재생용 audio_file로 함께 쓴다.
public은 transcription_service의 pydub 경로(샘플레이트·채널을 원본대로 유지)를 그대로 쓴다.
"""
import subprocess
import wave
from pathlib import Path

SAMPLE_RATE = 16000


class AudioDecodeError(Exception):
    """ffmpeg가 원본을 읽지 못했다(손상, 오디오 트랙 없음 등). 다시 시도해도 같다."""


def normalize_audio(source_path: str, wav_path: str) -> int:
    """source_path를 mono 16kHz PCM WAV로 wav_path에 쓰고 길이(ms)를 돌려준다.

    포맷은 확장자가 아니라 내용으로 판별한다(ffmpeg 기본 동작). 웹 녹음은 이름만 .wav인 webm/opus일 수 있다(01-plan 1.7).
    """
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", source_path,
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", wav_path,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, errors="replace")
    if completed.returncode != 0:
        Path(wav_path).unlink(missing_ok=True)
        raise AudioDecodeError(f"ffmpeg exited with {completed.returncode}: {completed.stderr.strip()[-500:]}")
    with wave.open(wav_path, "rb") as wav:
        return round(wav.getnframes() * 1000 / wav.getframerate())
