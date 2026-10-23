"""airgap 전사 파이프라인의 정규화·화자분리·병합 (01-plan 3.3, PR-6).

병합은 순수 함수라 DB 없이 확인한다. 정규화는 로컬 ffmpeg로 합성 음원(무음·사인파)만 변환한다.
"""
import subprocess
import wave

import pytest
from pydub import AudioSegment

from diarization.base import Turn
from diarization.factory import get_diarizer
from diarization.mock import MOCK_TURNS, MockDiarizer
from models.meeting import TranscriptSegment
from pipeline.audio import AudioDecodeError, normalize_audio
from pipeline.merge import merge_words_and_turns
from stt.base import Word
from stt.mock import MOCK_WORDS
from utils import config
from utils.config import parse_diarizer


def seg(speaker_index, text, start, end):
    return TranscriptSegment(speaker_index=speaker_index, text=text, start=start, end=end)


# ==================== 병합 ====================

def test_mock_words_and_turns_make_two_speakers():
    segments, names = merge_words_and_turns(MOCK_WORDS, MOCK_TURNS)

    assert segments == [
        seg(0, "안녕하세요. 오늘은 검색 개편을 논의합니다.", 0, 3400),
        seg(1, "네, 일정부터 정하죠.", 4200, 6100),
    ]
    assert names == ["화자1", "화자2"]


def test_single_speaker():
    words = [Word(0, 500, "하나"), Word(600, 1000, "둘"), Word(1100, 1500, "셋")]
    segments, names = merge_words_and_turns(words, [Turn(0, 1600, "SPEAKER_03")])

    assert segments == [seg(0, "하나 둘 셋", 0, 1500)]
    assert names == ["화자1"]


def test_speaker_returning_gets_same_index():
    words = [Word(0, 400, "가"), Word(1000, 1400, "나"), Word(2000, 2400, "다")]
    turns = [Turn(0, 500, "A"), Turn(900, 1500, "B"), Turn(1900, 2500, "A")]

    segments, names = merge_words_and_turns(words, turns)

    assert [(s.speaker_index, s.text) for s in segments] == [(0, "가"), (1, "나"), (0, "다")]
    assert names == ["화자1", "화자2"]


def test_word_across_turn_boundary_follows_its_midpoint():
    # "넘어가는"(900~1300)은 A 구간 끝(1000)에 걸치지만 중앙(1100)이 B 구간에 있다
    words = [Word(0, 800, "앞말"), Word(900, 1300, "넘어가는"), Word(1400, 2000, "뒷말")]
    turns = [Turn(0, 1000, "A"), Turn(1000, 2000, "B")]

    segments, _ = merge_words_and_turns(words, turns)

    assert [(s.speaker_index, s.text) for s in segments] == [(0, "앞말"), (1, "넘어가는 뒷말")]


def last_speaker(words, turns):
    """시간상 마지막 단어가 붙은 화자의 index. 앞 단어로 화자1(index 0)을 정해 두고 쓴다."""
    segments, _ = merge_words_and_turns(words, turns)
    return segments[-1].speaker_index


def test_overlapping_turns_pick_the_larger_overlap():
    """exclusive가 아닌 입력(구간이 겹침)이면 중앙을 덮는 구간 중 단어와 더 많이 겹치는 쪽, 같으면 먼저 시작한 쪽."""
    anchor = Word(0, 400, "앞")  # A 구간에만 있어 A가 index 0이 된다
    word = Word(1000, 2000, "겹침")  # 중앙 1500은 A, B 모두에 들어간다

    assert last_speaker([anchor, word], [Turn(0, 1600, "A"), Turn(1200, 3000, "B")]) == 1  # A와 600, B와 800
    assert last_speaker([anchor, word], [Turn(0, 1900, "A"), Turn(1200, 3000, "B")]) == 0  # A와 900, B와 800
    assert last_speaker([anchor, word], [Turn(1200, 3000, "B"), Turn(0, 1800, "A")]) == 0  # 둘 다 800


def test_word_in_gap_takes_the_nearest_turn():
    turns = [Turn(0, 1000, "A"), Turn(3000, 4000, "B")]
    anchor = Word(200, 600, "앞")  # A

    assert last_speaker([anchor, Word(1100, 1500, "x")], turns) == 0  # 중앙 1300: A까지 300, B까지 1700
    assert last_speaker([anchor, Word(2200, 2600, "x")], turns) == 1  # 중앙 2400: A까지 1400, B까지 600
    assert last_speaker([anchor, Word(1800, 2200, "x")], turns) == 0  # 중앙 2000: 같으면 앞 구간


def test_words_outside_all_turns():
    turns = [Turn(1000, 2000, "A"), Turn(3000, 4000, "B")]
    words = [Word(0, 400, "시작"), Word(1200, 1800, "가"), Word(3200, 3800, "나"), Word(5000, 5400, "끝")]

    segments, _ = merge_words_and_turns(words, turns)

    assert [(s.speaker_index, s.text) for s in segments] == [(0, "시작 가"), (1, "나 끝")]


def test_long_silence_splits_same_speaker():
    words = [Word(0, 500, "먼저"), Word(2500, 3000, "바로"), Word(5100, 5600, "나중")]
    segments, names = merge_words_and_turns(words, [Turn(0, 6000, "A")], max_gap_ms=2000)

    # 500→2500은 2000ms(기준 이하)라 붙고, 3000→5100은 2100ms라 나뉜다
    assert segments == [seg(0, "먼저 바로", 0, 3000), seg(0, "나중", 5100, 5600)]
    assert names == ["화자1"]


def test_long_monologue_splits_at_max_length():
    words = [Word(i * 1000, i * 1000 + 800, f"w{i}") for i in range(10)]
    segments, _ = merge_words_and_turns(words, [Turn(0, 10_000, "A")], max_segment_ms=3000)

    assert [(s.start, s.end, s.text) for s in segments] == [
        (0, 2800, "w0 w1 w2"),
        (3000, 5800, "w3 w4 w5"),
        (6000, 8800, "w6 w7 w8"),
        (9000, 9800, "w9"),
    ]


def test_speaker_order_follows_first_word_not_label():
    words = [Word(0, 400, "먼저"), Word(1000, 1400, "다음")]
    turns = [Turn(0, 500, "SPEAKER_01"), Turn(900, 1500, "SPEAKER_00"), Turn(5000, 6000, "SPEAKER_02")]

    segments, names = merge_words_and_turns(words, turns)

    assert [(s.speaker_index, s.text) for s in segments] == [(0, "먼저"), (1, "다음")]
    assert names == ["화자1", "화자2"]  # 말이 붙지 않은 SPEAKER_02는 빠진다


def test_no_turns_keeps_words_as_one_speaker():
    segments, names = merge_words_and_turns(MOCK_WORDS, [])

    assert [(s.speaker_index, s.start, s.end) for s in segments] == [(0, 0, 6100)]
    assert names == ["화자1"]


def test_no_words():
    assert merge_words_and_turns([], MOCK_TURNS) == ([], [])


def test_text_spacing_and_word_order():
    # 엔진이 붙인 앞 공백(faster-whisper 단어 형식)은 떼고, 빈 단어는 버리고, 시간순으로 잇는다
    words = [Word(600, 1000, " 시작하죠."), Word(0, 500, " 네,"), Word(500, 550, "  ")]
    segments, _ = merge_words_and_turns(words, [Turn(0, 1000, "A")])

    assert segments == [seg(0, "네, 시작하죠.", 0, 1000)]


def test_many_turns_are_matched_correctly():
    """2시간 분량(구간 수천 개)에서도 단어마다 자기 구간을 찾는다."""
    turns = [Turn(i * 3000, i * 3000 + 2500, f"S{i % 3}") for i in range(2400)]
    words = [Word(i * 3000 + 1000, i * 3000 + 1500, f"w{i}") for i in range(2400)]

    segments, names = merge_words_and_turns(words, turns)

    assert len(segments) == 2400
    assert [s.speaker_index for s in segments[:6]] == [0, 1, 2, 0, 1, 2]
    assert names == ["화자1", "화자2", "화자3"]


# ==================== 정규화 (ffmpeg) ====================

def read_wav(path):
    with wave.open(str(path), "rb") as wav:
        return wav.getnchannels(), wav.getframerate(), wav.getsampwidth(), wav.getnframes()


def test_normalize_stereo_wav_to_mono_16k(tmp_path):
    source = tmp_path / "source.wav"
    AudioSegment.silent(duration=1500, frame_rate=44100).set_channels(2).export(str(source), format="wav")
    out = tmp_path / "out.wav"

    duration_ms = normalize_audio(str(source), str(out))

    assert duration_ms == 1500
    assert read_wav(out) == (1, 16000, 2, 24000)


def test_normalize_detects_format_by_content(tmp_path):
    """웹 녹음처럼 이름만 .wav이고 내용은 webm/opus인 파일(01-plan 1.7)."""
    source = tmp_path / "recording.wav"
    created = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2.5:sample_rate=48000",
         "-ac", "2", "-c:a", "libopus", "-f", "webm", str(source)],
        capture_output=True,
    )
    if created.returncode != 0:
        pytest.skip("local ffmpeg cannot encode webm/opus")
    assert source.read_bytes()[:4] == b"\x1a\x45\xdf\xa3"  # EBML(webm) 헤더

    duration_ms = normalize_audio(str(source), str(tmp_path / "out.wav"))

    assert duration_ms == 2500
    channels, rate, width, _ = read_wav(tmp_path / "out.wav")
    assert (channels, rate, width) == (1, 16000, 2)


def test_normalize_undecodable_input(tmp_path):
    source = tmp_path / "broken.wav"
    source.write_bytes(b"not audio at all")
    out = tmp_path / "out.wav"

    with pytest.raises(AudioDecodeError, match="ffmpeg exited"):
        normalize_audio(str(source), str(out))
    assert not out.exists()


# ==================== 화자분리 설정·팩토리 ====================

@pytest.mark.parametrize("profile, expected", [("public", None), ("airgap", "pyannote")])
def test_diarizer_default(profile, expected):
    assert parse_diarizer(None, profile) == expected


@pytest.mark.parametrize("profile", ["public", "airgap"])
@pytest.mark.parametrize("value", ["pyannote", "mock"])
def test_valid_diarizers(profile, value):
    assert parse_diarizer(value, profile) == value


@pytest.mark.parametrize("value", ["", "none", "Pyannote", " mock"])
def test_invalid_diarizers_are_rejected(value):
    with pytest.raises(ValueError, match="Invalid DIARIZER"):
        parse_diarizer(value, "airgap")


def test_invalid_diarizer_stops_import(reload_config):
    with pytest.raises(ValueError, match="Invalid DIARIZER"):
        reload_config(DIARIZER="pyannote3")


def test_public_has_no_diarizer():
    assert config.DIARIZER is None
    assert get_diarizer() is None


def test_factory_mock(reload_config):
    reload_config(APP_PROFILE="airgap", DIARIZER="mock")
    assert isinstance(get_diarizer(), MockDiarizer)




def test_mock_turns_cover_mock_words():
    turns = MockDiarizer().diarize("unused.wav")

    assert turns == list(MOCK_TURNS)
    assert all(a.end_ms <= b.start_ms for a, b in zip(turns, turns[1:]))  # exclusive
    for word in MOCK_WORDS:
        mid = (word.start_ms + word.end_ms) / 2
        assert any(t.start_ms <= mid <= t.end_ms for t in turns)
