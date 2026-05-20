import { useRef, useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Capacitor, registerPlugin } from '@capacitor/core';
import { VoiceRecorder } from 'capacitor-voice-recorder';
import { confirmDialog } from '../utils/dialog';
import '../styles/RecorderControls.css';

interface RecorderControlsProps {
  onRecordingComplete: (audioBlob: Blob) => void;
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
  ({ onRecordingComplete }, ref) => {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
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
        await VoiceRecorder.startRecording();
      } else {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = stream;

        const mediaRecorder = new MediaRecorder(stream);
        mediaRecorderRef.current = mediaRecorder;
        audioChunksRef.current = [];

        mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            audioChunksRef.current.push(event.data);
          }
        };

        mediaRecorder.start();
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

  const finalizeRecording = (blob: Blob | null) => {
    setIsRecording(false);
    setIsPaused(false);
    resetTimer();
    if (blob) onRecordingComplete(blob);
  };

  const stopRecording = async () => {
    if (!isRecording) return;
    stopTimer();

    try {
      if (isNative) {
        const result = await VoiceRecorder.stopRecording();
        if (platform === 'android') {
          await RecordingService.stop().catch(() => {});
        }
        const shouldUpload = await confirmDialog('녹취된 내용을 업로드 하시겠습니까?');
        if (shouldUpload && result.value?.recordDataBase64) {
          const blob = base64ToBlob(
            result.value.recordDataBase64,
            result.value.mimeType || 'audio/aac'
          );
          finalizeRecording(blob);
        } else {
          finalizeRecording(null);
        }
      } else if (mediaRecorderRef.current) {
        const recorder = mediaRecorderRef.current;
        recorder.onstop = async () => {
          const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
          const shouldUpload = await confirmDialog('녹취된 내용을 업로드 하시겠습니까?');
          finalizeRecording(shouldUpload ? audioBlob : null);
        };
        recorder.stop();
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((track) => track.stop());
          streamRef.current = null;
        }
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
        await VoiceRecorder.stopRecording().catch(() => {});
        if (platform === 'android') {
          await RecordingService.stop().catch(() => {});
        }
      } else if (mediaRecorderRef.current) {
        audioChunksRef.current = [];
        mediaRecorderRef.current.onstop = () => {};
        mediaRecorderRef.current.stop();
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((track) => track.stop());
          streamRef.current = null;
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

// base64 문자열을 Blob으로 변환 (네이티브 녹음 결과 업로드용)
function base64ToBlob(base64: string, mimeType: string): Blob {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: mimeType });
}

export default RecorderControls;
