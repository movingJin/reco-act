import { useEffect, useState } from 'react';
import axios from 'axios';
import MeetingList from './components/MeetingList';
import MeetingDetail from './components/MeetingDetail';
import './styles/App.css';

interface Meeting {
  id: string;
  title: string;
  created_at: string;
  participants: string[];
  transcript: TranscriptSegment[];
  audio_files: string[];
}

interface TranscriptSegment {
  speaker_index: number;
  speaker_name: string;
  text: string;
  start: number;
  end: number;
}

function App() {
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [selectedMeeting, setSelectedMeeting] = useState<Meeting | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadMeetings();
  }, []);

  const loadMeetings = async () => {
    try {
      const response = await axios.get<{ meetings: Meeting[] }>('/api/meetings');
      setMeetings(response.data.meetings);
      if (response.data.meetings.length > 0) {
        setSelectedMeeting(response.data.meetings[0]);
      }
    } catch (error) {
      console.error('Failed to load meetings:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectMeeting = (meeting: Meeting) => {
    setSelectedMeeting(meeting);
  };

  const handleCreateMeeting = async (title: string) => {
    try {
      const response = await axios.post<Meeting>('/api/meetings', null, {
        params: { title }
      });
      setMeetings([response.data, ...meetings]);
      setSelectedMeeting(response.data);
    } catch (error) {
      console.error('Failed to create meeting:', error);
    }
  };

  const handleRefreshMeetings = async () => {
    // 현재 선택된 회의의 ID를 저장
    const currentMeetingId = selectedMeeting?.id;
    
    // 모든 회의 목록 재조회
    try {
      const response = await axios.get<{ meetings: Meeting[] }>('/api/meetings');
      setMeetings(response.data.meetings);
      
      // 현재 선택된 회의를 다시 조회하여 업데이트된 정보로 교체
      if (currentMeetingId) {
        const updatedMeeting = response.data.meetings.find(m => m.id === currentMeetingId);
        if (updatedMeeting) {
          setSelectedMeeting(updatedMeeting);
        }
      }
    } catch (error) {
      console.error('Failed to refresh meetings:', error);
    }
  };

  const handleDeleteMeeting = (meetingId: string) => {
    setMeetings(meetings.filter(m => m.id !== meetingId));
    if (selectedMeeting?.id === meetingId) {
      const remaining = meetings.filter(m => m.id !== meetingId);
      setSelectedMeeting(remaining.length > 0 ? remaining[0] : null);
    }
  };

  if (loading) {
    return <div className="loading">로딩 중...</div>;
  }

  return (
    <div className="app">
      <div className="left-panel">
        <MeetingList
          meetings={meetings}
          selectedMeeting={selectedMeeting}
          onSelectMeeting={handleSelectMeeting}
          onCreateMeeting={handleCreateMeeting}
          onDeleteMeeting={handleDeleteMeeting}
        />
      </div>
      <div className="right-panel">
        {selectedMeeting ? (
          <MeetingDetail
            meeting={selectedMeeting}
            onUpdate={handleRefreshMeetings}
          />
        ) : (
          <div className="no-selection">회의를 선택해주세요</div>
        )}
      </div>
    </div>
  );
}

export default App;
