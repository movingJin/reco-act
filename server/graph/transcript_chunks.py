"""요약 map-reduce용 전사 압축·분할 (airgap, 01-plan 3.7). 순수 함수다.

- 세그먼트를 "[start-end] 화자: 텍스트" 한 줄로 만든다. 문단의 start/end를 정하려면 시간이 필요해서 ms 값을 그대로 둔다.
  public(single)이 넣는 Python repr보다 짧다(합성 회의 기준 Qwen3 토큰 586 → 423).
- 세그먼트 경계를 따라 max_chars 안에서 청크를 나눈다. 토큰은 글자 수로 보수적으로 잡는다.
  tiktoken은 첫 사용 때 BPE 파일을 인터넷에서 받으므로 쓰지 않는다(01-plan 3.8).
"""
from typing import Sequence

from models.meeting import TranscriptSegmentResponse


def format_segment(segment: TranscriptSegmentResponse) -> str:
    return f"[{segment.start}-{segment.end}] {segment.speaker_name}: {segment.text}"


def chunk_segments(
    segments: Sequence[TranscriptSegmentResponse], max_chars: int
) -> list[list[TranscriptSegmentResponse]]:
    """압축한 줄(줄바꿈 포함)의 글자 수 합이 max_chars를 넘지 않게 세그먼트를 순서대로 묶는다.

    세그먼트 하나는 나누지 않는다. 혼자서 max_chars를 넘는 세그먼트는 혼자 한 청크가 된다. 빈 세그먼트는 뺀다.
    """
    chunks, current, size = [], [], 0
    for segment in segments:
        if not segment.text.strip():
            continue
        cost = len(format_segment(segment)) + 1
        if current and size + cost > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(segment)
        size += cost
    if current:
        chunks.append(current)
    return chunks
