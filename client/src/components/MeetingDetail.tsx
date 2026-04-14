import { useState, useEffect } from 'react';
import axios from 'axios';
import RecorderControls from './RecorderControls';
import TranscriptEditor from './TranscriptEditor';
import MeetingSettings from './MeetingSettings';
import SummaryPanel from './SummaryPanel';
import '../styles/MeetingDetail.css';

interface TranscriptSegment {
  speaker_index: number;
  speaker_name: string;
  text: string;
  start: number;
  end: number;
}

interface Meeting {
  id: string;
  title: string;
  created_at: string;
  participants: string[];
  transcript: TranscriptSegment[];
  audio_files: string[];
}

interface MeetingDetailProps {
  meeting: Meeting;
  onUpdate: () => void;
}

function MeetingDetail({ meeting, onUpdate }: MeetingDetailProps) {
  const [transcript, setTranscript] = useState<TranscriptSegment[]>(meeting.transcript || []);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isUploading, setIsUploading] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);

  useEffect(() => {
    // 외부에서 meeting이 업데이트되었을 때만 transcript 동기화
    // (예: 저장 후 서버 새로고침)
    setTranscript(meeting.transcript || []);
  }, [meeting.id]);

  const handleDownloadAudio = async () => {
    try {
      const response = await axios.get(
        `/api/meetings/${meeting.id}/download-audio`,
        { responseType: 'blob' }
      );
      
      // Create a blob URL and trigger download
      const url = window.URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = `meeting-${meeting.id}.wav`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Failed to download audio:', error);
      alert('녹취 파일 다운로드에 실패했습니다');
    }
  };

  const handleTranscriptUpdate = (updatedTranscript: TranscriptSegment[]) => {
    setTranscript(updatedTranscript);
  };

  const handleSaveTranscript = async (updatedTranscript: TranscriptSegment[]) => {
    try {
      // Send full TranscriptSegmentResponse format (server will extract speaker_index)
      await axios.post(`/api/meetings/${meeting.id}/transcript`, {
        transcript: updatedTranscript,
      });
    } catch (error) {
      console.error('Failed to save transcript:', error);
      alert('회의록 저장에 실패했습니다');
    }
  };

  const handleRecordingComplete = async (audioBlob: Blob) => {
    const formData = new FormData();
    formData.append('file', audioBlob, `meeting-${meeting.id}.wav`);

    setIsUploading(true);
    setUploadProgress(0);

    try {
      // API 요청 시작 시 isProcessing을 true로 설정
      // 이렇게 하면 pending 상태부터 처리 중 메시지가 표시됨
      setIsProcessing(true);

      const response = await axios.post(
        `/api/meetings/${meeting.id}/upload-audio`,
        formData,
        {
          headers: {
            'Content-Type': 'multipart/form-data',
          },
          onUploadProgress: (progressEvent) => {
            if (progressEvent.total) {
              const percentCompleted = Math.round(
                (progressEvent.loaded * 100) / progressEvent.total
              );
              setUploadProgress(percentCompleted);
            }
          },
        }
      );

      // 응답 수신 후 업로드 상태 종료
      setIsUploading(false);

      if (response.data.segments) {
        setTranscript(response.data.segments);
      }
      
      // 응답 수신 후 처리 완료
      setIsProcessing(false);
      
      // 업로드 완료 후 부모 컴포넌트의 재조회 로직 호출
      // 이를 통해 meeting.audio_files가 업데이트되어 다운로드 버튼이 활성화됨
      onUpdate();
    } catch (error) {
      console.error('Failed to upload audio:', error);
      alert('음성 파일 업로드에 실패했습니다');
      setIsUploading(false);
      setIsProcessing(false);
    } finally {
      setUploadProgress(0);
    }
  };

  return (
    <div className="meeting-detail">
      <div className="detail-header">
        <h1>{meeting.title}</h1>
        <button 
          className="settings-button"
          onClick={() => setShowSettingsModal(true)}
          title="회의 설정"
        >
          ⚙️
        </button>
      </div>

      <div className="detail-container">
        {/* Left Side - Meeting Content */}
        <div className="meeting-section">
          <div className="detail-content">
            <section className="recorder-section">
              <h3>녹음</h3>
              <div className="recorder-container">
                <RecorderControls onRecordingComplete={handleRecordingComplete} />
                <div className="or-divider">또는</div>
                <div className="file-upload-section">
                  <input
                    type="file"
                    id="wav-upload"
                    accept=".wav,audio/wav"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        handleRecordingComplete(file as Blob);
                      }
                      e.target.value = '';
                    }}
                    style={{ display: 'none' }}
                    disabled={isUploading}
                  />
                  <label htmlFor="wav-upload" className="upload-button" style={{ opacity: isUploading ? 0.5 : 1, cursor: isUploading ? 'not-allowed' : 'pointer' }}>
                    WAV 파일 업로드
                  </label>
                  <button
                    className="download-button"
                    onClick={handleDownloadAudio}
                    disabled={!meeting.audio_files || meeting.audio_files.length === 0}
                    title={meeting.audio_files && meeting.audio_files.length > 0 ? "녹취 파일 다운로드" : "업로드된 녹취 파일이 없습니다"}
                  >
                    🔽 녹취 다운로드
                  </button>
                </div>
                {isUploading && (
                  <div className="upload-progress-container">
                    <div className="progress-text">
                      <span>업로드 중...</span>
                      <span className="progress-percentage">{uploadProgress}%</span>
                    </div>
                    <div className="progress-bar-wrapper">
                      <div className="progress-bar" style={{ width: `${uploadProgress}%` }}></div>
                    </div>
                  </div>
                )}
                {isProcessing && (
                  <div className="processing-container">
                    <div className="processing-spinner"></div>
                    <span className="processing-text">업로드된 파일로부터 음성인식 중입니다. 잠시만 기다려주세요.</span>
                  </div>
                )}
              </div>
            </section>

            <section className="transcript-section">
              <div className="transcript-header">
                <h3>회의 내용</h3>
              </div>
              <TranscriptEditor
                transcript={transcript}
                onUpdate={handleTranscriptUpdate}
                onSave={handleSaveTranscript}
              />
            </section>
          </div>
        </div>

        {/* Right Side - Summary Panel */}
        <div className="summary-section-wrapper">
          <SummaryPanel meetingId={meeting.id} />
        </div>
      </div>

      {/* Settings Modal */}
      {showSettingsModal && (
        <div className="modal-overlay" onClick={() => setShowSettingsModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>회의 설정</h2>
              <button
                className="close-button"
                onClick={() => setShowSettingsModal(false)}
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <MeetingSettings 
                meeting={meeting} 
                onUpdate={() => {
                  setShowSettingsModal(false);
                  onUpdate();
                }}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default MeetingDetail;
