import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { apiClient } from '../api/authApi';
import { saveAndShare } from '../utils/download';
import '../styles/SummaryPanel.css';

interface Paragraph {
  id?: number;
  subject: string;
  summary: string;
  start: number;
  end: number;
}

interface SummaryData {
  meeting_id: string;
  subject: string | null;
  paragraphs: Paragraph[];
  next_steps: string[];
  meeting_notes: string | null;
}

// 서버의 요약 진행 상태 (GET /api/summary/{id}/status)
type SummaryStatus = 'none' | 'queued' | 'running' | 'done' | 'failed';

interface SummaryStatusData {
  status: SummaryStatus;
  error: string | null;
}

// 요약 진행 상태 폴링 주기 (STT 상태 폴링과 같은 간격)
const STATUS_POLL_INTERVAL_MS = 4000;

const REGENERATE_NOTES_CONFIRM = '현재 회의록 내용을 새로 생성한 내용으로 교체합니다. 진행할까요?';

const isGenerating = (status: SummaryStatus | null) => status === 'queued' || status === 'running';

interface SummaryPanelProps {
  meetingId: string;
  // MeetingDetail이 STT 완료를 확인할 때마다 올리는 값. 바뀌면 자동 요약 진행 상태를 조회한다.
  transcriptionDoneSignal?: number;
  // 사용자가 참여자 이름을 바꿔 저장할 때마다 올리는 값. 바뀌면 회의록 재생성을 안내한다.
  participantsChangedSignal?: number;
  // Android 전용: 기기에 영구 보관된 녹취가 있어 로컬 재생이 가능한지 여부.
  // false면(웹/과거 미팅 등) 단락 재생 버튼 자체를 노출하지 않는다.
  canPlayLocally?: boolean;
  // 현재 재생 중인 단락 id (전체 재생바와 오디오 엘리먼트는 MeetingDetail이 소유·공유한다)
  playingParagraphId?: number | null;
  onToggleParagraphPlayback?: (paragraphId: number, startMs: number, endMs: number) => void;
}

function SummaryPanel({
  meetingId,
  transcriptionDoneSignal = 0,
  participantsChangedSignal = 0,
  canPlayLocally,
  playingParagraphId = null,
  onToggleParagraphPlayback,
}: SummaryPanelProps) {
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isSendingEmail, setIsSendingEmail] = useState(false);
  const [editingSubject, setEditingSubject] = useState(false);
  const [editingNextSteps, setEditingNextSteps] = useState(false);
  const [editingParagraph, setEditingParagraph] = useState<number | null>(null);
  const [subjectText, setSubjectText] = useState('');
  const [nextStepsText, setNextStepsText] = useState<string[]>([]);
  const [paragraphEdits, setParagraphEdits] = useState<{ [key: number]: Paragraph }>({});
  const [showNotesPreview, setShowNotesPreview] = useState(false);
  const [meetingNotesText, setMeetingNotesText] = useState('');
  const [isSavingNotes, setIsSavingNotes] = useState(false);
  const [isRegeneratingNotes, setIsRegeneratingNotes] = useState(false);
  const [summaryStatus, setSummaryStatus] = useState<SummaryStatus | null>(null);
  // 참여자 이름을 바꾼 뒤 회의록 재생성 안내
  const [showNotesHint, setShowNotesHint] = useState(false);
  // 화면에 떠 있는 회의. 회의를 바꾼 뒤 늦게 도착한 응답을 버리는 데 쓴다.
  const currentMeetingRef = useRef(meetingId);
  // 요약 상태 폴링 제어. 폴링을 시작하거나 멈출 때마다 run을 올려 이전 폴링의 늦은 응답을 버린다.
  const statusTimerRef = useRef<number | null>(null);
  const statusTargetRef = useRef<string | null>(null);
  const statusRunRef = useRef(0);
  // 진행 중이거나 곧 시작될 요약을 지켜보는 중인지. 지켜보다 끝나면 요약을 다시 불러온다.
  const watchingRef = useRef(false);
  // 생성 중이 아닌 상태가 와도 다시 확인할 남은 횟수 (STT 완료 직후용)
  const graceChecksRef = useRef(0);
  const lastTranscriptionSignalRef = useRef(transcriptionDoneSignal);
  const lastParticipantsSignalRef = useRef(participantsChangedSignal);

  // 회의 변경/진입 시: 요약을 불러오고 진행 상태를 한 번 조회한다(자동 요약이 도는 중이면 폴링).
  useEffect(() => {
    currentMeetingRef.current = meetingId;
    setSummaryStatus(null);
    setShowNotesHint(false);
    setIsLoading(false);
    loadSummary(meetingId);
    startStatusPolling(meetingId);
    return () => stopStatusPolling();
  }, [meetingId]);

  // STT 완료 직후: 자동 요약 작업이 들어오므로 진행 상태를 지켜본다.
  // 워커는 전사 완료를 기록한 다음에 요약 작업을 넣어서, 그 사이에 조회하면 작업이 아직 없다. 그래서 한 번 더 확인한다.
  useEffect(() => {
    if (transcriptionDoneSignal === lastTranscriptionSignalRef.current) return;
    lastTranscriptionSignalRef.current = transcriptionDoneSignal;
    startStatusPolling(meetingId, { watch: true, graceChecks: 1 });
  }, [transcriptionDoneSignal]);

  useEffect(() => {
    if (participantsChangedSignal === lastParticipantsSignalRef.current) return;
    lastParticipantsSignalRef.current = participantsChangedSignal;
    setShowNotesHint(true);
  }, [participantsChangedSignal]);

  // 앱이 다시 포그라운드로 돌아오면 기다리던 상태 조회를 바로 한다 (대기시간 단축)
  useEffect(() => {
    const onVisible = () => {
      const target = statusTargetRef.current;
      if (document.visibilityState === 'visible' && target && statusTimerRef.current) {
        clearTimeout(statusTimerRef.current);
        statusTimerRef.current = null;
        void pollStatus(target, statusRunRef.current);
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, []);

  const applySummary = (data: SummaryData) => {
    setSummary(data);
    setSubjectText(data.subject || '');
    setNextStepsText(data.next_steps || []);
    setMeetingNotesText(data.meeting_notes || '');
  };

  // 요약이 새로 생성되면 이전 요약을 편집하던 상태와 회의록 재생성 안내를 정리한다.
  const resetEditing = () => {
    setEditingSubject(false);
    setEditingNextSteps(false);
    setEditingParagraph(null);
    setParagraphEdits({});
    setShowNotesHint(false);
  };

  const loadSummary = async (id: string) => {
    try {
      const response = await apiClient.get(`/api/summary/${id}`);
      if (currentMeetingRef.current !== id) return;
      applySummary(response.data);
    } catch (error) {
      // Summary might not exist yet
      console.log('Summary not found, create new one');
    }
  };

  const stopStatusPolling = () => {
    statusRunRef.current += 1;
    if (statusTimerRef.current) {
      clearTimeout(statusTimerRef.current);
      statusTimerRef.current = null;
    }
    statusTargetRef.current = null;
  };

  const startStatusPolling = (id: string, { watch = false, graceChecks = 0 } = {}) => {
    stopStatusPolling();
    statusTargetRef.current = id;
    watchingRef.current = watch;
    graceChecksRef.current = graceChecks;
    void pollStatus(id, statusRunRef.current);
  };

  // 요약 진행 상태를 한 번 조회하고, 생성 중이면 다음 조회를 예약한다.
  const pollStatus = async (id: string, run: number) => {
    statusTimerRef.current = null;
    const scheduleNext = () => {
      statusTimerRef.current = window.setTimeout(() => pollStatus(id, run), STATUS_POLL_INTERVAL_MS);
    };

    let data: SummaryStatusData;
    try {
      const res = await apiClient.get<SummaryStatusData>(`/api/summary/${id}/status`);
      data = res.data;
    } catch (error) {
      if (run !== statusRunRef.current) return;
      if (watchingRef.current) {
        // 일시적 오류(화면 꺼짐 등) → 잠시 후 재시도
        console.error('Summary status polling failed, will retry:', error);
        scheduleNext();
      } else {
        console.error('Failed to load summary status:', error);
        stopStatusPolling();
      }
      return;
    }
    if (run !== statusRunRef.current) return;

    if (isGenerating(data.status)) {
      watchingRef.current = true;
      graceChecksRef.current = 0;
      setSummaryStatus(data.status);
      scheduleNext();
      return;
    }
    if (graceChecksRef.current > 0) {
      graceChecksRef.current -= 1;
      scheduleNext();
      return;
    }

    const wasWatching = watchingRef.current;
    stopStatusPolling();
    setSummaryStatus(data.status);
    if (data.status === 'failed') {
      console.warn('Summary generation failed:', data.error);
    }
    if (wasWatching && data.status === 'done') {
      resetEditing();
      await loadSummary(id);
    }
  };

  const handleGenerateSummary = async () => {
    const id = meetingId;
    stopStatusPolling();
    setIsLoading(true);
    // 서버가 요약 작업을 기다리는 동안 진행 표시를 보여준다.
    setSummaryStatus('queued');
    try {
      const response = await apiClient.post(`/api/summary/${id}`);
      if (currentMeetingRef.current !== id) return;
      resetEditing();
      applySummary(response.data);
      setSummaryStatus('done');
    } catch (error) {
      if (currentMeetingRef.current !== id) return;
      if (axios.isAxiosError(error) && (!error.response || error.response.status === 504)) {
        // 응답을 못 받았거나 서버의 대기 시간을 넘겼다. 작업은 서버에서 계속 도니 상태 폴링으로 끝을 기다린다.
        startStatusPolling(id, { watch: true });
        return;
      }
      console.error('Failed to generate summary:', error);
      alert('요약 생성에 실패했습니다');
      // 실패 표시를 서버 상태와 맞춘다
      startStatusPolling(id);
    } finally {
      if (currentMeetingRef.current === id) setIsLoading(false);
    }
  };

  const handleSaveSubject = async () => {
    if (!summary) return;
    setIsSaving(true);
    try {
      const response = await apiClient.put(`/api/meetings/${meetingId}/subject`, {
        subject: subjectText,
      });
      setSummary({
        ...summary,
        subject: response.data.subject || subjectText,
      });
      setEditingSubject(false);
    } catch (error) {
      console.error('Failed to save subject:', error);
      alert('주제 저장에 실패했습니다');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveNextSteps = async () => {
    if (!summary) return;
    setIsSaving(true);
    try {
      const response = await apiClient.put(
        `/api/summary/${meetingId}/next-steps`,
        {
          next_steps: nextStepsText,
        }
      );
      setSummary(response.data);
      setEditingNextSteps(false);
    } catch (error) {
      console.error('Failed to save next steps:', error);
      alert('다음 할 일 저장에 실패했습니다');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveParagraph = async (paragraphId: number) => {
    if (!summary) return;
    const editedParagraph = paragraphEdits[paragraphId];
    if (!editedParagraph) return;

    setIsSaving(true);
    try {
      const response = await apiClient.put(
        `/api/summary/${meetingId}/paragraph/${paragraphId}`,
        {
          subject: editedParagraph.subject,
          summary: editedParagraph.summary,
          start: editedParagraph.start,
          end: editedParagraph.end,
        }
      );
      setSummary(response.data);
      setEditingParagraph(null);
      setParagraphEdits({});
    } catch (error) {
      console.error('Failed to save paragraph:', error);
      alert('단락 저장에 실패했습니다');
    } finally {
      setIsSaving(false);
    }
  };

  const handleAddNextStep = () => {
    setNextStepsText([...nextStepsText, '']);
  };

  const handleRemoveNextStep = (index: number) => {
    setNextStepsText(nextStepsText.filter((_, i) => i !== index));
  };

  const handleNextStepChange = (index: number, value: string) => {
    const updated = [...nextStepsText];
    updated[index] = value;
    setNextStepsText(updated);
  };

  const handleParagraphChange = (
    paragraphId: number,
    field: keyof Paragraph,
    value: any
  ) => {
    // Find the paragraph from summary by ID
    const original = summary!.paragraphs.find(p => p.id === paragraphId);
    if (!original) return;

    setParagraphEdits({
      ...paragraphEdits,
      [paragraphId]: {
        ...original,
        ...paragraphEdits[paragraphId],
        [field]: value,
      },
    });
  };

  // 미리보기에서 편집한 회의록 본문을 서버에 저장한다. 저장된 SummaryData를 반환.
  const persistMeetingNotes = async (text: string): Promise<SummaryData | null> => {
    try {
      const response = await apiClient.put(
        `/api/summary/${meetingId}/meeting-notes`,
        { meeting_notes: text }
      );
      setSummary(response.data);
      setMeetingNotesText(response.data.meeting_notes || '');
      return response.data;
    } catch (error) {
      console.error('Failed to save meeting notes:', error);
      alert('회의록 저장에 실패했습니다');
      return null;
    }
  };

  const handleSaveNotes = async () => {
    setIsSavingNotes(true);
    const saved = await persistMeetingNotes(meetingNotesText);
    setIsSavingNotes(false);
    if (saved) {
      alert('회의록이 저장되었습니다');
    }
  };

  // 기존 요약은 그대로 두고 회의록 본문만 다시 생성한다.
  const handleRegenerateNotes = async () => {
    if (!window.confirm(REGENERATE_NOTES_CONFIRM)) {
      return;
    }
    await regenerateNotes();
  };

  // 참여자 이름 변경 안내에서 재생성: 결과를 바로 볼 수 있게 회의록 미리보기를 연다.
  const handleRegenerateNotesFromHint = async () => {
    if (!window.confirm(REGENERATE_NOTES_CONFIRM)) {
      return;
    }
    openNotesPreview();
    await regenerateNotes();
  };

  const regenerateNotes = async () => {
    setIsRegeneratingNotes(true);
    try {
      const response = await apiClient.post(
        `/api/summary/${meetingId}/meeting-notes/regenerate`
      );
      setSummary(response.data);
      setMeetingNotesText(response.data.meeting_notes || '');
      setShowNotesHint(false);
    } catch (error) {
      console.error('Failed to regenerate meeting notes:', error);
      alert('회의록 재생성에 실패했습니다');
    } finally {
      setIsRegeneratingNotes(false);
    }
  };

  const handleSendEmail = async () => {
    setIsSendingEmail(true);
    try {
      const response = await apiClient.post(`/api/summary/${meetingId}/email`);
      const email = response.data?.email;
      alert(email ? `회의록이 ${email}로 전송되었습니다` : '회의록이 이메일로 전송되었습니다');
    } catch (error) {
      console.error('Failed to send meeting notes email:', error);
      alert('회의록 이메일 전송에 실패했습니다');
    } finally {
      setIsSendingEmail(false);
    }
  };

  const handleDownloadSummary = async () => {
    try {
      const response = await apiClient.get(
        `/api/summary/${meetingId}/download`,
        { responseType: 'blob' }
      );
      await saveAndShare(response.data, `meeting-notes-${meetingId}.docx`);
    } catch (error) {
      console.error('Failed to download meeting notes:', error);
      alert('회의록 다운로드에 실패했습니다');
    }
  };

  const openNotesPreview = () => {
    setMeetingNotesText(summary?.meeting_notes || '');
    setShowNotesPreview(true);
  };

  const formatTime = (ms: number): string => {
    const totalSeconds = Math.floor(ms / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  };

  // 자동 요약이 도는 중이거나 사용자가 요청한 요약을 기다리는 중
  const isBusy = isLoading || isGenerating(summaryStatus);

  // 요약 진행·실패 표시. 실패 사유(서버 내부 메시지)는 화면에 내지 않고 콘솔에만 남긴다.
  const statusBanner = isGenerating(summaryStatus) ? (
    <div className="summary-status-banner generating">
      <div className="summary-status-spinner"></div>
      <span className="summary-status-text">AI 요약을 생성하고 있습니다. 완료되면 자동으로 표시됩니다.</span>
    </div>
  ) : summaryStatus === 'failed' ? (
    <div className="summary-status-banner failed">
      <span className="summary-status-text">⚠️ AI 요약 생성에 실패했습니다. 다시 생성해 주세요.</span>
      <button
        className="summary-status-btn"
        onClick={handleGenerateSummary}
        disabled={isBusy}
      >
        다시 생성
      </button>
    </div>
  ) : null;

  if (!summary) {
    return (
      <div className="summary-panel">
        <div className="summary-header">
          <h2>AI 요약</h2>
          <button
            className="generate-summary-btn"
            onClick={handleGenerateSummary}
            disabled={isBusy}
          >
            {isBusy ? '생성 중...' : 'AI요약'}
          </button>
        </div>
        {statusBanner && <div className="summary-status-area">{statusBanner}</div>}
        <div className="summary-placeholder">
          <p>아직 생성된 요약이 없습니다.</p>
          {!isBusy && <p>위의 "AI요약" 버튼을 클릭하여 요약을 생성해주세요.</p>}
        </div>
      </div>
    );
  }

  const notesHint = showNotesHint && !!summary.meeting_notes && !isBusy ? (
    <div className="summary-status-banner notice">
      <span className="summary-status-text">
        참여자 이름이 바뀌었습니다. 회의록의 참석자에 반영하려면 회의록을 다시 생성해 주세요.
      </span>
      <div className="summary-status-actions">
        <button
          className="summary-status-btn"
          onClick={handleRegenerateNotesFromHint}
          disabled={isRegeneratingNotes}
        >
          {isRegeneratingNotes ? '재생성 중...' : '🔄 회의록 재생성'}
        </button>
        <button
          className="summary-status-dismiss"
          onClick={() => setShowNotesHint(false)}
          title="닫기"
          aria-label="안내 닫기"
        >
          ×
        </button>
      </div>
    </div>
  ) : null;

  return (
    <div className="summary-panel">
      <div className="summary-header">
        <h2>AI 요약</h2>
        <div className="summary-buttons">
          {(() => {
            const hasMeetingNotes = !!summary && !!summary.meeting_notes;
            const notesTitle = hasMeetingNotes ? undefined : "먼저 AI 요약을 생성해주세요";
            return (
              <>
                <button
                  className="preview-notes-btn"
                  onClick={openNotesPreview}
                  disabled={!hasMeetingNotes}
                  title={hasMeetingNotes ? "회의록을 미리보고 편집" : notesTitle}
                >
                  📝 회의록 미리보기
                </button>
                <button
                  className="email-summary-btn"
                  onClick={handleSendEmail}
                  disabled={!hasMeetingNotes || isSendingEmail}
                  title={hasMeetingNotes ? "가입한 이메일로 회의록(Word) 전송" : notesTitle}
                >
                  {isSendingEmail ? '전송 중...' : '📧 이메일 전송'}
                </button>
                <button
                  className="download-summary-btn"
                  onClick={handleDownloadSummary}
                  disabled={!hasMeetingNotes}
                  title={hasMeetingNotes ? "회의록을 Word 문서로 다운로드" : notesTitle}
                >
                  🔽 회의록 다운로드
                </button>
              </>
            );
          })()}
          <button
            className="generate-summary-btn"
            onClick={handleGenerateSummary}
            disabled={isBusy}
          >
            {isBusy ? '생성 중...' : 'AI요약'}
          </button>
        </div>
      </div>

      {(statusBanner || notesHint) && (
        <div className="summary-status-area">
          {statusBanner}
          {notesHint}
        </div>
      )}

      <div className="summary-content">
        {/* Subject Section */}
        <section className="summary-section subject-section">
          <h3>요약</h3>
          {editingSubject ? (
            <div className="edit-container">
              <textarea
                value={subjectText}
                onChange={(e) => setSubjectText(e.target.value)}
                className="edit-textarea"
                placeholder="요약을 입력하세요"
              />
              <div className="edit-buttons">
                <button
                  className="save-btn"
                  onClick={handleSaveSubject}
                  disabled={isSaving}
                >
                  {isSaving ? '저장 중...' : '저장'}
                </button>
                <button
                  className="cancel-btn"
                  onClick={() => {
                    setEditingSubject(false);
                    setSubjectText(summary.subject || '');
                  }}
                  disabled={isSaving}
                >
                  취소
                </button>
              </div>
            </div>
          ) : (
            <div
              className="display-container"
              onClick={() => setEditingSubject(true)}
            >
              <p className="subject-text">{summary.subject || '클릭하여 추가'}</p>
            </div>
          )}
        </section>

        {/* Next Steps Section */}
        <section className="summary-section next-steps-section">
          <h3>다음 할 일</h3>
          {editingNextSteps ? (
            <div className="edit-container">
              <div className="next-steps-edit">
                {nextStepsText.map((step, index) => (
                  <div key={index} className="next-step-edit-item">
                    <input
                      type="text"
                      value={step}
                      onChange={(e) =>
                        handleNextStepChange(index, e.target.value)
                      }
                      placeholder="할 일을 입력하세요"
                      className="next-step-input"
                    />
                    <button
                      className="remove-btn"
                      onClick={() => handleRemoveNextStep(index)}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
              <button
                className="add-step-btn"
                onClick={handleAddNextStep}
              >
                + 할 일 추가
              </button>
              <div className="edit-buttons">
                <button
                  className="save-btn"
                  onClick={handleSaveNextSteps}
                  disabled={isSaving}
                >
                  {isSaving ? '저장 중...' : '저장'}
                </button>
                <button
                  className="cancel-btn"
                  onClick={() => {
                    setEditingNextSteps(false);
                    setNextStepsText(summary.next_steps || []);
                  }}
                  disabled={isSaving}
                >
                  취소
                </button>
              </div>
            </div>
          ) : (
            <div
              className="display-container"
              onClick={() => setEditingNextSteps(true)}
            >
              {nextStepsText.length > 0 ? (
                <ul className="next-steps-list">
                  {nextStepsText.map((step, index) => (
                    <li key={index}>{step}</li>
                  ))}
                </ul>
              ) : (
                <p className="empty-text">클릭하여 추가</p>
              )}
            </div>
          )}
        </section>

        {/* Paragraphs Section */}
        <section className="summary-section paragraphs-section">
          <h3>단락</h3>
          <div className="paragraphs-container">
            {summary.paragraphs && summary.paragraphs.length > 0 ? (
              summary.paragraphs.map((paragraph) => (
                <div key={paragraph.id} className="paragraph-item">
                  {editingParagraph === paragraph.id ? (
                    <div className="paragraph-edit">
                      <div className="edit-field">
                        <label>주제</label>
                        <input
                          type="text"
                          value={
                            paragraphEdits[paragraph.id]?.subject ||
                            paragraph.subject
                          }
                          onChange={(e) =>
                            handleParagraphChange(
                              paragraph.id!,
                              'subject',
                              e.target.value
                            )
                          }
                          className="edit-input"
                        />
                      </div>
                      <div className="edit-field">
                        <label>내용</label>
                        <textarea
                          value={
                            paragraphEdits[paragraph.id]?.summary ||
                            paragraph.summary
                          }
                          onChange={(e) =>
                            handleParagraphChange(
                              paragraph.id!,
                              'summary',
                              e.target.value
                            )
                          }
                          className="edit-textarea"
                        />
                      </div>
                      <div className="edit-buttons">
                        <button
                          className="save-btn"
                          onClick={() => handleSaveParagraph(paragraph.id!)}
                          disabled={isSaving}
                        >
                          {isSaving ? '저장 중...' : '저장'}
                        </button>
                        <button
                          className="cancel-btn"
                          onClick={() => {
                            setEditingParagraph(null);
                            setParagraphEdits({});
                          }}
                          disabled={isSaving}
                        >
                          취소
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div
                      className="paragraph-display"
                      onClick={() => setEditingParagraph(paragraph.id!)}
                    >
                      <div className="paragraph-header">
                        <h4>{paragraph.subject}</h4>
                        {canPlayLocally && paragraph.id != null && (
                          <button
                            type="button"
                            className={
                              'paragraph-play-btn' +
                              (playingParagraphId === paragraph.id ? ' playing' : '')
                            }
                            onClick={(e) => {
                              e.stopPropagation();
                              onToggleParagraphPlayback?.(paragraph.id!, paragraph.start, paragraph.end);
                            }}
                            title={playingParagraphId === paragraph.id ? '일시정지' : '이 구간 재생'}
                          >
                            {playingParagraphId === paragraph.id ? '⏸' : '▶'}
                          </button>
                        )}
                        <div className="paragraph-time">
                          {formatTime(paragraph.start)} - {formatTime(paragraph.end)}
                        </div>
                      </div>
                      <p>{paragraph.summary}</p>
                    </div>
                  )}
                </div>
              ))
            ) : (
              <p className="empty-text">단락이 없습니다</p>
            )}
          </div>
        </section>
      </div>

      {/* 회의록 미리보기 (편집 가능) 모달 */}
      {showNotesPreview && (
        <div className="modal-overlay" onClick={() => setShowNotesPreview(false)}>
          <div className="modal-content notes-preview-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>회의록 미리보기</h2>
              <button
                className="close-button"
                onClick={() => setShowNotesPreview(false)}
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              {isRegeneratingNotes ? (
                <div className="notes-regenerating">회의록을 재생성하는 중입니다...</div>
              ) : (
                <textarea
                  className="notes-preview-textarea"
                  value={meetingNotesText}
                  onChange={(e) => setMeetingNotesText(e.target.value)}
                  placeholder="회의록 내용을 입력하세요"
                />
              )}
            </div>
            <div className="modal-footer">
              <button
                className="regenerate-notes-btn"
                onClick={handleRegenerateNotes}
                disabled={isSavingNotes || isRegeneratingNotes}
                title="기존 요약을 바탕으로 회의록을 다시 생성"
              >
                {isRegeneratingNotes ? '재생성 중...' : '🔄 회의록 재생성'}
              </button>
              <button
                className="save-btn"
                onClick={handleSaveNotes}
                disabled={isSavingNotes || isRegeneratingNotes}
              >
                {isSavingNotes ? '저장 중...' : '저장'}
              </button>
              <button
                className="cancel-btn"
                onClick={() => setShowNotesPreview(false)}
                disabled={isSavingNotes || isRegeneratingNotes}
              >
                닫기
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default SummaryPanel;
