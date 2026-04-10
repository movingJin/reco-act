import { useState } from 'react';
import axios from 'axios';
import '../styles/MeetingSettings.css';

interface Meeting {
  id: string;
  title: string;
  created_at: string;
  participants: string[];
  transcript: any[];
  audio_files: string[];
}

interface MeetingSettingsProps {
  meeting: Meeting;
  onUpdate: () => void;
}

function MeetingSettings({ meeting, onUpdate }: MeetingSettingsProps) {
  const [title, setTitle] = useState<string>(meeting.title);
  const [participants, setParticipants] = useState<string[]>(meeting.participants);
  const [isSaving, setIsSaving] = useState(false);
  const [newParticipantName, setNewParticipantName] = useState('');

  const handleParticipantChange = (index: number, value: string) => {
    const updated = [...participants];
    updated[index] = value;
    setParticipants(updated);
  };

  const handleAddParticipant = () => {
    if (newParticipantName.trim()) {
      setParticipants([...participants, newParticipantName]);
      setNewParticipantName('');
    }
  };

  const handleRemoveParticipant = (index: number) => {
    setParticipants(participants.filter((_, i) => i !== index));
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      // Update title if changed
      if (title !== meeting.title) {
        await axios.put(`/api/meetings/${meeting.id}/title`, {
          title,
        });
      }

      // Update participants
      await axios.post(`/api/meetings/${meeting.id}/settings`, {
        participants,
      });
      
      onUpdate();
      alert('회의 설정이 저장되었습니다');
    } catch (error) {
      console.error('Failed to save settings:', error);
      alert('설정 저장에 실패했습니다');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="meeting-settings">
      <div className="title-section">
        <h4>회의 제목</h4>
        <div className="title-edit">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="title-input"
            placeholder="회의 제목"
          />
        </div>
      </div>

      <div className="participants-section">
        <h4>참여자 목록 ({participants.length}명)</h4>

        <div className="participants-list">
          {participants.map((participant, index) => (
            <div key={index} className="participant-item">
              <input
                type="text"
                value={participant}
                onChange={(e) => handleParticipantChange(index, e.target.value)}
                className="participant-input"
                placeholder="화자 이름"
              />
              <button
                className="btn-remove"
                onClick={() => handleRemoveParticipant(index)}
                title="제거"
              >
                ×
              </button>
            </div>
          ))}
        </div>

        <div className="add-participant">
          <input
            type="text"
            value={newParticipantName}
            onChange={(e) => setNewParticipantName(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleAddParticipant()}
            placeholder="새로운 화자 이름"
            className="participant-input"
          />
          <button
            className="btn-add"
            onClick={handleAddParticipant}
          >
            추가
          </button>
        </div>
      </div>

      <div className="settings-actions">
        <button
          className="btn-save"
          onClick={handleSave}
          disabled={isSaving}
        >
          {isSaving ? '저장 중...' : '저장'}
        </button>
      </div>
    </div>
  );
}

export default MeetingSettings;
