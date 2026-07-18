import { BrowserRouter as Router, Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { useEffect, useState, useRef } from 'react';
import { apiClient, setUnauthorizedHandler } from './api/authApi';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ProtectedRoute } from './components/ProtectedRoute';
import MeetingList from './components/MeetingList';
import MeetingDetail from './components/MeetingDetail';
import { Login } from './pages/Login';
import { Signup } from './pages/Signup';
import { ForgotPassword } from './pages/ForgotPassword';
import { Profile } from './pages/Profile';
import { PrivacyPolicy } from './pages/PrivacyPolicy';
import { confirmDialog } from './utils/dialog';
import { useAndroidBackButton } from './utils/backButton';
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

// 모바일에서 list/detail을 한 번에 한 패널만 표시 (768px 미만)
function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState<boolean>(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
  );
  useEffect(() => {
    const mql = window.matchMedia('(max-width: 767px)');
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);
  return isMobile;
}

function MeetingApp() {
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [selectedMeeting, setSelectedMeeting] = useState<Meeting | null>(null);
  const [loading, setLoading] = useState(true);
  const [domainsVersion, setDomainsVersion] = useState(0);
  const recorderStateRef = useRef<{ isRecording: boolean; stopRecordingWithoutUpload: () => void } | null>(null);
  const isMobile = useIsMobile();

  useEffect(() => {
    loadMeetings();
  }, []);

  const loadMeetings = async () => {
    try {
      const response = await apiClient.get<{ meetings: Meeting[] }>('/api/meetings');
      setMeetings(response.data.meetings);
      // 모바일에선 자동 선택하지 않고 목록 화면을 먼저 보여준다.
      if (!isMobile && response.data.meetings.length > 0) {
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
      const userConfirmed = await confirmDialog(
        '다른 회의로 이동하는 경우, 녹취가 중단됩니다. 녹취를 중단하고, 다른 회의로 이동할까요?'
      );
      if (userConfirmed) {
        recorderStateRef.current?.stopRecordingWithoutUpload();
        return true;
      }
      return false;
    }

    // 녹취 중이 아니면 그냥 이동
    return true;
  };

  const handleCreateMeeting = async (title: string) => {
    try {
      const response = await apiClient.post<Meeting>('/api/meetings', null, {
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
      const response = await apiClient.get<{ meetings: Meeting[] }>('/api/meetings');
      setMeetings(response.data.meetings);

      // 현재 선택된 회의의 상세정보만 다시 조회 (transcript 포함)
      if (currentMeetingId) {
        const updatedMeetingResponse = await apiClient.get<Meeting>(`/api/meetings/${currentMeetingId}`);
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
      setSelectedMeeting(!isMobile && remaining.length > 0 ? remaining[0] : null);
    }
  };

  if (loading) {
    return <div className="loading">로딩 중...</div>;
  }

  // 모바일: selectedMeeting이 있으면 detail, 없으면 list만 표시.
  // 데스크톱: 둘 다 표시.
  const showList = !isMobile || !selectedMeeting;
  const showDetail = !isMobile || !!selectedMeeting;

  return (
    <div className="app">
      <div className={`left-panel ${showList ? '' : 'hidden'}`}>
        <MeetingList
          meetings={meetings}
          selectedMeeting={selectedMeeting}
          onSelectMeeting={handleSelectMeeting}
          onCreateMeeting={handleCreateMeeting}
          onDeleteMeeting={handleDeleteMeeting}
          onBeforeSelectMeeting={handleBeforeSelectMeeting}
          onDomainsUpdate={() => {
            setDomainsVersion((v) => v + 1);
            handleRefreshMeetings();
          }}
        />
      </div>
      <div className={`right-panel ${showDetail ? '' : 'hidden'}`}>
        {selectedMeeting ? (
          <>
            {isMobile && (
              <button
                className="mobile-back-button"
                onClick={async () => {
                  const ok = await handleBeforeSelectMeeting();
                  if (ok) setSelectedMeeting(null);
                }}
              >
                ← 목록
              </button>
            )}
            <MeetingDetail
              meeting={selectedMeeting}
              onUpdate={handleRefreshMeetings}
              onSetRecorderState={(state) => {
                recorderStateRef.current = state;
              }}
              domainsVersion={domainsVersion}
            />
          </>
        ) : (
          <div className="no-selection">회의를 선택해주세요</div>
        )}
      </div>
    </div>
  );
}

// AuthShell: 401 응답 시 로그아웃하고 라우터로 /login 이동시키는 핸들러를 등록한다.
// AuthProvider 내부에서 useAuth()를, Router 내부에서 useNavigate()를 사용해야 하므로
// 이 컴포넌트는 두 Provider 모두의 자식으로 배치된다.
function AuthShell({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const { logout } = useAuth();

  useAndroidBackButton();

  useEffect(() => {
    setUnauthorizedHandler(() => {
      void (async () => {
        await logout();
        navigate('/login', { replace: true });
      })();
    });
  }, [navigate, logout]);

  return <>{children}</>;
}

function App() {
  return (
    <Router>
      <AuthProvider>
        <AuthShell>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Signup />} />
            <Route path="/forgot-password" element={<ForgotPassword />} />
            <Route path="/privacy" element={<PrivacyPolicy />} />
            <Route path="/profile" element={<ProtectedRoute><Profile /></ProtectedRoute>} />
            <Route path="/meetings" element={<ProtectedRoute><MeetingApp /></ProtectedRoute>} />
            <Route path="*" element={<Navigate to="/meetings" replace />} />
          </Routes>
        </AuthShell>
      </AuthProvider>
    </Router>
  );
}

export default App;
