"""OpenRouter qwen/qwen3-32b로 airgap LLM 경로를 실제로 호출한다 (01-plan 7장 LLM 모의, PR-3).

`-m external`로만 돈다. OPENROUTER_API_KEY env가 없으면 건너뛴다. 합성 회의(synthetic_meeting)만 보낸다.
공급자를 고정하려면 OPENROUTER_PROVIDER=SiliconFlow처럼 지정한다(OpenRouter 공급자 이름).
"""
import os

import pytest
from pydantic import BaseModel

from conftest import EXTERNAL_OPENROUTER_API_KEY
from graph import summary_workflow
from graph.summary_workflow import SummaryNode
from synthetic_meeting import make_synthetic_meeting

pytestmark = pytest.mark.external


@pytest.fixture
def openrouter(reload_config, monkeypatch):
    if not EXTERNAL_OPENROUTER_API_KEY:
        pytest.skip("OPENROUTER_API_KEY not set")
    cfg = reload_config(
        APP_PROFILE="airgap",
        LLM_SERVING="openrouter",
        LLM_BASE_URL="https://openrouter.ai/api/v1",
        LLM_MODEL="qwen/qwen3-32b",
        LLM_API_KEY=EXTERNAL_OPENROUTER_API_KEY,
    )
    # max_tokens가 없으면 OpenRouter는 공급자의 최대 출력(SiliconFlow는 약 11만 토큰)만큼 크레딧을 잡아 두고,
    # 키 한도가 그보다 작으면 402로 거부한다. 테스트는 응답 길이 상한을 둔다.
    body = {**cfg.LLM_THINKING_OFF_BODY["openrouter"], "max_tokens": 4096}
    provider = os.environ.get("OPENROUTER_PROVIDER")
    if provider:
        body["provider"] = {**body["provider"], "order": [provider], "allow_fallbacks": False}
    monkeypatch.setitem(cfg.LLM_THINKING_OFF_BODY, "openrouter", body)
    return cfg


def test_thinking_off_with_structured_output(openrouter):
    class Answer(BaseModel):
        subject: str
        next_steps: list[str]

    meeting = make_synthetic_meeting()
    transcript = "\n".join(f"[{s.start}-{s.end}] {s.speaker_name}: {s.text}" for s in meeting.transcript)
    llm = openrouter.get_llm(0).with_structured_output(Answer, include_raw=True)
    result = llm.invoke(f"다음 회의의 주제와 다음 할 일을 정리해줘.\n{transcript}")

    assert result["parsing_error"] is None
    assert result["parsed"].subject
    raw = result["raw"]
    assert "<think>" not in raw.content
    assert raw.usage_metadata["output_token_details"].get("reasoning", 0) == 0


def test_summary_workflow(openrouter, monkeypatch):
    meeting = make_synthetic_meeting()
    monkeypatch.setattr(summary_workflow, "load_meeting", lambda meeting_id: meeting)

    result = SummaryNode().run(meeting.id)

    assert result is not None
    assert result.subject and result.next_steps and result.meeting_notes
    assert result.paragraphs
    for paragraph in result.paragraphs:
        assert set(paragraph) == {"subject", "start", "end", "summary"}
        assert 0 <= paragraph["start"] <= paragraph["end"] <= meeting.transcript[-1].end


def test_term_correction(openrouter):
    """LLM 용어 보정(PR-9)이 용어만 고치고 나머지(말버릇, 고칠 것 없는 세그먼트)는 그대로 둔다. 합성 전사만 보낸다."""
    from models.meeting import TranscriptSegment
    from pipeline.correction import correct_segments

    texts = [
        "오늘은 엘라스틱 서치 인덱스 재구축 일정을 정하겠습니다.",
        "어 그 뭐냐 벡타 디비 비용은 다음 주에 비교하죠.",
        "쿠버네티스 클러스터는 문제 없습니다.",
        "네 좋습니다 그럼 금요일에 다시 보죠.",
    ]
    segments = [TranscriptSegment(speaker_index=i % 2, text=t, start=i * 1000, end=i * 1000 + 900) for i, t in enumerate(texts)]

    result = correct_segments(segments, ["엘라스틱서치", "벡터DB", "쿠버네티스", "인덱스"])

    assert [s.text for s in result] == [
        "오늘은 엘라스틱서치 인덱스 재구축 일정을 정하겠습니다.",
        "어 그 뭐냐 벡터DB 비용은 다음 주에 비교하죠.",
        texts[2],
        texts[3],
    ]
    assert [(s.speaker_index, s.start, s.end) for s in result] == [(s.speaker_index, s.start, s.end) for s in segments]
