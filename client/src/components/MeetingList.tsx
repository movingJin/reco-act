import { useState } from 'react';
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
}

function MeetingList({
  meetings,
  selectedMeeting,
  onSelectMeeting,
  onCreateMeeting,
}: MeetingListProps) {
  const [newTitle, setNewTitle] = useState('');

  const handleCreateClick = () => {
    if (newTitle.trim()) {
      onCreateMeeting(newTitle);
      setNewTitle('');
    }
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
              onClick={() => onSelectMeeting(meeting)}
            >
              <div className="meeting-title">{meeting.title}</div>
              <div className="meeting-date">{formatDate(meeting.created_at)}</div>
              <div className="meeting-participants">{formatParticipants(meeting.participants)}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default MeetingList;
