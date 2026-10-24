"""용어 사전 치환 (01-plan 3.4, 00-decisions 2.4의 2단). 순수 함수다.

term_corrections의 오인식 표현(wrong)을 정식 표기(correct)로 바꾼다.
- 긴 표현부터 맞춘다. 한 번 바꾼 곳은 다른 항목이 다시 건드리지 않는다(원문 위치 기준으로 한 번에 바꾼다).
- 한국어는 조사가 붙으므로 부분 문자열로 맞춘다("벡타디비를" → "벡터DB를").
- 이미 정식 표기인 곳과 겹치면 바꾸지 않는다(wrong "엘라스틱" → correct "엘라스틱서치"일 때 "엘라스틱서치"는 그대로).
- 영문 대소문자는 구분하지 않는다. 영문·숫자로 시작하거나 끝나는 표현은 앞뒤가 영문·숫자인 곳(다른 단어의 일부)에서는 맞추지 않는다.
"""
import re
from typing import Sequence

from models.meeting import TranscriptSegment

_ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")


def _pattern(term: str) -> re.Pattern:
    before = r"(?<![A-Za-z0-9])" if _ASCII_ALNUM.match(term[0]) else ""
    after = r"(?![A-Za-z0-9])" if _ASCII_ALNUM.match(term[-1]) else ""
    return re.compile(before + re.escape(term) + after, re.IGNORECASE)


def correct_text(text: str, corrections: Sequence[tuple[str, str]]) -> str:
    """text 안의 오인식 표현을 정식 표기로 바꾼 문자열을 돌려준다."""
    entries = sorted(((w, c) for w, c in corrections if w and c and w != c), key=lambda e: len(e[0]), reverse=True)
    if not entries:
        return text

    # 이미 정식 표기인 구간. 이 구간에 걸치는 오인식 표현은 바꾸지 않는다.
    # 정식 표기가 오인식 표현 안에 통째로 든 경우("검색엔진엔진" → "검색엔진")는 오인식 표현 쪽을 바꾼다.
    protected = [m.span() for correct in {c for _, c in entries} for m in _pattern(correct).finditer(text)]
    taken: list[tuple[int, int, str]] = []
    for wrong, correct in entries:
        for m in _pattern(wrong).finditer(text):
            start, end = m.span()
            if any(start < p_end and p_start < end and not (start <= p_start and p_end <= end) for p_start, p_end in protected):
                continue
            if any(start < t_end and t_start < end for t_start, t_end, _ in taken):
                continue  # 더 긴 표현이 이미 차지했다
            taken.append((start, end, correct))

    out, cursor = [], 0
    for start, end, correct in sorted(taken):
        out.append(text[cursor:start])
        out.append(correct)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def apply_term_corrections(
    segments: Sequence[TranscriptSegment], corrections: Sequence[tuple[str, str]]
) -> list[TranscriptSegment]:
    """세그먼트마다 text를 치환한 새 목록을 돌려준다. 화자·시간은 그대로다."""
    return [seg.model_copy(update={"text": correct_text(seg.text, corrections)}) for seg in segments]
