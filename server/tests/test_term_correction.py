"""용어 사전 치환과 LLM 보정 가드 (01-plan 3.4, PR-9). 순수 함수라 DB와 LLM 없이 확인한다(LLM은 대역)."""
import pytest

from models.meeting import TranscriptSegment
from pipeline import correction
from pipeline.correction import changed_chars, correct_segments
from pipeline.glossary import apply_term_corrections, correct_text


def seg(text, speaker_index=0, start=0, end=1000):
    return TranscriptSegment(speaker_index=speaker_index, text=text, start=start, end=end)


# ==================== 사전 치환 ====================

@pytest.mark.parametrize("text, expected", [
    ("벡타디비를 쓰자", "벡터DB를 쓰자"),  # 조사가 붙어도 맞춘다
    ("벡타디비와 벡타디비", "벡터DB와 벡터DB"),
    ("엘라스틱 서치 인덱스", "엘라스틱서치 인덱스"),
    ("오늘 일정", "오늘 일정"),  # 해당 없음
])
def test_substring_replacement_with_particles(text, expected):
    corrections = [("벡타디비", "벡터DB"), ("엘라스틱 서치", "엘라스틱서치")]
    assert correct_text(text, corrections) == expected


def test_longest_entry_wins_and_no_cascade():
    corrections = [("디비", "DB"), ("벡타 디비", "벡터DB"), ("벡터DB", "벡터 데이터베이스")]
    # "벡타 디비"가 "디비"보다 먼저 맞고, 바꾼 결과("벡터DB")를 다른 항목이 다시 바꾸지 않는다
    assert correct_text("벡타 디비와 디비", corrections) == "벡터DB와 DB"


def test_already_correct_term_is_left_alone():
    corrections = [("엘라스틱", "엘라스틱서치")]
    assert correct_text("엘라스틱서치와 엘라스틱을 비교", corrections) == "엘라스틱서치와 엘라스틱서치을 비교"


def test_correct_term_inside_wrong_is_still_fixed():
    assert correct_text("검색엔진엔진 개편", [("검색엔진엔진", "검색엔진")]) == "검색엔진 개편"


def test_ascii_terms_ignore_case_but_not_inside_words():
    corrections = [("elastic search", "Elasticsearch"), ("AI", "A.I.")]
    assert correct_text("Elastic Search 도입", corrections) == "Elasticsearch 도입"
    assert correct_text("AIR 장비와 AI 모델, AI를", corrections) == "AIR 장비와 A.I. 모델, A.I.를"  # 한글 조사는 붙어도 된다


def test_invalid_entries_are_ignored():
    assert correct_text("그대로", [("", "x"), ("그대로", "그대로"), ("y", "")]) == "그대로"
    assert correct_text("그대로", []) == "그대로"


def test_apply_keeps_speaker_and_time():
    segments = [seg("벡타디비 얘기", speaker_index=1, start=10, end=20), seg("다른 얘기")]

    result = apply_term_corrections(segments, [("벡타디비", "벡터DB")])

    assert result == [seg("벡터DB 얘기", speaker_index=1, start=10, end=20), seg("다른 얘기")]
    assert segments[0].text == "벡타디비 얘기"  # 원본(원문 보존용)은 바꾸지 않는다


# ==================== LLM 보정 ====================

class FakeLLM:
    """요청을 기록하고, 정해 둔 함수로 응답을 만든다."""

    def __init__(self, respond):
        self.respond = respond
        self.requests = []

    def __call__(self, items, glossary):
        self.requests.append((items, glossary))
        return self.respond(items)


def fix_terms(items):
    return [{"index": i["index"], "text": i["text"].replace("벡타 디비", "벡터DB")} for i in items]


def test_changed_chars():
    assert changed_chars("같은 문장", "같은 문장") == 0
    assert changed_chars("벡타 디비 비용", "벡터DB 비용") == 4  # "타 디비" → "터DB"
    assert changed_chars("abc", "") == 3


def test_term_fix_is_applied():
    segments = [seg("어 그 뭐냐 벡타 디비 비용은 다음 주에", speaker_index=1, start=5, end=9), seg("네 좋습니다")]
    llm = FakeLLM(fix_terms)

    result = correct_segments(segments, ["벡터DB", "", "벡터DB", "쿠버네티스"], request=llm)

    assert result == [seg("어 그 뭐냐 벡터DB 비용은 다음 주에", speaker_index=1, start=5, end=9), seg("네 좋습니다")]
    (items, glossary), = llm.requests
    assert items == [{"index": 0, "text": segments[0].text}, {"index": 1, "text": "네 좋습니다"}]
    assert glossary == ["벡터DB", "쿠버네티스"]  # 빈 값과 중복은 뺀다


def test_empty_glossary_skips_llm():
    llm = FakeLLM(fix_terms)
    assert correct_segments([seg("벡타 디비")], [" ", ""], request=llm) == [seg("벡타 디비")]
    assert llm.requests == []


@pytest.mark.parametrize("respond", [
    lambda items: fix_terms(items)[:-1],  # 세그먼트 하나를 빠뜨림
    lambda items: fix_terms(items) + [{"index": 99, "text": "덤"}],  # 하나 더 붙임
    lambda items: list(reversed(fix_terms(items))),  # 순서가 바뀜
    lambda items: [{**i, "text": ""} if i["index"] == 1 else i for i in fix_terms(items)],  # 빈 문장
    lambda items: [{**i, "text": "완전히 다른 문장으로 바꿔 썼습니다"} if i["index"] == 1 else i for i in fix_terms(items)],
])
def test_guard_reverts_the_whole_batch(respond):
    segments = [seg("벡타 디비 테스트는 다음 주에 끝납니다"), seg("일정은 금요일까지 정하면 됩니다")]

    assert correct_segments(segments, ["벡터DB"], request=FakeLLM(respond)) == segments


def test_short_segment_allows_small_absolute_change():
    # 4글자 문장에서 4글자가 바뀌어도 MIN_CHANGE_CHARS(6) 안이라 받는다
    segments = [seg("벡타디비")]
    llm = FakeLLM(lambda items: [{"index": 0, "text": "벡터DB"}])
    assert correct_segments(segments, ["벡터DB"], request=llm) == [seg("벡터DB")]


def test_llm_failure_keeps_text_and_does_not_raise():
    def broken(items, glossary):
        raise ConnectionError("vLLM down")

    segments = [seg("벡타 디비")]
    assert correct_segments(segments, ["벡터DB"], request=broken) == segments


def test_batches_split_and_fail_independently(monkeypatch):
    monkeypatch.setattr(correction, "BATCH_MAX_SEGMENTS", 2)
    segments = [seg(f"벡타 디비 {i}번") for i in range(5)]
    calls = []

    def respond(items, glossary):
        calls.append(len(items))
        if len(calls) == 2:  # 두 번째 묶음만 개수를 틀린다
            return fix_terms(items)[:1]
        return fix_terms(items)

    result = correct_segments(segments, ["벡터DB"], request=respond)

    assert calls == [2, 2, 1]
    assert [s.text for s in result] == ["벡터DB 0번", "벡터DB 1번", "벡타 디비 2번", "벡타 디비 3번", "벡터DB 4번"]


def test_batches_respect_char_budget(monkeypatch):
    monkeypatch.setattr(correction, "BATCH_MAX_CHARS", 10)
    segments = [seg("가" * 6), seg("나" * 6), seg("다" * 3)]
    sizes = []

    correct_segments(segments, ["용어"], request=lambda items, glossary: (sizes.append(len(items)), items)[1])

    assert sizes == [1, 2]  # 6자 / 6+3자. 한 세그먼트가 예산을 넘어도 혼자 한 묶음이 된다
