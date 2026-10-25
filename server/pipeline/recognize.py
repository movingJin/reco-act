"""정규화한 WAV에서 화자 세그먼트까지: STT → (화자분리 → 병합 → 용어 사전 치환 → [LLM 보정]) (01-plan 3.2~3.4).

DB를 읽지 않는다. 워커의 전사(services/transcription_service.py)와 평가 도구(eval/run_eval.py)가
같은 흐름을 쓰게 하려고 따로 두었다(PR-18). 키워드·사전은 부르는 쪽이 넘긴다.
"""
from dataclasses import asdict
from typing import Callable, List, Optional, Tuple

from diarization.factory import get_diarizer
from pipeline.correction import correct_segments
from pipeline.glossary import apply_term_corrections
from pipeline.merge import merge_words_and_turns
from stt.base import SttHints
from stt.factory import get_stt_provider
from utils import config


def recognize_audio(
    wav_path: str,
    *,
    language: str,
    keywords: List[str],
    corrections: Callable[[], List[Tuple[str, str]]],
    stage: Callable[[str], None],
) -> Tuple[list, Optional[List[str]], Optional[dict]]:
    """(세그먼트, 화자 이름, stt_results에 남길 값 또는 None)을 돌려준다.

    keywords: 도메인 키워드. STT 엔진 힌트(Clova boostings, faster-whisper hotwords)와 LLM 보정의 용어집
    corrections: 사전 치환 (오인식 표현, 정식 표기) 목록을 돌려주는 함수. 화자를 주지 않는 엔진일 때만 부른다
    stage: 단계(stt, [diarize, merge, glossary, correct])가 시작될 때 불린다
    """
    # STT (수 분 소요 가능). 엔진은 STT_PROVIDER로 고른다(public은 Clova).
    stage("stt")
    result = get_stt_provider().transcribe(wav_path, language=language, hints=SttHints(keywords=keywords))
    if result.segments is not None:
        # 화자까지 주는 엔진(Clova). 용어 보정과 원문 보존은 하지 않는다(public 현행).
        return result.segments, result.speaker_names, None

    # 단어만 주는 엔진은 화자분리 구간과 합쳐 세그먼트를 만든다(01-plan 3.3).
    diarizer = get_diarizer()
    if diarizer is None:
        raise RuntimeError(f"STT provider {result.engine} returned no speaker segments and DIARIZER is not set")
    stage("diarize")
    turns = diarizer.diarize(wav_path)
    stage("merge")
    raw_segments, speaker_names = merge_words_and_turns(result.words, turns)

    # 용어 보정(01-plan 3.4): 사전 치환, 켜져 있으면 LLM 보정
    stage("glossary")
    pairs = corrections()
    segments = apply_term_corrections(raw_segments, pairs)
    if config.TERM_CORRECTION_LLM:
        stage("correct")
        segments = correct_segments(segments, keywords + [correct for _, correct in pairs])

    record = {
        "engine": result.engine,
        "engine_version": result.engine_version or None,
        "words": [asdict(word) for word in result.words],
        "diarization": [asdict(turn) for turn in turns],
        "raw_segments": [seg.model_dump() for seg in raw_segments],
        "corrected_segments": [seg.model_dump() for seg in segments],
        "engine_raw": result.raw or None,
    }
    return segments, speaker_names, record
