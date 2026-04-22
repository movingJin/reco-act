import { useRef, useState, forwardRef, useImperativeHandle } from 'react';
import '../styles/RecorderControls.css';

interface RecorderControlsProps {
  onRecordingComplete: (audioBlob: Blob) => void;
}

export interface RecorderControlsHandle {
  isRecording: boolean;
  stopRecordingWithoutUpload: () => void;
}

const RecorderControls = forwardRef<RecorderControlsHandle, RecorderControlsProps>(
  ({ onRecordingComplete }, ref) => {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const startRecording = async () => {
    try {
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
      setIsRecording(true);
      setIsPaused(false);
      setRecordingTime(0);

      timerRef.current = setInterval(() => {
        setRecordingTime((prev) => prev + 1);
      }, 1000);
    } catch (error) {
      console.error('Failed to start recording:', error);
      alert('마이크 접근 권한이 필요합니다');
    }
  };

  const pauseRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.pause();
      setIsPaused(true);
      if (timerRef.current) clearInterval(timerRef.current);
    }
  };

  const resumeRecording = () => {
    if (mediaRecorderRef.current && isPaused) {
      mediaRecorderRef.current.resume();
      setIsPaused(false);
      timerRef.current = setInterval(() => {
        setRecordingTime((prev) => prev + 1);
      }, 1000);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      if (timerRef.current) clearInterval(timerRef.current);

      mediaRecorderRef.current.onstop = () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
        
        // 업로드 여부를 묻는 확인 대화
        const shouldUpload = window.confirm('녹취된 내용을 업로드 하시겠습니까?');
        
        if (shouldUpload) {
          onRecordingComplete(audioBlob);
        }
        
        setIsRecording(false);
        setIsPaused(false);
        setRecordingTime(0);
      };

      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
      }
    }
  };

  const stopRecordingWithoutUpload = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      if (timerRef.current) clearInterval(timerRef.current);

      // 녹취 데이터를 버리고 업로드하지 않음
      audioChunksRef.current = [];

      mediaRecorderRef.current.onstop = () => {
        setIsRecording(false);
        setIsPaused(false);
        setRecordingTime(0);
      };

      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
      }
    }
  };

  useImperativeHandle(ref, () => ({
    isRecording,
    stopRecordingWithoutUpload,
  }));


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
