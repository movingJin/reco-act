import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { apiClient } from '../api/authApi';
import { useModalBackButton } from '../utils/backButton';
import '../styles/Auth.css';

interface Domain {
  id: number;
  domain_name: string;
  keywords: string[];
}

export const Profile: React.FC = () => {
  const navigate = useNavigate();
  const { user, updateProfile, deleteAccount } = useAuth();
  const [editMode, setEditMode] = useState(false);
  const [name, setName] = useState(user?.name || '');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deletePassword, setDeletePassword] = useState('');

  const [domains, setDomains] = useState<Domain[]>([]);
  const [domainsLoading, setDomainsLoading] = useState(false);
  const [editDomainMode, setEditDomainMode] = useState(false);
  const [selectedDomainId, setSelectedDomainId] = useState<number | null>(
    user?.domain_id ?? null
  );
  const [isSavingDomain, setIsSavingDomain] = useState(false);

  // 계정 삭제 모달 닫기 (입력 비번도 함께 초기화)
  const closeDeleteModal = () => {
    setShowDeleteModal(false);
    setDeletePassword('');
  };

  // Android 백버튼으로 모달 닫기
  useModalBackButton(showDeleteModal, closeDeleteModal);

  useEffect(() => {
    const loadDomains = async () => {
      setDomainsLoading(true);
      try {
        const response = await apiClient.get<Domain[]>('/api/domains');
        setDomains(response.data);
      } catch (err) {
        console.error('Failed to load domains:', err);
      } finally {
        setDomainsLoading(false);
      }
    };
    loadDomains();
  }, []);

  useEffect(() => {
    setSelectedDomainId(user?.domain_id ?? null);
  }, [user?.domain_id]);

  const currentDomainName = (() => {
    if (!user?.domain_id) return null;
    const found = domains.find((d) => d.id === user.domain_id);
    return found ? found.domain_name : null;
  })();

  const handleUpdateProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setIsLoading(true);

    try {
      await updateProfile({ name });
      setSuccess('프로필이 업데이트되었습니다');
      setEditMode(false);
    } catch (err: any) {
      setError(err.message || '프로필 업데이트 실패');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSaveDomain = async () => {
    setError('');
    setSuccess('');
    setIsSavingDomain(true);

    try {
      await updateProfile({ domain_id: selectedDomainId });
      setSuccess('기본 도메인이 업데이트되었습니다');
      setEditDomainMode(false);
    } catch (err: any) {
      setError(err.message || '기본 도메인 업데이트 실패');
    } finally {
      setIsSavingDomain(false);
    }
  };

  const handleCancelDomain = () => {
    setSelectedDomainId(user?.domain_id ?? null);
    setEditDomainMode(false);
    setError('');
  };

  const handleDeleteAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setIsLoading(true);

    try {
      await deleteAccount(deletePassword);
      navigate('/login', { state: { message: '계정이 삭제되었습니다.' } });
    } catch (err: any) {
      setError(err.message || '계정 삭제 실패');
    } finally {
      setIsLoading(false);
      closeDeleteModal();
    }
  };

  if (!user) {
    return null;
  }

  return (
    <div className="profile-container">
      <div className="profile-box">
        <div className="profile-header">
          <button
            type="button"
            className="profile-back-button"
            onClick={() => navigate(-1)}
            title="이전 화면으로"
          >
            ← 뒤로
          </button>
          <h2 className="profile-title">프로필 관리</h2>
        </div>

        {error && <div className="error-message">{error}</div>}
        {success && <div className="success-message">{success}</div>}

        <div className="profile-content">
          <div className="profile-section">
            <h3>계정 정보</h3>
            <div className="profile-info">
              <div className="info-item">
                <label>이메일</label>
                <p>{user.email}</p>
              </div>

              <div className="info-item">
                <label>이름</label>
                {editMode ? (
                  <form onSubmit={handleUpdateProfile} className="edit-form">
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      minLength={2}
                      required
                      disabled={isLoading}
                    />
                    <div className="edit-buttons">
                      <button type="submit" disabled={isLoading}>
                        저장
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setEditMode(false);
                          setName(user.name);
                          setError('');
                        }}
                        disabled={isLoading}
                      >
                        취소
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="name-display">
                    <p>{user.name}</p>
                    <button onClick={() => setEditMode(true)} className="edit-button">
                      수정
                    </button>
                  </div>
                )}
              </div>

              <div className="info-item">
                <label>기본 도메인</label>
                {editDomainMode ? (
                  <div className="edit-form">
                    <select
                      value={selectedDomainId ?? ''}
                      onChange={(e) =>
                        setSelectedDomainId(
                          e.target.value === '' ? null : Number(e.target.value)
                        )
                      }
                      disabled={isSavingDomain || domainsLoading}
                      className="domain-select"
                    >
                      <option value="">선택 안 함</option>
                      {domains.map((domain) => (
                        <option key={domain.id} value={domain.id}>
                          {domain.domain_name}
                        </option>
                      ))}
                    </select>
                    <div className="edit-buttons">
                      <button
                        type="button"
                        onClick={handleSaveDomain}
                        disabled={isSavingDomain}
                      >
                        {isSavingDomain ? '저장 중...' : '저장'}
                      </button>
                      <button
                        type="button"
                        onClick={handleCancelDomain}
                        disabled={isSavingDomain}
                      >
                        취소
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="name-display">
                    <p>
                      {domainsLoading
                        ? '불러오는 중...'
                        : currentDomainName ?? '미설정'}
                    </p>
                    <button
                      onClick={() => setEditDomainMode(true)}
                      className="edit-button"
                      disabled={domainsLoading}
                    >
                      수정
                    </button>
                  </div>
                )}
              </div>

              {user.created_at && (
                <div className="info-item">
                  <label>가입일</label>
                  <p>{new Date(user.created_at).toLocaleDateString('ko-KR')}</p>
                </div>
              )}
            </div>
          </div>

          <div className="profile-section actions">
            <h3>계정 관리</h3>
            <div className="action-buttons">
              <button onClick={() => setShowDeleteModal(true)} className="delete-button">
                계정 삭제
              </button>
            </div>
          </div>
        </div>
      </div>

      {showDeleteModal && (
        <div className="modal-overlay">
          <div className="modal-box">
            <h3>계정 삭제</h3>
            <p className="modal-warning">
              정말로 계정을 삭제하시겠습니까?<br />
              이 작업은 되돌릴 수 없습니다.
            </p>

            {error && <div className="error-message">{error}</div>}

            <form onSubmit={handleDeleteAccount} className="auth-form">
              <div className="form-group">
                <label htmlFor="deletePassword">비밀번호</label>
                <input
                  id="deletePassword"
                  type="password"
                  value={deletePassword}
                  onChange={(e) => setDeletePassword(e.target.value)}
                  placeholder="비밀번호를 입력하세요"
                  required
                  disabled={isLoading}
                  autoFocus
                />
              </div>

              <div className="modal-buttons">
                <button type="submit" className="delete-button" disabled={isLoading}>
                  {isLoading ? '삭제 중...' : '계정 삭제'}
                </button>
                <button
                  type="button"
                  className="cancel-button"
                  onClick={() => {
                    closeDeleteModal();
                    setError('');
                  }}
                  disabled={isLoading}
                >
                  취소
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
