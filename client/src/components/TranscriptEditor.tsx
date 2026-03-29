import { useState } from 'react';
import '../styles/TranscriptEditor.css';

interface TranscriptSegment {
  speaker: string;
  text: string;
  start: number;
  end: number;
}

interface TranscriptEditorProps {
  transcript: TranscriptSegment[];
  onUpdate: (transcript: TranscriptSegment[]) => void;
  onSave?: (transcript: TranscriptSegment[]) => Promise<void>;
}

function TranscriptEditor({ transcript, onUpdate, onSave }: TranscriptEditorProps) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingText, setEditingText] = useState('');

  const handleEditStart = (index: number, text: string) => {
    setEditingId(index);
    setEditingText(text);
  };

  const handleEditSave = async (index: number) => {
    const updated = [...transcript];
    updated[index].text = editingText;
    onUpdate(updated);
    setEditingId(null);
    
    // 자동으로 저장 (onSave가 제공된 경우)
    if (onSave) {
      try {
        await onSave(updated);
      } catch (error) {
        console.error('Failed to save:', error);
      }
    }
  };

  const handleEditCancel = () => {
    setEditingId(null);
  };

  const handleDelete = async (index: number) => {
    const updated = transcript.filter((_, i) => i !== index);
    onUpdate(updated);
    
    // 삭제 후 자동으로 저장 (onSave가 제공된 경우)
    if (onSave) {
      try {
        await onSave(updated);
      } catch (error) {
        console.error('Failed to save:', error);
      }
    }
  };

  if (transcript.length === 0) {
    return (
      <div className="transcript-editor">
        <div className="empty-transcript">
          아직 회의록이 없습니다. 녹음을 시작하거나 음성 파일을 업로드해주세요.
        </div>
      </div>
    );
  }

  return (
    <div className="transcript-editor">
      <div className="segments-list">
        {transcript.map((segment, index) => (
          <div key={index} className="segment-item">
            <div className="segment-speaker">{segment.speaker}</div>
            <div className="segment-content">
              {editingId === index ? (
                <div className="segment-edit">
                  <textarea
                    value={editingText}
                    onChange={(e) => setEditingText(e.target.value)}
                    autoFocus
                  />
                  <div className="edit-buttons">
                    <button
                      className="btn-save"
                      onClick={() => handleEditSave(index)}
                    >
                      저장
                    </button>
                    <button
                      className="btn-cancel"
                      onClick={handleEditCancel}
                    >
                      취소
                    </button>
                  </div>
                </div>
              ) : (
                <div
                  className="segment-text"
                  onClick={() => handleEditStart(index, segment.text)}
                >
                  {segment.text}
                </div>
              )}
            </div>
            <button
              className="btn-delete"
              onClick={() => handleDelete(index)}
              title="삭제"
            >
              🗑️
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

export default TranscriptEditor;
