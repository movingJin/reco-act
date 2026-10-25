"""평가 지표 (00-decisions 5장 PoC 게이트, 01-plan PR-18): CER, 용어 적중률, DER.

- CER: 정답과 인식 결과를 이어 붙여 정규화(NFC, 소문자, 공백·문장부호 제거)한 뒤 글자 단위 편집 거리 / 정답 글자 수.
  1시간 회의는 정답이 2만 자 안팎이라 표준 라이브러리의 비트 병렬 편집 거리(Myers·Hyyrö)로 잰다.
- 용어 적중률: 샘플의 용어마다 정답에 나온 횟수 중 인식 결과에도 나온 횟수(min)의 합 / 정답에 나온 횟수의 합.
  띄어쓰기 차이는 용어 오류로 보지 않으려고 CER과 같은 정규화를 한 문자열에서 센다.
- DER: pyannote.metrics(MIT, pyannote.audio 의존성)의 DiarizationErrorRate. collar 0.5는 경계 앞뒤 0.25초를 뺀다.
"""
import unicodedata
from typing import Dict, Iterable, List

DER_COLLAR = 0.5


def normalize(text: str) -> str:
    """글자(L)와 숫자(N)만 남긴다. 공백, 문장부호, 기호는 뺀다."""
    text = unicodedata.normalize("NFC", text).lower()
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in ("L", "N"))


def edit_distance(a: str, b: str) -> int:
    """레벤슈타인 거리. Hyyrö(2001)의 비트 병렬 알고리즘으로 짧은 쪽을 비트열(패턴)로 쓴다."""
    if len(a) < len(b):
        a, b = b, a
    m = len(b)
    if m == 0:
        return len(a)
    peq: Dict[str, int] = {}
    for i, ch in enumerate(b):
        peq[ch] = peq.get(ch, 0) | (1 << i)
    mask, high = (1 << m) - 1, 1 << (m - 1)
    pv, mv, score = mask, 0, m
    for ch in a:
        eq = peq.get(ch, 0)
        xv = eq | mv
        xh = (((eq & pv) + pv) ^ pv) | eq
        ph = (mv | ~(xh | pv)) & mask
        mh = pv & xh
        if ph & high:
            score += 1
        elif mh & high:
            score -= 1
        ph = ((ph << 1) | 1) & mask  # 첫 행이 0..n인 전역 편집 거리
        mh = (mh << 1) & mask
        pv = (mh | ~(xv | ph)) & mask
        mv = ph & xv
    return score


def cer(reference: str, hypothesis: str) -> Dict[str, float]:
    ref, hyp = normalize(reference), normalize(hypothesis)
    errors = edit_distance(ref, hyp)
    return {"cer": errors / len(ref) if ref else float(bool(hyp)), "errors": errors, "ref_chars": len(ref), "hyp_chars": len(hyp)}


def term_hits(reference: str, hypothesis: str, terms: Iterable[str]) -> Dict[str, object]:
    ref, hyp = normalize(reference), normalize(hypothesis)
    detail, expected, hits, extra = {}, 0, 0, 0
    for term in dict.fromkeys(terms):  # 순서를 지키며 중복 제거
        key = normalize(term)
        if not key:
            continue
        in_ref, in_hyp = ref.count(key), hyp.count(key)
        detail[term] = {"reference": in_ref, "hypothesis": in_hyp}
        expected, hits, extra = expected + in_ref, hits + min(in_ref, in_hyp), extra + max(0, in_hyp - in_ref)
    return {"rate": hits / expected if expected else None, "hits": hits, "expected": expected, "extra": extra, "terms": detail}


def der(reference: List[dict], hypothesis: List[dict], duration: float = None, collar: float = DER_COLLAR) -> Dict[str, float]:
    """reference·hypothesis: [{"speaker", "start", "end"}] (초). 화자 이름은 서로 대응시킨다(최적 매핑).
    duration(초)을 주면 음원 전체를 평가 구간(uem)으로 쓴다. 없으면 pyannote가 두 전사의 범위로 어림한다."""
    from pyannote.core import Annotation, Segment, Timeline
    from pyannote.metrics.diarization import DiarizationErrorRate

    def annotation(segments):
        out = Annotation()
        for seg in segments:
            if seg["end"] > seg["start"]:
                out[Segment(seg["start"], seg["end"]), len(out)] = str(seg["speaker"])
        return out

    ref, hyp = annotation(reference), annotation(hypothesis)
    end = max([duration or 0] + [s["end"] for s in reference + hypothesis])  # 끝 시각이 길이를 조금 넘는 전사도 받는다
    uem = Timeline([Segment(0, end)]) if end > 0 else None
    detail = DiarizationErrorRate(collar=collar, skip_overlap=False)(ref, hyp, uem=uem, detailed=True)
    return {
        "der": detail["diarization error rate"],
        "missed": detail["missed detection"],
        "false_alarm": detail["false alarm"],
        "confusion": detail["confusion"],
        "total": detail["total"],
        "ref_speakers": len({s["speaker"] for s in reference}),
        "hyp_speakers": len({s["speaker"] for s in hypothesis}),
    }
