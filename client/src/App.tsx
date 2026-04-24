import { useEffect, useState, useRef } from 'react';
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
  domain_id?: number;
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
  const recorderStateRef = useRef<{ isRecording: boolean; stopRecordingWithoutUpload: () => void } | null>(null);

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

  const handleBeforeSelectMeeting = async (): Promise<boolean> => {
    // 현재 RecorderControls에서 녹취 중인지 확인
    if (recorderStateRef.current?.isRecording) {
      // 확인 대화 표시
      return new Promise((resolve) => {
        const userConfirmed = window.confirm(
          '다른 회의로 이동하는 경우, 녹취가 중단됩니다. 녹취를 중단하고, 다른 회의로 이동할까요?'
        );
        
        if (userConfirmed) {
          // "예"를 눌렀을 때 - 녹취 중단 후 이동
          recorderStateRef.current?.stopRecordingWithoutUpload();
          resolve(true);
        } else {
          // "아니오"를 눌렀을 때 - 계속 녹취
          resolve(false);
        }
      });
    }
    
    // 녹취 중이 아니면 그냥 이동
    return true;
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
    
    // 모든 회의 목록 재조회 (transcript 없이)
    try {
      const response = await axios.get<{ meetings: Meeting[] }>('/api/meetings');
      setMeetings(response.data.meetings);
      
      // 현재 선택된 회의의 상세정보만 다시 조회 (transcript 포함)
      if (currentMeetingId) {
        const updatedMeetingResponse = await axios.get<Meeting>(`/api/meetings/${currentMeetingId}`);
        setSelectedMeeting(updatedMeetingResponse.data);
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
          onBeforeSelectMeeting={handleBeforeSelectMeeting}
        />
      </div>
      <div className="right-panel">
        {selectedMeeting ? (
          <MeetingDetail
            meeting={selectedMeeting}
            onUpdate={handleRefreshMeetings}
            onSetRecorderState={(state) => {
              recorderStateRef.current = state;
            }}
          />
        ) : (
          <div className="no-selection">회의를 선택해주세요</div>
        )}
      </div>
    </div>
  );
}

export default App;
