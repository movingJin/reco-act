import { useState, useEffect } from 'react';
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

interface SummaryPanelProps {
  meetingId: string;
  // Android 전용: 기기에 영구 보관된 녹취가 있어 로컬 재생이 가능한지 여부.
  // false면(웹/과거 미팅 등) 단락 재생 버튼 자체를 노출하지 않는다.
  canPlayLocally?: boolean;
  // 현재 재생 중인 단락 id (전체 재생바와 오디오 엘리먼트는 MeetingDetail이 소유·공유한다)
  playingParagraphId?: number | null;
  onToggleParagraphPlayback?: (paragraphId: number, startMs: number, endMs: number) => void;
}

function SummaryPanel({
  meetingId,
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

  // Load summary on mount
  useEffect(() => {
    loadSummary();
  }, [meetingId]);

  const loadSummary = async () => {
    try {
      const response = await apiClient.get(`/api/summary/${meetingId}`);
      setSummary(response.data);
      setSubjectText(response.data.subject || '');
      setNextStepsText(response.data.next_steps || []);
      setMeetingNotesText(response.data.meeting_notes || '');
    } catch (error) {
      // Summary might not exist yet
      console.log('Summary not found, create new one');
    }
  };

  const handleGenerateSummary = async () => {
    setIsLoading(true);
    try {
      const response = await apiClient.post(`/api/summary/${meetingId}`);
      setSummary(response.data);
      setSubjectText(response.data.subject || '');
      setNextStepsText(response.data.next_steps || []);
      setMeetingNotesText(response.data.meeting_notes || '');
    } catch (error) {
      console.error('Failed to generate summary:', error);
      alert('요약 생성에 실패했습니다');
    } finally {
      setIsLoading(false);
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
    if (!window.confirm('현재 회의록 내용을 새로 생성한 내용으로 교체합니다. 진행할까요?')) {
      return;
    }
    setIsRegeneratingNotes(true);
    try {
      const response = await apiClient.post(
        `/api/summary/${meetingId}/meeting-notes/regenerate`
      );
      setSummary(response.data);
      setMeetingNotesText(response.data.meeting_notes || '');
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

  if (!summary) {
    return (
      <div className="summary-panel">
        <div className="summary-header">
          <h2>AI 요약</h2>
          <button
            className="generate-summary-btn"
            onClick={handleGenerateSummary}
            disabled={isLoading}
          >
            {isLoading ? '생성 중...' : 'AI요약'}
          </button>
        </div>
        <div className="summary-placeholder">
          <p>아직 생성된 요약이 없습니다.</p>
          <p>위의 "AI요약" 버튼을 클릭하여 요약을 생성해주세요.</p>
        </div>
      </div>
    );
  }

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
            disabled={isLoading}
          >
            {isLoading ? '생성 중...' : 'AI요약'}
          </button>
        </div>
      </div>

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
