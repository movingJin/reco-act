"""고정 화자 구간을 돌려주는 화자분리 (테스트·개발용, 01-plan 3.3). 입력 음성은 읽지 않는다."""
from diarization.base import Turn

# stt/mock.py의 MOCK_WORDS에 맞춰 두 화자가 차례로 말한 것으로 둔다(첫 문장 0~3.4초, 둘째 문장 4.2~6.1초).
MOCK_TURNS = (
    Turn(0, 3500, "SPEAKER_00"),
    Turn(4100, 6200, "SPEAKER_01"),
)


class MockDiarizer:
    name = "mock"

    def diarize(self, wav_path: str) -> list[Turn]:
        return list(MOCK_TURNS)

    def unload(self) -> None:
        pass
