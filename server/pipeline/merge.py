"""STT 단어 타임스탬프와 화자분리 구간을 합쳐 화자별 세그먼트를 만든다 (01-plan 3.3). 순수 함수다.

1. 단어마다 중앙 시점이 들어가는 구간의 화자를 붙인다. 들어가는 구간이 없으면 가장 가까운 구간을 쓴다.
2. 같은 화자의 연속 단어를 묶는다. 단어 사이 침묵이 max_gap_ms를 넘거나 세그먼트가 max_segment_ms보다 길어지면 나눈다.
3. 화자 라벨을 처음 말한 순서대로 speaker_index 0..N-1로 바꾸고, 이름은 화자1..N으로 둔다(회의 생성 때 기본 참여자와 같은 형태).
   구간은 있어도 단어가 하나도 붙지 않은 화자는 빠진다.

출력은 Clova 경로와 같은 (TranscriptSegment 리스트, 화자 이름 리스트)라서 저장 경로를 그대로 쓴다.
"""
from bisect import bisect_right
from typing import Optional, Sequence

from diarization.base import Turn
from models.meeting import TranscriptSegment
from stt.base import Word

MAX_GAP_MS = 2000
MAX_SEGMENT_MS = 30000


class _TurnIndex:
    """시작 시각으로 정렬한 구간에서 시점에 맞는 구간을 찾는다. 단어마다 전체 구간을 훑지 않으려는 것이다(2시간 회의면 구간이 수천 개)."""

    def __init__(self, turns: Sequence[Turn]) -> None:
        self.turns = sorted(turns, key=lambda t: (t.start_ms, t.end_ms))
        self.starts = [t.start_ms for t in self.turns]
        # reach[i]: turns[0..i] 중 가장 늦게 끝나는 구간의 위치. 앞쪽 구간이 시점을 덮는지 이것으로 끊어 본다.
        self.reach: list[int] = []
        for i, turn in enumerate(self.turns):
            if not self.reach or turn.end_ms > self.turns[self.reach[-1]].end_ms:
                self.reach.append(i)
            else:
                self.reach.append(self.reach[-1])

    def speaker_at(self, word: Word) -> str:
        mid = (word.start_ms + word.end_ms) / 2
        i = bisect_right(self.starts, mid)  # turns[:i]는 mid 이전에 시작했다

        containing = []
        j = i - 1
        while j >= 0 and self.turns[self.reach[j]].end_ms >= mid:
            if self.turns[j].end_ms >= mid:
                containing.append(self.turns[j])
            j -= 1
        if containing:
            # 겹치는 구간(exclusive가 아닌 입력)이면 단어와 더 많이 겹치는 구간, 같으면 먼저 시작한 구간
            return max(containing, key=lambda t: (_overlap(word, t), -t.start_ms)).speaker

        # 들어가는 구간이 없다. 앞에서 가장 늦게 끝난 구간과 뒤에서 가장 먼저 시작하는 구간 중 가까운 쪽(같으면 앞)
        before = self.turns[self.reach[i - 1]] if i > 0 else None
        after = self.turns[i] if i < len(self.turns) else None
        if after is None or (before is not None and mid - before.end_ms <= after.start_ms - mid):
            return before.speaker
        return after.speaker


def _overlap(word: Word, turn: Turn) -> int:
    return max(0, min(word.end_ms, turn.end_ms) - max(word.start_ms, turn.start_ms))


def merge_words_and_turns(
    words: Sequence[Word],
    turns: Sequence[Turn],
    *,
    max_gap_ms: int = MAX_GAP_MS,
    max_segment_ms: int = MAX_SEGMENT_MS,
) -> tuple[list[TranscriptSegment], list[str]]:
    """단어와 화자 구간을 합쳐 (세그먼트, 화자 이름)을 돌려준다. 단어가 없으면 ([], []).

    단어 text는 앞뒤 공백을 떼고 공백 하나로 잇는다(엔진이 붙인 앞 공백은 무시). 빈 단어는 버린다.
    화자분리가 구간을 하나도 내지 않으면 모든 단어를 한 화자로 둔다(인식된 말을 버리지 않는다).
    """
    index = _TurnIndex(turns) if turns else None
    labels: dict[Optional[str], int] = {}
    # 각 원소: [speaker_index, start_ms, end_ms, 단어 text 목록]
    groups: list[list] = []

    for word in sorted(words, key=lambda w: (w.start_ms, w.end_ms)):
        text = word.text.strip()
        if not text:
            continue
        label = index.speaker_at(word) if index else None
        speaker_index = labels.setdefault(label, len(labels))
        current = groups[-1] if groups else None
        if (
            current is not None
            and current[0] == speaker_index
            and word.start_ms - current[2] <= max_gap_ms
            and word.end_ms - current[1] <= max_segment_ms
        ):
            current[2] = max(current[2], word.end_ms)
            current[3].append(text)
        else:
            groups.append([speaker_index, word.start_ms, word.end_ms, [text]])

    segments = [
        TranscriptSegment(speaker_index=speaker_index, text=" ".join(texts), start=start, end=end)
        for speaker_index, start, end, texts in groups
    ]
    return segments, [f"화자{n}" for n in range(1, len(labels) + 1)]
