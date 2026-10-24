"""LLM 용어 보정 (선택, TERM_CORRECTION_LLM. 01-plan 3.4, 00-decisions 2.4의 3단).

사전 치환이 끝난 세그먼트를 묶음으로 나눠 용어집과 함께 LLM에 보내고, 용어 오인식만 고친 {index, text}를 받는다.
의미가 바뀌는 것을 막으려고 다음 경우에는 그 묶음 전체를 보정 전 값으로 되돌린다.
- 돌려받은 세그먼트의 개수나 index가 보낸 것과 다르다.
- 한 세그먼트라도 바뀐 글자 수가 한도(max(MIN_CHANGE_CHARS, len * MAX_CHANGE_RATIO))를 넘는다.
LLM 호출이 실패해도 전사는 실패시키지 않고 그 묶음을 보정 전 값으로 둔다(원문은 stt_results에 따로 남는다).
"""
import json
from difflib import SequenceMatcher
from typing import Callable, Sequence

from pydantic import BaseModel, Field

from models.meeting import TranscriptSegment

BATCH_MAX_SEGMENTS = 20
BATCH_MAX_CHARS = 2000
MAX_CHANGE_RATIO = 0.3
MIN_CHANGE_CHARS = 6

SYSTEM_PROMPT = (
    "너는 회의 음성 전사의 용어 오인식만 고치는 교정기다.\n"
    "- 용어집에 있는 용어가 잘못 받아써진 곳만 용어집의 표기로 고친다.\n"
    "- 그 밖의 단어, 말버릇, 어순, 조사, 띄어쓰기, 문장부호는 그대로 둔다. 문장을 다듬거나 요약하거나 보충하지 않는다.\n"
    "- 고칠 곳이 없는 세그먼트는 원문을 그대로 돌려준다.\n"
    "- 입력의 모든 세그먼트를 같은 index로, 같은 순서로 하나씩 돌려준다."
)


class CorrectedSegment(BaseModel):
    index: int
    text: str


class CorrectionOutput(BaseModel):
    segments: list[CorrectedSegment] = Field(description="입력과 같은 순서·개수의 세그먼트")


# (묶음의 [{index, text}], 용어집) -> 돌려받은 [{index, text}]
CorrectionRequest = Callable[[list[dict], list[str]], list[dict]]


def request_llm(items: list[dict], glossary: list[str]) -> list[dict]:
    """get_llm()(airgap은 vLLM/OpenRouter)에 구조화 출력으로 요청한다. 2026-10-24 OpenRouter qwen3-32b로 프롬프트 동작을 확인했다."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from utils.config import get_llm

    user = "용어집: " + ", ".join(glossary) + "\n\n세그먼트:\n" + json.dumps(items, ensure_ascii=False)
    output = get_llm(0).with_structured_output(CorrectionOutput).invoke([SystemMessage(SYSTEM_PROMPT), HumanMessage(user)])
    return [segment.model_dump() for segment in output.segments]


def changed_chars(before: str, after: str) -> int:
    """before에서 after로 바뀐 글자 수(바뀐 구간마다 지운 것과 넣은 것 중 긴 쪽)."""
    return sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
        if tag != "equal"
    )


def _batches(segments: Sequence[TranscriptSegment]) -> list[list[int]]:
    batches, current, chars = [], [], 0
    for i, seg in enumerate(segments):
        if current and (len(current) >= BATCH_MAX_SEGMENTS or chars + len(seg.text) > BATCH_MAX_CHARS):
            batches.append(current)
            current, chars = [], 0
        current.append(i)
        chars += len(seg.text)
    if current:
        batches.append(current)
    return batches


def _accepted(sent: list[dict], received: list[dict]) -> bool:
    if [item.get("index") for item in received] != [item["index"] for item in sent]:
        return False
    for before, after in zip(sent, received):
        text = after.get("text")
        if not isinstance(text, str) or not text.strip():
            return False
        if changed_chars(before["text"], text) > max(MIN_CHANGE_CHARS, len(before["text"]) * MAX_CHANGE_RATIO):
            return False
    return True


def correct_segments(
    segments: Sequence[TranscriptSegment], glossary: Sequence[str], request: CorrectionRequest = request_llm
) -> list[TranscriptSegment]:
    """용어 오인식을 LLM으로 고친 새 목록을 돌려준다. 용어집이 비어 있으면 부르지 않는다. 화자·시간은 그대로다."""
    result = list(segments)
    terms = list(dict.fromkeys(term.strip() for term in glossary if term and term.strip()))
    if not terms:
        return result
    for batch in _batches(segments):
        # index는 묶음 안의 순번이다. 세그먼트 전체 번호를 보내면 긴 회의에서 숫자가 커질 뿐 이점이 없다.
        sent = [{"index": n, "text": segments[i].text} for n, i in enumerate(batch)]
        try:
            received = request(sent, terms)
        except Exception as e:
            print(f"[correction] batch {batch[0]}-{batch[-1]} kept as is: LLM request failed ({type(e).__name__}: {e})")
            continue
        if not _accepted(sent, received):
            print(f"[correction] batch {batch[0]}-{batch[-1]} reverted: response failed the guard")
            continue
        for i, item in zip(batch, received):
            result[i] = segments[i].model_copy(update={"text": item["text"]})
    return result
