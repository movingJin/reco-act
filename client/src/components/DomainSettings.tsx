import { useState, useEffect } from 'react';
import axios from 'axios';
import '../styles/DomainSettings.css';

interface Domain {
  domain_name: string;
  keywords: string[];
}

interface DomainSettingsProps {
  isOpen: boolean;
  onClose: () => void;
  onUpdate: () => void;
}

function DomainSettings({ isOpen, onClose, onUpdate }: DomainSettingsProps) {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [selectedDomain, setSelectedDomain] = useState<Domain | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [editMode, setEditMode] = useState<'view' | 'create' | 'edit'>('view');
  const [formData, setFormData] = useState<Domain>({ domain_name: '', keywords: [] });
  const [newKeyword, setNewKeyword] = useState('');
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    if (isOpen) {
      loadDomains();
    }
  }, [isOpen]);

  const loadDomains = async () => {
    setIsLoading(true);
    try {
      const response = await axios.get<Domain[]>('/api/domains');
      setDomains(response.data);
      if (response.data.length > 0) {
        setSelectedDomain(response.data[0]);
      }
      setErrorMessage('');
    } catch (error) {
      console.error('Failed to load domains:', error);
      setErrorMessage('도메인 목록을 불러올 수 없습니다');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSelectDomain = (domain: Domain) => {
    setSelectedDomain(domain);
    setEditMode('view');
    setErrorMessage('');
  };

  const handleCreateClick = () => {
    setFormData({ domain_name: '', keywords: [] });
    setNewKeyword('');
    setEditMode('create');
    setErrorMessage('');
  };

  const handleEditClick = () => {
    if (selectedDomain) {
      setFormData({ ...selectedDomain });
      setNewKeyword('');
      setEditMode('edit');
      setErrorMessage('');
    }
  };

  const handleAddKeyword = () => {
    if (newKeyword.trim() && !formData.keywords.includes(newKeyword.trim())) {
      const updatedKeywords = [...formData.keywords, newKeyword.trim()].sort();
      setFormData({ ...formData, keywords: updatedKeywords });
      setNewKeyword('');
    }
  };

  const handleRemoveKeyword = (keyword: string) => {
    setFormData({
      ...formData,
      keywords: formData.keywords.filter(k => k !== keyword),
    });
  };

  const handleSave = async () => {
    if (!formData.domain_name.trim()) {
      setErrorMessage('도메인 이름을 입력해주세요');
      return;
    }

    if (formData.keywords.length === 0) {
      setErrorMessage('최소 1개 이상의 키워드를 추가해주세요');
      return;
    }

    setIsSaving(true);
    try {
      if (editMode === 'create') {
        await axios.post('/api/domains', formData);
      } else if (editMode === 'edit' && selectedDomain) {
        await axios.put(`/api/domains/${selectedDomain.domain_name}`, formData);
      }

      setEditMode('view');
      setErrorMessage('');
      await loadDomains();
      onUpdate();
      alert(editMode === 'create' ? '도메인이 생성되었습니다' : '도메인이 수정되었습니다');
    } catch (error: any) {
      const message = error.response?.data?.detail || '도메인 저장에 실패했습니다';
      setErrorMessage(message);
      console.error('Failed to save domain:', error);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!selectedDomain) return;

    if (!window.confirm(`'${selectedDomain.domain_name}' 도메인을 삭제하시겠습니까?`)) {
      return;
    }

    setIsSaving(true);
    try {
      await axios.delete(`/api/domains/${selectedDomain.domain_name}`);
      setErrorMessage('');
      await loadDomains();
      onUpdate();
      alert('도메인이 삭제되었습니다');
      setEditMode('view');
    } catch (error: any) {
      const message = error.response?.data?.detail || '도메인 삭제에 실패했습니다';
      setErrorMessage(message);
      console.error('Failed to delete domain:', error);
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    setEditMode('view');
    setErrorMessage('');
    setNewKeyword('');
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content domain-settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>도메인 설정</h2>
          <button className="btn-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          {isLoading ? (
            <div className="loading">도메인을 불러오는 중...</div>
          ) : (
            <div className="domain-settings-container">
              {/* Domain List */}
              <div className="domain-list-section">
                <h3>도메인 목록</h3>
                <div className="domain-list">
                  {domains.length === 0 ? (
                    <p className="empty-message">등록된 도메인이 없습니다</p>
                  ) : (
                    domains.map((domain) => (
                      <div
                        key={domain.domain_name}
                        className={`domain-item ${
                          selectedDomain?.domain_name === domain.domain_name ? 'selected' : ''
                        }`}
                        onClick={() => handleSelectDomain(domain)}
                      >
                        <span className="domain-name">{domain.domain_name}</span>
                        <span className="keyword-count">({domain.keywords.length})</span>
                      </div>
                    ))
                  )}
                </div>

                <button className="btn-primary" onClick={handleCreateClick}>
                  + 새 도메인
                </button>
              </div>

              {/* Domain Editor */}
              <div className="domain-editor-section">
                {editMode === 'view' && selectedDomain ? (
                  <div className="view-mode">
                    <h3>{selectedDomain.domain_name}</h3>

                    <div className="keywords-display">
                      <h4>키워드 ({selectedDomain.keywords.length}개)</h4>
                      <div className="keywords-list">
                        {selectedDomain.keywords.map((keyword, index) => (
                          <span key={index} className="keyword-tag">
                            {keyword}
                          </span>
                        ))}
                      </div>
                    </div>

                    {errorMessage && <div className="error-message">{errorMessage}</div>}

                    <div className="action-buttons">
                      <button
                        className="btn-secondary"
                        onClick={handleEditClick}
                        disabled={isSaving}
                      >
                        수정
                      </button>
                      <button
                        className="btn-danger"
                        onClick={handleDelete}
                        disabled={isSaving}
                      >
                        삭제
                      </button>
                    </div>
                  </div>
                ) : editMode !== 'view' ? (
                  <div className="edit-mode">
                    <h3>{editMode === 'create' ? '새 도메인 생성' : '도메인 수정'}</h3>

                    <div className="form-group">
                      <label>도메인 이름</label>
                      <input
                        type="text"
                        value={formData.domain_name}
                        onChange={(e) =>
                          setFormData({ ...formData, domain_name: e.target.value })
                        }
                        placeholder="예: 제조, 통신"
                        disabled={editMode === 'edit'}
                        className={editMode === 'edit' ? 'disabled' : ''}
                      />
                      {editMode === 'edit' && (
                        <small>도메인 이름은 수정할 수 없습니다</small>
                      )}
                    </div>

                    <div className="form-group">
                      <label>키워드</label>
                      <div className="keyword-input-group">
                        <input
                          type="text"
                          value={newKeyword}
                          onChange={(e) => setNewKeyword(e.target.value)}
                          onKeyPress={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              handleAddKeyword();
                            }
                          }}
                          placeholder="키워드 입력 후 Enter"
                        />
                        <button
                          className="btn-add-keyword"
                          onClick={handleAddKeyword}
                          disabled={!newKeyword.trim()}
                        >
                          추가
                        </button>
                      </div>

                      <div className="keywords-edit-list">
                        {formData.keywords.map((keyword, index) => (
                          <div key={index} className="keyword-item">
                            <span>{keyword}</span>
                            <button
                              className="btn-remove-keyword"
                              onClick={() => handleRemoveKeyword(keyword)}
                            >
                              ×
                            </button>
                          </div>
                        ))}
                      </div>

                      <small>키워드는 자동으로 오름차순 정렬됩니다 ({formData.keywords.length}개)</small>
                    </div>

                    {errorMessage && <div className="error-message">{errorMessage}</div>}

                    <div className="action-buttons">
                      <button
                        className="btn-primary"
                        onClick={handleSave}
                        disabled={isSaving}
                      >
                        {isSaving ? '저장 중...' : '저장'}
                      </button>
                      <button
                        className="btn-secondary"
                        onClick={handleCancel}
                        disabled={isSaving}
                      >
                        취소
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="empty-state">
                    도메인을 선택하거나 새 도메인을 생성해주세요
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default DomainSettings;
