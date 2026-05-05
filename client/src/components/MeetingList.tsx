import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiClient } from '../api/authApi';
import { useAuth } from '../contexts/AuthContext';
import DomainSettings from './DomainSettings';
import { confirmDialog } from '../utils/dialog';
import '../styles/MeetingList.css';

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

interface MeetingListProps {
  meetings: Meeting[];
  selectedMeeting: Meeting | null;
  onSelectMeeting: (meeting: Meeting) => void;
  onCreateMeeting: (title: string) => void;
  onDeleteMeeting?: (meetingId: string) => void;
  onBeforeSelectMeeting?: () => Promise<boolean>;
  onDomainsUpdate?: () => void;
}

function MeetingList({
  meetings,
  selectedMeeting,
  onSelectMeeting,
  onCreateMeeting,
  onDeleteMeeting,
  onBeforeSelectMeeting,
  onDomainsUpdate,
}: MeetingListProps) {
  const [newTitle, setNewTitle] = useState('');
  const [showDomainSettingsModal, setShowDomainSettingsModal] = useState(false);
  const { logout } = useAuth();
  const navigate = useNavigate();

  const handleDeleteMeeting = async (e: React.MouseEvent, meetingId: string) => {
    e.stopPropagation();

    if (!(await confirmDialog('정말로 이 회의를 삭제하시겠습니까? 모든 관련 데이터가 함께 삭제됩니다.'))) {
      return;
    }

    try {
      await apiClient.delete(`/api/meetings/${meetingId}`);
      if (onDeleteMeeting) {
        onDeleteMeeting(meetingId);
      }
    } catch (error) {
      console.error('Failed to delete meeting:', error);
      alert('회의 삭제에 실패했습니다');
    }
  };

  const handleCreateClick = () => {
    if (newTitle.trim()) {
      onCreateMeeting(newTitle);
      setNewTitle('');
    }
  };

  const handleLogout = async () => {
    if (!(await confirmDialog('로그아웃 하시겠습니까?'))) {
      return;
    }
    await logout();
    navigate('/login');
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const formatParticipants = (participants: string[]) => {
    if (participants.length <= 2) {
      return participants.join(', ');
    }
    return `${participants.slice(0, 2).join(', ')}, ...`;
  };

  return (
    <div className="meeting-list">
      <div className="list-header">
        <h2>회의 목록</h2>
        <div className="global-actions">
          <button
            className="global-action-button"
            onClick={() => setShowDomainSettingsModal(true)}
            title="도메인 설정"
          >
            🏷️ 도메인 설정
          </button>
          <button
            className="global-action-button logout-action"
            onClick={handleLogout}
            title="로그아웃"
          >
            로그아웃
          </button>
          <Link to="/profile" className="profile-button" title="프로필">
            👤
          </Link>
        </div>
      </div>

      <div className="create-meeting">
        <input
          type="text"
          placeholder="새 회의 제목 입력"
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          onKeyPress={(e) => e.key === 'Enter' && handleCreateClick()}
        />
        <button onClick={handleCreateClick}>생성</button>
      </div>

      <div className="meetings">
        {meetings.length === 0 ? (
          <div className="empty-state">회의가 없습니다</div>
        ) : (
          meetings.map((meeting) => (
            <div
              key={meeting.id}
              className={`meeting-item ${selectedMeeting?.id === meeting.id ? 'active' : ''}`}
              onClick={async () => {
                if (onBeforeSelectMeeting) {
                  const shouldProceed = await onBeforeSelectMeeting();
                  if (shouldProceed) {
                    onSelectMeeting(meeting);
                  }
                } else {
                  onSelectMeeting(meeting);
                }
              }}
            >
              <div className="meeting-info">
                <div className="meeting-title">{meeting.title}</div>
                <div className="meeting-date">{formatDate(meeting.created_at)}</div>
                <div className="meeting-participants">{formatParticipants(meeting.participants)}</div>
              </div>
              <button
                className="btn-delete-meeting"
                onClick={(e) => handleDeleteMeeting(e, meeting.id)}
                title="회의 삭제"
              >
                🗑️
              </button>
            </div>
          ))
        )}
      </div>

      <DomainSettings
        isOpen={showDomainSettingsModal}
        onClose={() => setShowDomainSettingsModal(false)}
        onUpdate={() => {
          if (onDomainsUpdate) {
            onDomainsUpdate();
          }
        }}
      />
    </div>
  );
}

export default MeetingList;
