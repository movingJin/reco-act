import { useState, useEffect } from 'react';
import { apiClient } from '../api/authApi';
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
}

interface SummaryPanelProps {
  meetingId: string;
}

function SummaryPanel({ meetingId }: SummaryPanelProps) {
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [editingSubject, setEditingSubject] = useState(false);
  const [editingNextSteps, setEditingNextSteps] = useState(false);
  const [editingParagraph, setEditingParagraph] = useState<number | null>(null);
  const [subjectText, setSubjectText] = useState('');
  const [nextStepsText, setNextStepsText] = useState<string[]>([]);
  const [paragraphEdits, setParagraphEdits] = useState<{ [key: number]: Paragraph }>({});

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

  const handleDownloadSummary = async () => {
    try {
      const response = await apiClient.get(
        `/api/summary/${meetingId}/download`,
        { responseType: 'blob' }
      );
      
      // Create a blob URL and trigger download
      const url = window.URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = `summary-${meetingId}.txt`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Failed to download summary:', error);
      alert('요약 다운로드에 실패했습니다');
    }
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
          <button
            className="download-summary-btn"
            onClick={handleDownloadSummary}
            disabled={!summary || (summary.paragraphs.length === 0 && !summary.subject && summary.next_steps.length === 0)}
            title={summary && (summary.paragraphs.length > 0 || summary.subject || summary.next_steps.length > 0) ? "요약 내용을 텍스트로 다운로드" : "먼저 AI 요약을 생성해주세요"}
          >
            🔽 요약 다운로드
          </button>
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
    </div>
  );
}

export default SummaryPanel;
