"""백그라운드 STT 처리.

업로드 엔드포인트는 원본 파일만 저장하고 즉시 202로 응답한 뒤, 실제 변환(ffmpeg
정규화 + Clova STT)은 여기서 백그라운드로 수행한다. 진행 상태는 meetings.transcription_status
컬럼에 기록되어 프론트가 폴링으로 확인한다.

서버가 변환 도중 재시작돼도 source_audio_path가 DB에 남아 있어 기동 시 재처리할 수 있다.
"""

from datetime import datetime
from pathlib import Path

from pydub import AudioSegment

from clova_stt import convert as clova_convert
from services.meeting_service import (
    update_meeting_settings,
    update_transcript,
    add_audio_file,
    clear_audio_file,
    get_domain_keywords,
    set_transcription_status,
    get_pending_transcription,
)
from utils.config import RECORDS_DIR


def process_audio_transcription(meeting_id: str) -> None:
    """meetings.source_audio_path의 원본을 WAV로 변환 후 Clova STT를 수행하고,
    transcript/participants/audio_file/status를 갱신한다. 실패 시 status='failed'.

    동기 함수로 작성되어 있어 FastAPI BackgroundTasks(threadpool)에서 안전하게 실행된다.
    """
    source_path, domain_id = get_pending_transcription(meeting_id)

    if not source_path or not Path(source_path).exists():
        print(f"[transcription] source audio missing for {meeting_id}: {source_path}")
        set_transcription_status(meeting_id, "failed")
        return

    # source 파일명의 "_nokeep" 마커로 서버 사본 보관 여부를 판단한다(별도 컬럼 없이,
    # 서버 재시작 후 재처리(requeue_stuck_transcriptions)에서도 동일하게 동작).
    keep_server_copy = "_nokeep" not in Path(source_path).name

    try:
        # 1) WAV 정규화 (모바일은 aac/m4a로 녹음 → Clova는 WAV 기대)
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = int(datetime.now().timestamp() * 1000)
        wav_path = RECORDS_DIR / f"meeting_{meeting_id}_{timestamp}.wav"
        audio = AudioSegment.from_file(source_path)
        audio.export(str(wav_path), format="wav")

        # 2) 도메인 키워드 부스팅
        domain_keywords = get_domain_keywords(domain_id) if domain_id else None

        # 3) Clova STT (수 분 소요 가능)
        segments, speaker_names = clova_convert(
            file_path=str(wav_path),
            language="ko-KR",
            domain_keywords=domain_keywords,
        )

        # 4) 인식된 화자 수에 맞춰 참가자 갱신 후 transcript 저장
        if speaker_names:
            update_meeting_settings(meeting_id, speaker_names)
        transcript_data = [seg.model_dump() for seg in segments]
        update_transcript(meeting_id, transcript_data)
        add_audio_file(meeting_id, str(wav_path))

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

        print(f"[transcription] done for {meeting_id}")
    except Exception as e:
        print(f"[transcription] failed for {meeting_id}: {e}")
        # source_audio_path는 남겨둬 추후 재시도가 가능하도록 한다.
        set_transcription_status(meeting_id, "failed")


def requeue_stuck_transcriptions() -> None:
    """서버 기동 시, 'processing'으로 멈춘 미팅을 백그라운드 스레드에서 재처리한다.

    (BackgroundTasks는 요청 컨텍스트가 필요하므로 기동 시점에는 직접 스레드를 띄운다.)
    """
    import threading

    from services.meeting_service import list_stuck_transcription_ids

    stuck = list_stuck_transcription_ids()
    if not stuck:
        return

    print(f"[transcription] requeuing {len(stuck)} stuck transcription(s): {stuck}")

    def _worker():
        for meeting_id in stuck:
            process_audio_transcription(meeting_id)

    # 기동을 막지 않도록 단일 데몬 스레드에서 순차 처리
    threading.Thread(target=_worker, daemon=True).start()
