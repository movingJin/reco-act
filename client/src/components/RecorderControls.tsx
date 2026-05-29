import { useRef, useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Capacitor, registerPlugin } from '@capacitor/core';
import { VoiceRecorder } from 'capacitor-voice-recorder';
import { confirmDialog } from '../utils/dialog';
import {
  PendingRecording,
  NATIVE_DIRECTORY,
  NATIVE_MIME_TYPE,
  WEB_MIME_TYPE,
  WEB_TIMESLICE_MS,
  savePending,
  appendWebChunk,
  discardRecording,
} from '../utils/recordingStore';
import '../styles/RecorderControls.css';

interface RecorderControlsProps {
  meetingId: string;
  onRecordingComplete: (pending: PendingRecording) => void;
}

export interface RecorderControlsHandle {
  isRecording: boolean;
  stopRecordingWithoutUpload: () => void;
}

// Android foreground service bridge — JS에서 녹음 시작 시 마이크 service를 기동해
// 화면이 꺼지거나 앱이 백그라운드로 가도 프로세스가 죽지 않도록 한다.
interface RecordingServicePlugin {
  start: () => Promise<void>;
  stop: () => Promise<void>;
}
const RecordingService = registerPlugin<RecordingServicePlugin>('RecordingService');

const isNative = Capacitor.isNativePlatform();
const platform = Capacitor.getPlatform();

const RecorderControls = forwardRef<RecorderControlsHandle, RecorderControlsProps>(
  ({ meetingId, onRecordingComplete }, ref) => {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const timerRef = useRef<number | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  // 웹 녹음 청크를 IndexedDB에 묶는 세션 식별자 (시작 시 생성)
  const webSessionIdRef = useRef<string | null>(null);
  // wall-clock 기반 타이머: 화면이 꺼져 setInterval이 throttle돼도
  // 콜백이 실행되는 시점에 (Date.now() - startedAt) 으로 실제 경과시간을 계산한다.
  const segmentStartRef = useRef<number | null>(null);
  const accumulatedMsRef = useRef<number>(0);

  const computeElapsedSeconds = () => {
    const running = segmentStartRef.current != null ? Date.now() - segmentStartRef.current : 0;
    return Math.floor((accumulatedMsRef.current + running) / 1000);
  };

  const startTimer = () => {
    segmentStartRef.current = Date.now();
    timerRef.current = window.setInterval(() => {
      setRecordingTime(computeElapsedSeconds());
    }, 1000);
  };

  const stopTimer = () => {
    if (segmentStartRef.current != null) {
      accumulatedMsRef.current += Date.now() - segmentStartRef.current;
      segmentStartRef.current = null;
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setRecordingTime(Math.floor(accumulatedMsRef.current / 1000));
  };

  const resetTimer = () => {
    accumulatedMsRef.current = 0;
    segmentStartRef.current = null;
    setRecordingTime(0);
  };

  const startRecording = async () => {
    try {
      if (isNative) {
        const perm = await VoiceRecorder.requestAudioRecordingPermission();
        if (!perm.value) {
          alert('마이크 접근 권한이 필요합니다');
          return;
        }
        if (platform === 'android') {
          await RecordingService.start();
        }
        // directory 옵션 → 녹음을 Directory.Data의 파일에 실시간 기록한다.
        // 정지 시 base64 변환을 하지 않으므로 긴 녹음에서도 OOM이 없고, 파일이 폰에 남는다.
        await VoiceRecorder.startRecording({ directory: NATIVE_DIRECTORY });
        // 경로는 정지 시점에야 확정되지만, 녹음 도중 크래시해도 복구할 수 있도록
        // meetingId만이라도 먼저 기록해 둔다(복구 시 디렉토리의 파일과 매칭).
        await savePending({
          kind: 'native',
          meetingId,
          mimeType: NATIVE_MIME_TYPE,
          savedAt: Date.now(),
        });
      } else {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = stream;

        const sessionId = `web-${Date.now()}`;
        webSessionIdRef.current = sessionId;
        // 청크가 들어올 때마다 IndexedDB로 flush → 탭/브라우저가 죽어도 복구 가능
        await savePending({
          kind: 'web',
          meetingId,
          mimeType: WEB_MIME_TYPE,
          savedAt: Date.now(),
          sessionId,
        });

        const mediaRecorder = new MediaRecorder(stream);
        mediaRecorderRef.current = mediaRecorder;

        mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            // append 실패해도 녹음은 계속 진행 (다음 청크에서 회복)
            appendWebChunk(sessionId, event.data).catch((e) =>
              console.error('Failed to persist chunk:', e)
            );
          }
        };

        mediaRecorder.start(WEB_TIMESLICE_MS);
      }

      setIsRecording(true);
      setIsPaused(false);
      resetTimer();
      startTimer();
    } catch (error) {
      console.error('Failed to start recording:', error);
      alert('마이크 접근 권한이 필요합니다');
    }
  };

  const pauseRecording = async () => {
    if (!isRecording) return;
    try {
      if (isNative) {
        await VoiceRecorder.pauseRecording();
      } else if (mediaRecorderRef.current) {
        mediaRecorderRef.current.pause();
      }
      setIsPaused(true);
      stopTimer();
    } catch (error) {
      console.error('Failed to pause recording:', error);
    }
  };

  const resumeRecording = async () => {
    if (!isPaused) return;
    try {
      if (isNative) {
        await VoiceRecorder.resumeRecording();
      } else if (mediaRecorderRef.current) {
        mediaRecorderRef.current.resume();
      }
      setIsPaused(false);
      startTimer();
    } catch (error) {
      console.error('Failed to resume recording:', error);
    }
  };

  const finalizeRecording = (pending: PendingRecording | null) => {
    setIsRecording(false);
    setIsPaused(false);
    resetTimer();
    if (pending) onRecordingComplete(pending);
  };

  // 정지 후 업로드 여부를 묻고, 저장된 녹음을 업로드(pending 전달) 또는 폐기한다.
  const finishWithPending = async (pending: PendingRecording) => {
    const shouldUpload = await confirmDialog('녹취된 내용을 업로드 하시겠습니까?');
    if (shouldUpload) {
      finalizeRecording(pending);
    } else {
      await discardRecording(pending);
      finalizeRecording(null);
    }
  };

  const stopRecording = async () => {
    if (!isRecording) return;
    stopTimer();
    const durationMs = accumulatedMsRef.current;

    try {
      if (isNative) {
        const result = await VoiceRecorder.stopRecording();
        if (platform === 'android') {
          await RecordingService.stop().catch(() => {});
        }
        const path = result.value?.path;
        if (!path) {
          // directory 옵션을 줬으므로 path가 와야 한다. 없으면 복구 불가로 간주.
          console.error('Native recording returned no path', result);
          alert('녹취 파일을 저장하지 못했습니다');
          finalizeRecording(null);
          return;
        }
        const pending: PendingRecording = {
          kind: 'native',
          meetingId,
          mimeType: result.value?.mimeType || NATIVE_MIME_TYPE,
          savedAt: Date.now(),
          durationMs: result.value?.msDuration ?? durationMs,
          path,
        };
        await savePending(pending);
        await finishWithPending(pending);
      } else if (mediaRecorderRef.current) {
        const recorder = mediaRecorderRef.current;
        const sessionId = webSessionIdRef.current;
        recorder.onstop = async () => {
          if (streamRef.current) {
            streamRef.current.getTracks().forEach((track) => track.stop());
            streamRef.current = null;
          }
          if (!sessionId) {
            finalizeRecording(null);
            return;
          }
          const pending: PendingRecording = {
            kind: 'web',
            meetingId,
            mimeType: WEB_MIME_TYPE,
            savedAt: Date.now(),
            durationMs,
            sessionId,
          };
          await savePending(pending);
          await finishWithPending(pending);
        };
        recorder.stop();
      }
    } catch (error) {
      console.error('Failed to stop recording:', error);
      finalizeRecording(null);
    }
  };

  const stopRecordingWithoutUpload = async () => {
    if (!isRecording) return;
    stopTimer();

    try {
      if (isNative) {
        const result = await VoiceRecorder.stopRecording().catch(() => null);
        if (platform === 'android') {
          await RecordingService.stop().catch(() => {});
        }
        // 업로드하지 않고 버리는 경로 → 저장된 파일과 pending 메타데이터를 정리
        await discardRecording({
          kind: 'native',
          meetingId,
          mimeType: NATIVE_MIME_TYPE,
          savedAt: 0,
          path: result?.value?.path,
        });
      } else if (mediaRecorderRef.current) {
        mediaRecorderRef.current.onstop = () => {};
        mediaRecorderRef.current.stop();
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((track) => track.stop());
          streamRef.current = null;
        }
        if (webSessionIdRef.current) {
          await discardRecording({
            kind: 'web',
            meetingId,
            mimeType: WEB_MIME_TYPE,
            savedAt: 0,
            sessionId: webSessionIdRef.current,
          });
          webSessionIdRef.current = null;
        }
      }
    } finally {
      setIsRecording(false);
      setIsPaused(false);
      setRecordingTime(0);
    }
  };

  useImperativeHandle(ref, () => ({
    isRecording,
    stopRecordingWithoutUpload,
  }));

  // 전원 버튼으로 화면을 끄면 Android WebView가 setInterval을 throttle/정지시킨다.
  // 화면이 다시 켜졌을 때 wall-clock 기반으로 표시 시간을 즉시 보정한다.
  useEffect(() => {
    if (!isRecording || isPaused) return;
    const sync = () => setRecordingTime(computeElapsedSeconds());
    document.addEventListener('visibilitychange', sync);
    window.addEventListener('focus', sync);
    window.addEventListener('pageshow', sync);
    return () => {
      document.removeEventListener('visibilitychange', sync);
      window.removeEventListener('focus', sync);
      window.removeEventListener('pageshow', sync);
    };
  }, [isRecording, isPaused]);

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  return (
    <div className="recorder-controls">
      <div className="recording-timer">{formatTime(recordingTime)}</div>

      <div className="controls-buttons">
        {!isRecording ? (
          <button className="btn btn-primary" onClick={startRecording}>
            ▶️ 녹음 시작
          </button>
        ) : (
          <>
            {!isPaused ? (
              <button className="btn btn-warning" onClick={pauseRecording}>
                ⏸️ 일시정지
              </button>
            ) : (
              <button className="btn btn-success" onClick={resumeRecording}>
                ▶️ 재개
              </button>
            )}
            <button className="btn btn-danger" onClick={stopRecording}>
              ⏹️ 중지
            </button>
          </>
        )}
      </div>

      {isRecording && (
        <div className="recording-indicator">
          <span className="pulse">●</span> 녹음 중...
        </div>
      )}
    </div>
  );
  }
);

RecorderControls.displayName = 'RecorderControls';

export default RecorderControls;
