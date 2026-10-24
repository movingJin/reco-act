"""요약 map-reduce (airgap, 01-plan 3.7, PR-12). LLM은 대역으로 바꾸고 합성 회의만 쓴다.

public(single)은 현행 프롬프트(전사 전체의 Python repr)를 그대로 보내는지, airgap(map_reduce)은 압축 포맷의 청크마다
문단을 요청해 이어 붙이는지 확인한다.
"""
import re

import pytest

from graph import summary_workflow
from graph.summary_workflow import SummaryNode
from graph.transcript_chunks import chunk_segments, format_segment
from models.meeting import TranscriptSegmentResponse
from synthetic_meeting import make_synthetic_meeting
from utils import config
from utils.config import parse_positive_int, parse_summary_strategy

LINE = re.compile(r"^\[(\d+)-(\d+)\] ", re.MULTILINE)


class FakeLLM:
    """get_llm() 대역. 문단 요청에는 받은 줄의 시간 범위를 덮는 문단 하나를 돌려준다(시간을 바꿔 보려면 shift)."""

    def __init__(self):
        self.calls = []  # (응답 스키마의 필드 이름, 사용자 메시지)
        self.shift = (0, 0)

    def __call__(self, temperature=0.3):
        return self

    def with_structured_output(self, schema):
        llm = self

        class Runner:
            def invoke(self, messages):
                fields = set(schema.model_fields)
                user = messages[1].content
                llm.calls.append((fields, user))
                if "paragraphs" in fields:
                    spans = [(int(a), int(b)) for a, b in LINE.findall(user)]
                    if not spans:
                        return schema(paragraphs=[])
                    start, end = spans[0][0] + llm.shift[0], spans[-1][1] + llm.shift[1]
                    return schema(paragraphs=[{"subject": f"{start}부터", "start": start, "end": end, "summary": "요약"}])
                if "next_steps" in fields:
                    return schema(subject="문서 검색 개편", next_steps=["1. 테스트 문서 준비."])
                return schema(meeting_notes="1. 회의 개요")

        return Runner()

    def paragraph_prompts(self):
        return [user for fields, user in self.calls if "paragraphs" in fields]


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(summary_workflow, "get_llm", fake)
    return fake


@pytest.fixture
def meeting(monkeypatch):
    synthetic = make_synthetic_meeting()
    monkeypatch.setattr(summary_workflow, "load_meeting", lambda meeting_id: synthetic)
    return synthetic


def map_reduce(monkeypatch, chunk_chars=12000):
    monkeypatch.setattr(config, "SUMMARY_STRATEGY", "map_reduce")
    monkeypatch.setattr(config, "SUMMARY_CHUNK_CHARS", chunk_chars)


def segment(start, end, text, speaker="화자1"):
    return TranscriptSegmentResponse(speaker_index=0, speaker_name=speaker, text=text, start=start, end=end)


# ==================== 압축·분할 ====================

def test_format_segment():
    assert format_segment(segment(7000, 15000, "검색 서버는 키워드만 지원합니다.", "화자2")) == "[7000-15000] 화자2: 검색 서버는 키워드만 지원합니다."


def test_chunks_respect_budget_and_segment_boundaries():
    segments = make_synthetic_meeting().transcript

    chunks = chunk_segments(segments, 200)

    assert [s for chunk in chunks for s in chunk] == segments  # 순서대로, 빠짐없이, 나누지 않고
    assert len(chunks) > 1
    for chunk in chunks:
        assert sum(len(format_segment(s)) + 1 for s in chunk) <= 200


def test_oversized_segment_gets_its_own_chunk_and_blank_is_skipped():
    long = segment(1000, 2000, "가" * 300)
    segments = [segment(0, 900, "짧은 말"), segment(950, 990, "   "), long, segment(2100, 2500, "끝")]

    assert chunk_segments(segments, 100) == [[segments[0]], [long], [segments[3]]]
    assert chunk_segments([], 100) == []


# ==================== 설정 ====================

@pytest.mark.parametrize("profile, expected", [("public", "single"), ("airgap", "map_reduce")])
def test_strategy_default(profile, expected):
    assert parse_summary_strategy(None, profile) == expected


@pytest.mark.parametrize("value", ["", "mapreduce", "Single", " single"])
def test_invalid_strategy_is_rejected(value):
    with pytest.raises(ValueError, match="Invalid SUMMARY_STRATEGY"):
        parse_summary_strategy(value, "airgap")


def test_chunk_chars_setting(reload_config):
    assert config.SUMMARY_CHUNK_CHARS == 12000
    assert reload_config(SUMMARY_CHUNK_CHARS="3000").SUMMARY_CHUNK_CHARS == 3000
    for value in ("0", "-1", "abc", ""):
        with pytest.raises(ValueError, match="Invalid SUMMARY_CHUNK_CHARS"):
            parse_positive_int("SUMMARY_CHUNK_CHARS", value, 12000)


# ==================== 요약 흐름 ====================

def test_public_single_sends_current_prompt(llm, meeting):
    """public은 현행 그대로: 문단 요청 한 번, 전사 전체의 Python repr."""
    assert config.SUMMARY_STRATEGY == "single"

    SummaryNode().run(meeting.id)

    (prompt,) = llm.paragraph_prompts()
    assert prompt == f"""
    다음 회의록을 읽고, 문단을 생성해줘.
    {meeting.transcript}
    """
    assert len(llm.calls) == 3


def test_short_meeting_is_one_chunk(llm, meeting, monkeypatch):
    map_reduce(monkeypatch)

    result = SummaryNode().run(meeting.id)

    (prompt,) = llm.paragraph_prompts()
    assert "부분으로 나눈 것 중" not in prompt
    assert prompt.splitlines()[1] == '각 줄은 "[시작-끝] 화자: 발화" 형식이고, 시간은 회의 시작부터의 밀리초다.'
    assert prompt.splitlines()[2:] == [format_segment(s) for s in meeting.transcript]
    assert "TranscriptSegmentResponse" not in prompt  # repr을 보내지 않는다
    assert len(llm.calls) == 3
    assert result.paragraphs == [{"subject": "0부터", "start": 0, "end": 76000, "summary": "요약"}]


def test_long_meeting_is_mapped_per_chunk_and_reduced_in_order(llm, meeting, monkeypatch):
    map_reduce(monkeypatch, chunk_chars=200)
    chunks = chunk_segments(meeting.transcript, 200)

    result = SummaryNode().run(meeting.id)

    prompts = llm.paragraph_prompts()
    assert len(prompts) == len(chunks) > 1
    for number, (prompt, chunk) in enumerate(zip(prompts, chunks), 1):
        lines = prompt.splitlines()
        assert lines[1] == f"전체 회의를 {len(chunks)}부분으로 나눈 것 중 {number}번째 부분이다."
        assert lines[3:] == [format_segment(s) for s in chunk]  # 그 청크의 줄만
    assert result.paragraphs == [
        {"subject": f"{chunk[0].start}부터", "start": chunk[0].start, "end": chunk[-1].end, "summary": "요약"} for chunk in chunks
    ]
    # reduce: 다음 할 일·주제와 회의록 노드는 이어 붙인 문단 전체로 한 번씩 돈다
    assert len(llm.calls) == len(chunks) + 2
    assert str(result.paragraphs) in llm.calls[len(chunks)][1]


def test_paragraph_times_are_kept_inside_their_chunk(llm, meeting, monkeypatch):
    map_reduce(monkeypatch, chunk_chars=200)
    llm.shift = (-5000, 999999)  # 청크 밖 시간을 돌려준다
    chunks = chunk_segments(meeting.transcript, 200)

    result = SummaryNode().run(meeting.id)

    assert [(p["start"], p["end"]) for p in result.paragraphs] == [(chunk[0].start, chunk[-1].end) for chunk in chunks]


def test_inverted_paragraph_time_is_fixed(llm, meeting, monkeypatch):
    map_reduce(monkeypatch)
    llm.shift = (50000, -60000)  # start 50000, end 16000

    result = SummaryNode().run(meeting.id)

    (paragraph,) = result.paragraphs
    assert paragraph["start"] == 50000 and paragraph["end"] == 50000


def test_empty_transcript_skips_paragraph_request(llm, meeting, monkeypatch):
    map_reduce(monkeypatch)
    meeting.transcript = []

    result = SummaryNode().run(meeting.id)

    assert llm.paragraph_prompts() == []
    assert result.paragraphs == []
    assert len(llm.calls) == 2  # 다음 할 일·주제, 회의록은 현행처럼 돈다
