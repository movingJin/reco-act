"""STT 처리.

업로드 엔드포인트는 원본 파일만 저장하고 transcribe 작업을 넣은 뒤 즉시 202로 응답한다. 실제 변환은
워커(python -m worker)가 transcribe_meeting으로 수행한다.
- public: pydub WAV 변환 → STT(Clova, 화자 포함) → 저장
- airgap: ffmpeg mono 16kHz 정규화 → STT(단어 타임스탬프) → 화자분리 → 병합 → 저장 (01-plan 3.3)
진행 상태는 meetings.transcription_status 컬럼에 기록되어 프론트가 폴링으로 확인한다.

워커가 변환 도중 죽어도 작업 기록과 source_audio_path가 DB에 남아 있어 다시 처리할 수 있다.
"""

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from pydub import AudioSegment

from services.meeting_service import (
    update_meeting_settings,
    update_transcript,
    add_audio_file,
    clear_audio_file,
    get_domain_keywords,
    set_transcription_status,
    get_pending_transcription,
)
from services.job_service import enqueue_job
from diarization.factory import get_diarizer
from pipeline.audio import normalize_audio
from pipeline.merge import merge_words_and_turns
from stt.base import SttHints
from stt.factory import get_stt_provider
from utils import config
from utils.config import RECORDS_DIR


class SourceAudioMissing(Exception):
    """처리할 원본 오디오가 DB에 없거나 디스크에 없다. 다시 시도해도 같다."""


def transcribe_meeting(meeting_id: str, on_stage: Optional[Callable[[str], None]] = None) -> int:
    """meetings.source_audio_path의 원본을 WAV로 변환 후 STT를 수행하고,
    transcript/participants/audio_file/status를 갱신하고 저장한 세그먼트 수를 돌려준다. 실패하면 예외를 올린다(상태는 건드리지 않음).

    워커가 transcribe 작업으로 호출한다. 실패 시 상태 처리(재시도, failed)는 워커가 맡는다.
    on_stage는 단계(normalize, stt, [diarize, merge], save)가 시작될 때 불린다. 워커가 작업 기록에 남긴다.
    diarize와 merge는 STT 엔진이 화자를 주지 않을 때(airgap)만 거친다.
    """
    stage = on_stage or (lambda name: None)
    source_path, domain_id = get_pending_transcription(meeting_id)

    if not source_path or not Path(source_path).exists():
        raise SourceAudioMissing(f"source audio missing for {meeting_id}: {source_path}")

    # source 파일명의 "_nokeep" 마커로 서버 사본 보관 여부를 판단한다(별도 컬럼 없이,
    # 워커가 작업을 다시 처리할 때도 동일하게 동작).
    keep_server_copy = "_nokeep" not in Path(source_path).name

    # 1) WAV 정규화
    stage("normalize")
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = int(datetime.now().timestamp() * 1000)
    wav_path = RECORDS_DIR / f"meeting_{meeting_id}_{timestamp}.wav"
    if config.APP_PROFILE == "airgap":
        # STT·화자분리 입력과 재생용으로 함께 쓰는 mono 16kHz WAV. 디코딩할 수 없으면 재시도 없이 실패한다.
        duration_ms = normalize_audio(source_path, str(wav_path))
    else:
        # 모바일은 aac/m4a로 녹음 → Clova는 WAV 기대. 샘플레이트·채널은 원본 그대로 둔다.
        audio = AudioSegment.from_file(source_path)
        audio.export(str(wav_path), format="wav")
        duration_ms = len(audio)

    # 2) 도메인 키워드 부스팅
    domain_keywords = get_domain_keywords(domain_id) if domain_id else None

    # 3) STT (수 분 소요 가능). 엔진은 STT_PROVIDER로 고른다(public은 Clova).
    stage("stt")
    result = get_stt_provider().transcribe(
        str(wav_path),
        language="ko-KR",
        hints=SttHints(keywords=domain_keywords or []),
    )
    if result.segments is not None:
        # 화자까지 주는 엔진(Clova)
        segments, speaker_names = result.segments, result.speaker_names
    else:
        # 단어만 주는 엔진은 화자분리 구간과 합쳐 세그먼트를 만든다(01-plan 3.3).
        diarizer = get_diarizer()
        if diarizer is None:
            raise RuntimeError(f"STT provider {result.engine} returned no speaker segments and DIARIZER is not set")
        stage("diarize")
        turns = diarizer.diarize(str(wav_path))
        stage("merge")
        segments, speaker_names = merge_words_and_turns(result.words, turns)

    # 4) 인식된 화자 수에 맞춰 참가자 갱신 후 transcript 저장
    stage("save")
    if speaker_names:
        update_meeting_settings(meeting_id, speaker_names)
    transcript_data = [seg.model_dump() for seg in segments]
    update_transcript(meeting_id, transcript_data)
    add_audio_file(meeting_id, str(wav_path), duration_ms=duration_ms)

    # 5) 완료 처리 + 원본 정리
    set_transcription_status(meeting_id, "done", clear_source=True)
    try:
        Path(source_path).unlink(missing_ok=True)
    except Exception as e:
        print(f"[transcription] failed to remove source {source_path}: {e}")

    # 6) 클라이언트(Android)가 원본을 자체 보관하는 경우, 서버 사본은 즉시 제거한다.
    if not keep_server_copy:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except Exception as e:
            print(f"[transcription] failed to remove wav {wav_path}: {e}")
        clear_audio_file(meeting_id)

    return len(segments)


def enqueue_orphaned_transcriptions() -> list[str]:
    """processing인데 대기·실행 중인 전사 작업이 없는 회의에 전사 작업을 넣는다. 넣은 회의 id를 돌려준다.

    워커가 기동할 때 실행한다. 업로드가 상태만 기록하고 작업을 넣기 전에 실패한 경우와,
    BackgroundTasks로 처리하던 시절(PR-5b 이전)에 실행 중이던 전사가 배포로 끊긴 경우를 되살린다.
    같은 회의의 대기 작업은 하나만 생기므로(enqueue_job) 업로드와 겹쳐도 중복되지 않는다.
    """
    from services.job_service import find_active_job
    from services.meeting_service import list_stuck_transcription_ids

    orphans = [m for m in list_stuck_transcription_ids() if find_active_job(m, "transcribe") is None]
    for meeting_id in orphans:
        enqueue_job(meeting_id, "transcribe")
    return orphans
