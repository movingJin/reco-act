"""백그라운드 STT 처리.

업로드 엔드포인트는 원본 파일만 저장하고 즉시 202로 응답한 뒤, 실제 변환(ffmpeg
정규화 + Clova STT)은 여기서 백그라운드로 수행한다. 진행 상태는 meetings.transcription_status
컬럼에 기록되어 프론트가 폴링으로 확인한다.

Clova STT 호출 방식:
- PUBLIC_API_BASE_URL이 설정된 환경(운영)에서는 비동기(콜백) 방식을 사용한다. 제출
  자체는 수 초 내 끝나고, 실제 인식 결과는 Clova가 완료 후 웹훅으로 넘겨준다
  (handle_clova_callback). sync 방식은 처리 완료까지 하나의 HTTP 커넥션을 계속
  붙들고 있어야 해서 1시간 이상 녹음처럼 처리 시간이 긴 경우 불안정했다.
- 공개 콜백 URL이 없는 로컬 개발 환경에서는 기존 sync 방식으로 즉시 처리한다.

서버가 변환 도중 재시작돼도 source_audio_path가 DB에 남아 있어 기동 시 재처리할 수 있다.
이미 Clova에 제출까지 끝낸 작업(콜백 대기 중)이었다면 재제출 대신 상태를 한 번
조회해 콜백 유실 여부를 복구한다(requeue_stuck_transcriptions).
"""

import time
from datetime import datetime
from pathlib import Path
from typing import List

from pydub import AudioSegment

from clova_stt import (
    convert as clova_convert_sync,
    fetch_job_result as clova_fetch_job_result,
    parse_result as clova_parse_result,
    submit_async as clova_submit_async,
)
from models.meeting import TranscriptSegment
from services.meeting_service import (
    update_meeting_settings,
    update_transcript,
    add_audio_file,
    clear_audio_file,
    get_domain_keywords,
    set_transcription_status,
    get_pending_transcription,
    set_pending_clova_job,
    get_pending_clova_job,
    clear_pending_clova_job,
    list_stuck_transcription_ids,
)
from utils.config import RECORDS_DIR, PUBLIC_API_BASE_URL


def process_audio_transcription(meeting_id: str) -> None:
    """meetings.source_audio_path의 원본을 WAV로 변환 후 Clova STT를 수행한다.

    동기 함수로 작성되어 있어 FastAPI BackgroundTasks(threadpool)에서 안전하게 실행된다.
    운영 환경(PUBLIC_API_BASE_URL 설정됨)에서는 Clova에 제출만 하고 즉시 반환하며,
    transcript 반영/완료 처리는 handle_clova_callback에서 이어서 수행한다.
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

        if PUBLIC_API_BASE_URL:
            # 3-a) Clova 비동기 제출 (제출 자체는 수 초 내 끝남)
            callback_url = f"{PUBLIC_API_BASE_URL}/api/webhooks/clova/{meeting_id}"
            token = clova_submit_async(
                file_path=str(wav_path),
                callback_url=callback_url,
                language="ko-KR",
                domain_keywords=domain_keywords,
            )
            set_pending_clova_job(meeting_id, str(wav_path), token)
            print(f"[transcription] submitted async job for {meeting_id} (token={token})")
            return

        # 3-b) 로컬 개발 등 공개 콜백 URL이 없는 환경: 기존 sync 방식
        segments, speaker_names = clova_convert_sync(
            file_path=str(wav_path),
            language="ko-KR",
            domain_keywords=domain_keywords,
        )
        _finish_transcription(meeting_id, segments, speaker_names, str(wav_path), source_path, keep_server_copy)

    except Exception as e:
        print(f"[transcription] failed for {meeting_id}: {e}")
        # source_audio_path는 남겨둬 추후 재시도가 가능하도록 한다.
        set_transcription_status(meeting_id, "failed")


def handle_clova_callback(meeting_id: str, payload: dict) -> None:
    """Clova 비동기 인식 완료 후 도착하는 콜백을 처리한다.

    인증 없는 공개 엔드포인트로 노출되므로, 제출 시 저장해둔 token과 대조해
    실제로 우리가 요청한 작업의 결과인지 확인한다(불일치/모르는 미팅이면 무시).

    콜백 본문 자체에는 실제 인식 결과(segments/speakers)가 실려오지 않고
    {"token", "result": "SUCCEEDED", "message": "Succeeded"} 형태의 완료 알림만
    온다(운영 로그로 확인). 그래서 token으로 GET /recognizer/{token}을 다시 조회해
    진짜 결과를 받아온다. 콜백 도착 직후 아주 짧게 결과가 아직 준비 안 됐을 수 있어
    잠깐 재시도한다.
    """
    wav_path, expected_token, source_path = get_pending_clova_job(meeting_id)

    if not wav_path or not source_path:
        print(f"[transcription] callback for {meeting_id} but no pending job found, ignoring")
        return

    incoming_token = payload.get("token")
    if expected_token and incoming_token and incoming_token != expected_token:
        print(f"[transcription] callback token mismatch for {meeting_id}, ignoring (stale job?)")
        return

    token = incoming_token or expected_token
    keep_server_copy = "_nokeep" not in Path(source_path).name

    if payload.get("result") not in ("SUCCEEDED", "COMPLETED"):
        print(f"[transcription] callback reported failure for {meeting_id}: {payload}")
        set_transcription_status(meeting_id, "failed")
        clear_pending_clova_job(meeting_id)
        return

    result = None
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            candidate = clova_fetch_job_result(token)
        except Exception as e:
            last_error = e
            time.sleep(3)
            continue
        if candidate.get("result") == "COMPLETED":
            result = candidate
            break
        last_error = RuntimeError(f"unexpected status on fetch: {candidate.get('result')}")
        time.sleep(3)

    if result is None:
        print(f"[transcription] failed to fetch completed result for {meeting_id} after callback: {last_error}")
        set_transcription_status(meeting_id, "failed")
        clear_pending_clova_job(meeting_id)
        return

    try:
        segments, speaker_names = clova_parse_result(result)
        _finish_transcription(meeting_id, segments, speaker_names, wav_path, source_path, keep_server_copy)
    except Exception as e:
        print(f"[transcription] failed to process callback for {meeting_id}: {e}")
        set_transcription_status(meeting_id, "failed")
        clear_pending_clova_job(meeting_id)


def _finish_transcription(
    meeting_id: str,
    segments: List[TranscriptSegment],
    speaker_names: List[str],
    wav_path: str,
    source_path: str,
    keep_server_copy: bool,
) -> None:
    """STT 결과를 transcript/participants/audio_file에 반영하고 완료 처리한다.

    sync 경로(process_audio_transcription)와 콜백 경로(handle_clova_callback)가 공유한다.
    """
    if speaker_names:
        update_meeting_settings(meeting_id, speaker_names)
    transcript_data = [seg.model_dump() for seg in segments]
    update_transcript(meeting_id, transcript_data)
    add_audio_file(meeting_id, wav_path)

    set_transcription_status(meeting_id, "done", clear_source=True)
    clear_pending_clova_job(meeting_id)
    try:
        Path(source_path).unlink(missing_ok=True)
    except Exception as e:
        print(f"[transcription] failed to remove source {source_path}: {e}")

    # 클라이언트(Android)가 원본을 자체 보관하는 경우, 서버 사본은 즉시 제거한다.
    if not keep_server_copy:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except Exception as e:
            print(f"[transcription] failed to remove wav {wav_path}: {e}")
        clear_audio_file(meeting_id)

    print(f"[transcription] done for {meeting_id}")


def requeue_stuck_transcriptions() -> None:
    """서버 기동 시, 'processing'으로 멈춘 미팅을 백그라운드 스레드에서 복구한다.

    - 이미 Clova 제출까지 끝난 작업(pending_wav_path/clova_token 있음): 재제출하면
      비용이 중복되므로, 대신 상태를 한 번 조회해 그 사이 완료돼 있었다면(콜백 유실)
      바로 마무리하고, 아직 처리 중이면 그대로 콜백을 계속 기다린다.
    - 아직 제출 전에 멈춘 작업(원본 파일만 있음): 처음부터 다시 처리한다.

    (BackgroundTasks는 요청 컨텍스트가 필요하므로 기동 시점에는 직접 스레드를 띄운다.)
    """
    import threading

    stuck = list_stuck_transcription_ids()
    if not stuck:
        return

    print(f"[transcription] requeuing {len(stuck)} stuck transcription(s): {stuck}")

    def _recover_pending_job(meeting_id: str, wav_path: str, token: str, source_path: str) -> None:
        print(f"[transcription] {meeting_id} already submitted (token={token}); checking status")
        try:
            result = clova_fetch_job_result(token)
        except Exception as e:
            print(f"[transcription] failed to check status for {meeting_id}: {e}")
            return

        status = result.get("result")
        if status == "COMPLETED":
            keep_server_copy = "_nokeep" not in Path(source_path).name
            try:
                segments, speaker_names = clova_parse_result(result)
                _finish_transcription(meeting_id, segments, speaker_names, wav_path, source_path, keep_server_copy)
            except Exception as e:
                print(f"[transcription] failed to finish {meeting_id} after recovery: {e}")
                set_transcription_status(meeting_id, "failed")
                clear_pending_clova_job(meeting_id)
        elif status in ("FAILED", "TIMEOUT"):
            print(f"[transcription] {meeting_id} job {status} while server was down")
            set_transcription_status(meeting_id, "failed")
            clear_pending_clova_job(meeting_id)
        else:
            print(f"[transcription] {meeting_id} still {status}; will keep waiting for callback")

    def _worker():
        for meeting_id in stuck:
            wav_path, token, source_path = get_pending_clova_job(meeting_id)
            if wav_path and token:
                _recover_pending_job(meeting_id, wav_path, token, source_path)
            else:
                process_audio_transcription(meeting_id)

    # 기동을 막지 않도록 단일 데몬 스레드에서 순차 처리
    threading.Thread(target=_worker, daemon=True).start()
