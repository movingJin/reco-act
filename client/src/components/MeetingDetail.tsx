import { useState, useEffect, useRef } from 'react';
import { apiClient } from '../api/authApi';
import RecorderControls, { RecorderControlsHandle } from './RecorderControls';
import TranscriptEditor from './TranscriptEditor';
import MeetingSettings from './MeetingSettings';
import SummaryPanel from './SummaryPanel';
import { saveAndShare } from '../utils/download';
import {
  PendingRecording,
  getUploadBlob,
  discardRecording,
  recoverPending,
} from '../utils/recordingStore';
import { useModalBackButton } from '../utils/backButton';
import { confirmDialog } from '../utils/dialog';
import '../styles/MeetingDetail.css';

interface TranscriptSegment {
  speaker_index: number;
  speaker_name: string;
  text: string;
  start: number;
  end: number;
}

interface Domain {
  id: number;
  domain_name: string;
  keywords: string[];
}

interface Meeting {
  id: string;
  title: string;
  created_at: string;
  participants: string[];
  transcript: TranscriptSegment[];
  audio_files: string[];
  domain_id?: number;
}

interface MeetingDetailProps {
  meeting: Meeting;
  onUpdate: () => void;
  onRequestMeetingChange?: (newMeeting: Meeting) => Promise<boolean>;
  onSetRecorderState?: (state: { isRecording: boolean; stopRecordingWithoutUpload: () => void }) => void;
  domainsVersion?: number;
}

function MeetingDetail({ meeting, onUpdate, onSetRecorderState, domainsVersion }: MeetingDetailProps) {
  const [transcript, setTranscript] = useState<TranscriptSegment[]>(meeting.transcript || []);
  const [showParticipantSettingsModal, setShowParticipantSettingsModal] = useState(false);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [selectedDomain, setSelectedDomain] = useState<number | null>(meeting.domain_id || null);
  const [isLoadingDomains, setIsLoadingDomains] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isUploading, setIsUploading] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  // 업로드되지 못한 채 디스크에 남아 있는 녹음 (크래시/연결 끊김 복구용)
  const [recoverable, setRecoverable] = useState<PendingRecording | null>(null);
  const recorderRef = useRef<RecorderControlsHandle>(null);

  // Android 백버튼으로 참여자 설정 모달 닫기
  useModalBackButton(showParticipantSettingsModal, () => setShowParticipantSettingsModal(false));

  useEffect(() => {
    // 회의가 변경되면 서버에서 transcript 로드
    const loadMeetingWithTranscript = async () => {
      try {
        const response = await apiClient.get(`/api/meetings/${meeting.id}`);
        setTranscript(response.data.transcript || []);
      } catch (error) {
        console.error('Failed to load meeting transcript:', error);
        setTranscript([]);
      }
    };
    
    loadMeetingWithTranscript();
    setSelectedDomain(meeting.domain_id || null);
  }, [meeting.id]);

  useEffect(() => {
    // recorderRef의 상태를 부모 컴포넌트에 전달
    if (onSetRecorderState && recorderRef.current) {
      onSetRecorderState({
        get isRecording() {
          return recorderRef.current?.isRecording ?? false;
        },
        stopRecordingWithoutUpload() {
          recorderRef.current?.stopRecordingWithoutUpload();
        },
      });
    }
  }, [onSetRecorderState]);

  useEffect(() => {
    loadDomains();
  }, [domainsVersion]);

  // 화면 진입 시, 업로드되지 못하고 기기에 남은 녹음이 있는지 확인한다.
  // (정지 시 OOM 크래시, 업로드 중 연결 끊김, 녹음 도중 강제 종료 등)
  useEffect(() => {
    let cancelled = false;
    recoverPending(meeting.id)
      .then((pending) => {
        if (!cancelled) setRecoverable(pending);
      })
      .catch((e) => console.error('Failed to check recoverable recording:', e));
    return () => {
      cancelled = true;
    };
  }, [meeting.id]);

  const loadDomains = async () => {
    setIsLoadingDomains(true);
    try {
      const response = await apiClient.get<Domain[]>('/api/domains');
      setDomains(response.data);
    } catch (error) {
      console.error('Failed to load domains:', error);
    } finally {
      setIsLoadingDomains(false);
    }
  };

  const handleDomainChange = async (domainId: number | null) => {
    try {
      await apiClient.put(`/api/meetings/${meeting.id}/domain`, null, {
        params: { domain_id: domainId }
      });
      setSelectedDomain(domainId);
      onUpdate();
    } catch (error) {
      console.error('Failed to update domain:', error);
      alert('도메인 설정에 실패했습니다');
    }
  };

  const handleDownloadAudio = async () => {
    try {
      const response = await apiClient.get(
        `/api/meetings/${meeting.id}/download-audio`,
        { responseType: 'blob' }
      );
      await saveAndShare(response.data, `meeting-${meeting.id}.wav`);
    } catch (error) {
      console.error('Failed to download audio:', error);
      alert('녹취 파일 다운로드에 실패했습니다');
    }
  };

  const handleDownloadTranscript = async () => {
    try {
      const response = await apiClient.get(
        `/api/meetings/${meeting.id}/download-transcript`,
        { responseType: 'blob' }
      );
      await saveAndShare(response.data, `transcript-${meeting.id}.txt`);
    } catch (error) {
      console.error('Failed to download transcript:', error);
      alert('대화 내용 다운로드에 실패했습니다');
    }
  };

  const handleTranscriptUpdate = (updatedTranscript: TranscriptSegment[]) => {
    setTranscript(updatedTranscript);
  };

  const handleSaveTranscript = async (updatedTranscript: TranscriptSegment[]) => {
    try {
      // Send full TranscriptSegmentResponse format (server will extract speaker_index)
      await apiClient.post(`/api/meetings/${meeting.id}/transcript`, {
        transcript: updatedTranscript,
      });
    } catch (error) {
      console.error('Failed to save transcript:', error);
      alert('회의록 저장에 실패했습니다');
    }
  };

  // 오디오 Blob을 서버에 업로드하는 공통 핵심. 성공 시 true를 반환하고,
  // 응답 결과(transcript 등)를 반영한다. 실패하면 예외를 던진다.
  const postAudio = async (blob: Blob, targetMeetingId: string): Promise<void> => {
    const formData = new FormData();
    formData.append('file', blob, `meeting-${targetMeetingId}.wav`);

    setIsUploading(true);
    setUploadProgress(0);
    setIsProcessing(true);

    try {
      const response = await apiClient.post(
        `/api/meetings/${targetMeetingId}/upload-audio`,
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

      setIsUploading(false);
      if (response.data.segments) {
        setTranscript(response.data.segments);
      }
      setIsProcessing(false);
      // meeting.audio_files 갱신 → 다운로드 버튼 활성화
      onUpdate();
    } catch (error) {
      setIsUploading(false);
      setIsProcessing(false);
      throw error;
    } finally {
      setUploadProgress(0);
    }
  };

  // 디스크에 저장된 녹음(pending)을 업로드한다. 성공이 확인된 뒤에만 파일을 삭제하고,
  // 실패(연결 끊김 포함)하면 파일을 그대로 두어 복구 배너로 재업로드할 수 있게 한다.
  const uploadPending = async (pending: PendingRecording) => {
    try {
      const audioBlob = await getUploadBlob(pending);
      await postAudio(audioBlob, pending.meetingId);
      // 업로드가 성공으로 확인됐을 때만 저장된 녹음을 삭제한다.
      await discardRecording(pending);
      setRecoverable(null);
    } catch (error) {
      console.error('Failed to upload recording:', error);
      // 파일은 보존한 채 복구 배너를 띄운다. (응답만 유실됐을 수도 있으므로 안내)
      setRecoverable(pending);
      alert(
        '음성 파일 업로드에 실패했거나 응답을 받지 못했습니다.\n' +
          '녹음은 기기에 저장되어 있으니, 잠시 후 다시 업로드해 주세요.'
      );
    }
  };

  const handleRecordingComplete = (pending: PendingRecording) => {
    void uploadPending(pending);
  };

  // 사용자가 직접 고른 WAV 파일 업로드 (영속화 불필요 — 이미 파일로 존재)
  const handleFileUpload = async (file: File) => {
    try {
      await postAudio(file, meeting.id);
    } catch (error) {
      console.error('Failed to upload audio:', error);
      alert('음성 파일 업로드에 실패했습니다');
    }
  };

  const handleDiscardRecoverable = async () => {
    if (!recoverable) return;
    const ok = await confirmDialog('저장된 녹음을 삭제하시겠습니까? 되돌릴 수 없습니다.');
    if (!ok) return;
    await discardRecording(recoverable);
    setRecoverable(null);
  };

  return (
    <div className="meeting-detail">
      <div className="detail-header">
        <h1>{meeting.title}</h1>
        <div className="header-controls">
          <div className="domain-selector-group">
            <label htmlFor="domain-select">도메인:</label>
            <select
              id="domain-select"
              value={selectedDomain || ''}
              onChange={(e) => handleDomainChange(e.target.value ? parseInt(e.target.value) : null)}
              disabled={isLoadingDomains}
              className="domain-select"
            >
              <option value="">도메인 선택 안함</option>
              {domains.map((domain) => (
                <option key={domain.id} value={domain.id}>
                  {domain.domain_name}
                </option>
              ))}
            </select>
          </div>
          <button
            className="settings-button"
            onClick={() => setShowParticipantSettingsModal(true)}
            title="참여자 설정"
          >
            👥 참여자 설정
          </button>
        </div>
      </div>

      <div className="detail-container">
        {/* Left Side - Meeting Content */}
        <div className="meeting-section">
          <div className="detail-content">
            <section className="recorder-section">
              <h3>녹음</h3>
              <div className="recorder-container">
                {recoverable && (
                  <div className="recovery-banner">
                    <span className="recovery-text">
                      ⚠️ 업로드되지 않은 녹음이 기기에 저장되어 있습니다.
                    </span>
                    <div className="recovery-actions">
                      <button
                        className="btn btn-primary"
                        onClick={() => uploadPending(recoverable)}
                        disabled={isUploading}
                      >
                        다시 업로드
                      </button>
                      <button
                        className="btn btn-danger"
                        onClick={handleDiscardRecoverable}
                        disabled={isUploading}
                      >
                        삭제
                      </button>
                    </div>
                  </div>
                )}
                <RecorderControls
                  ref={recorderRef}
                  meetingId={meeting.id}
                  onRecordingComplete={handleRecordingComplete}
                />
                <div className="or-divider">또는</div>
                <div className="file-upload-section">
                  <input
                    type="file"
                    id="wav-upload"
                    accept=".wav,audio/wav"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        void handleFileUpload(file);
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
                  <button
                    className="download-button"
                    onClick={handleDownloadTranscript}
                    disabled={!transcript || transcript.length === 0}
                    title={transcript && transcript.length > 0 ? "대화 내용을 텍스트로 다운로드" : "저장된 대화 내용이 없습니다"}
                  >
                    🔽 대화 내용 다운로드
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

      {/* Participant Settings Modal */}
      {showParticipantSettingsModal && (
        <div className="modal-overlay" onClick={() => setShowParticipantSettingsModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>참여자 설정</h2>
              <button
                className="close-button"
                onClick={() => setShowParticipantSettingsModal(false)}
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <MeetingSettings 
                meeting={meeting} 
                onUpdate={() => {
                  setShowParticipantSettingsModal(false);
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
