import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { authApi } from '../api/authApi';
import '../styles/Auth.css';

export const ForgotPassword: React.FC = () => {
  const navigate = useNavigate();
  const [step, setStep] = useState<'email' | 'code' | 'reset'>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSendCode = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    try {
      await authApi.requestPasswordReset(email);
      setStep('code');
    } catch (err: any) {
      setError(err.response?.data?.detail || '인증코드 전송 실패');
    } finally {
      setIsLoading(false);
    }
  };

  const handleVerifyCode = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (code.length !== 6) {
      setError('인증코드는 6자리여야 합니다');
      return;
    }

    setIsLoading(true);

    try {
      await authApi.verifyCode(email, code);
      setStep('reset');
    } catch (err: any) {
      setError(err.response?.data?.detail || '인증코드 확인 실패');
    } finally {
      setIsLoading(false);
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    // 비밀번호 검증 먼저
    if (newPassword !== passwordConfirm) {
      setError('비밀번호가 일치하지 않습니다');
      return;
    }

    setIsLoading(true);

    try {
      await authApi.confirmPasswordReset(email, code, newPassword, passwordConfirm);
      navigate('/login', { state: { message: '비밀번호가 변경되었습니다. 로그인해주세요.' } });
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || '비밀번호 재설정 실패');
    } finally {
      setIsLoading(false);
    }
  };

  if (step === 'email') {
    return (
      <div className="auth-container">
        <div className="auth-box">
          <h1 className="auth-logo">reco-act</h1>
          <h2 className="auth-title">비밀번호 재설정</h2>

          {error && <div className="error-message">{error}</div>}

          <form onSubmit={handleSendCode} className="auth-form">
            <div className="form-group">
              <label htmlFor="email">가입된 이메일</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="example@company.com"
                required
                disabled={isLoading}
              />
            </div>

            <button type="submit" className="auth-button" disabled={isLoading}>
              {isLoading ? '전송 중...' : '인증코드 전송'}
            </button>
          </form>

          <div className="auth-divider">또는</div>

          <div className="auth-links">
            <Link to="/login" className="auth-link">로그인</Link>
            <Link to="/signup" className="auth-link">회원가입</Link>
          </div>
        </div>
      </div>
    );
  }

  if (step === 'code') {
    return (
      <div className="auth-container">
        <div className="auth-box">
          <h1 className="auth-logo">reco-act</h1>
          <h2 className="auth-title">이메일 인증</h2>
          <p className="auth-subtitle">{email}로 인증코드를 보냈습니다.</p>

          {error && <div className="error-message">{error}</div>}

          <form onSubmit={handleVerifyCode} className="auth-form">
            <div className="form-group">
              <label htmlFor="code">인증코드</label>
              <input
                id="code"
                type="text"
                inputMode="numeric"
                pattern="\d{6}"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="123456"
                maxLength={6}
                required
                autoFocus
                disabled={isLoading}
              />
              <p className="form-help">6자리 인증코드를 입력하세요</p>
            </div>

            <button
              type="submit"
              className="auth-button"
              disabled={isLoading || code.length !== 6}
            >
              {isLoading ? '확인 중...' : '확인'}
            </button>
          </form>

          <button
            type="button"
            className="auth-link-button"
            onClick={() => {
              setStep('email');
              setCode('');
              setError('');
            }}
            disabled={isLoading}
          >
            뒤로 가기
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-container">
      <div className="auth-box">
        <h1 className="auth-logo">reco-act</h1>
        <h2 className="auth-title">새 비밀번호 설정</h2>

        {error && <div className="error-message">{error}</div>}

        <form onSubmit={handleResetPassword} className="auth-form">
          <div className="form-group">
            <label htmlFor="newPassword">새 비밀번호</label>
            <input
              id="newPassword"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="8자 이상의 비밀번호"
              required
              minLength={8}
              disabled={isLoading}
            />
          </div>

          <div className="form-group">
            <label htmlFor="passwordConfirm">비밀번호 확인</label>
            <input
              id="passwordConfirm"
              type="password"
              value={passwordConfirm}
              onChange={(e) => setPasswordConfirm(e.target.value)}
              placeholder="비밀번호를 다시 입력하세요"
              required
              minLength={8}
              disabled={isLoading}
              className={passwordConfirm ? (newPassword === passwordConfirm ? 'input-success' : 'input-error') : ''}
            />
            {passwordConfirm && (
              <p className={newPassword === passwordConfirm ? 'form-success' : 'form-error'}>
                {newPassword === passwordConfirm ? '✓ 비밀번호가 일치합니다' : '✗ 비밀번호가 일치하지 않습니다'}
              </p>
            )}
          </div>

          <button
            type="submit"
            className="auth-button"
            disabled={
              isLoading ||
              newPassword.length < 8 ||
              passwordConfirm.length < 8 ||
              newPassword !== passwordConfirm
            }
          >
            {isLoading ? '변경 중...' : '비밀번호 변경'}
          </button>
        </form>

        <button
          type="button"
          className="auth-link-button"
          onClick={() => {
            setStep('email');
            setCode('');
            setNewPassword('');
            setPasswordConfirm('');
            setError('');
          }}
          disabled={isLoading}
        >
          뒤로 가기
        </button>
      </div>
    </div>
  );
};
