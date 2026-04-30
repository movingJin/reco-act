import React, { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { authApi } from '../api/authApi';
import '../styles/Auth.css';

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const Signup: React.FC = () => {
  const navigate = useNavigate();
  const [step, setStep] = useState<'info' | 'verification'>('info');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [verificationCode, setVerificationCode] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isCheckingEmail, setIsCheckingEmail] = useState(false);
  const [isEmailAvailable, setIsEmailAvailable] = useState(false);
  const [emailCheckMessage, setEmailCheckMessage] = useState('');
  const [emailCheckSuccess, setEmailCheckSuccess] = useState(false);

  const isEmailFormatValid = EMAIL_REGEX.test(email);
  const isPasswordMatch = password.length >= 8 && password === passwordConfirm;
  const canSendCode = isEmailAvailable && name.length >= 2 && isPasswordMatch;

  const handleEmailChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setEmail(e.target.value);
    setIsEmailAvailable(false);
    setEmailCheckMessage('');
    setEmailCheckSuccess(false);
  };

  const handleCheckEmail = async () => {
    setError('');
    setEmailCheckMessage('');
    setEmailCheckSuccess(false);

    if (!isEmailFormatValid) {
      return;
    }

    setIsCheckingEmail(true);
    try {
      await authApi.checkEmail(email);
      setIsEmailAvailable(true);
      setEmailCheckSuccess(true);
      setEmailCheckMessage('✓ 사용 가능한 이메일입니다');
    } catch (err: any) {
      setIsEmailAvailable(false);
      setEmailCheckSuccess(false);
      setEmailCheckMessage(err.response?.data?.detail || '이메일 확인 실패');
    } finally {
      setIsCheckingEmail(false);
    }
  };

  const handleSendCode = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    try {
      await authApi.sendVerificationCode(email);
      setStep('verification');
    } catch (err: any) {
      setError(err.response?.data?.detail || '인증코드 전송 실패');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    if (verificationCode.length !== 6) {
      setError('인증코드는 6자리여야 합니다');
      setIsLoading(false);
      return;
    }

    try {
      await authApi.signup(email, name, password, passwordConfirm, verificationCode);
      navigate('/login', { state: { message: '회원가입이 완료되었습니다. 로그인해주세요.' } });
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || '회원가입 실패');
    } finally {
      setIsLoading(false);
    }
  };

  if (step === 'info') {
    return (
      <div className="auth-container">
        <div className="auth-box">
          <h1 className="auth-logo">reco-act</h1>
          <h2 className="auth-title">회원가입</h2>

          {error && <div className="error-message">{error}</div>}

          <form onSubmit={handleSendCode} className="auth-form">
            <div className="form-group">
              <label htmlFor="email">이메일</label>
              <div className="input-with-button">
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={handleEmailChange}
                  placeholder="example@company.com"
                  required
                  disabled={isLoading || isCheckingEmail}
                  className={
                    email
                      ? isEmailAvailable
                        ? 'input-success'
                        : isEmailFormatValid
                        ? ''
                        : 'input-error'
                      : ''
                  }
                />
                <button
                  type="button"
                  className="inline-button"
                  onClick={handleCheckEmail}
                  disabled={!isEmailFormatValid || isCheckingEmail || isLoading || isEmailAvailable}
                >
                  {isCheckingEmail ? '확인 중...' : isEmailAvailable ? '확인 완료' : '이메일 확인'}
                </button>
              </div>
              {email && !isEmailFormatValid && (
                <p className="form-error">✗ 올바른 이메일 형식이 아닙니다</p>
              )}
              {emailCheckMessage && (
                <p className={emailCheckSuccess ? 'form-success' : 'form-error'}>
                  {emailCheckMessage}
                </p>
              )}
            </div>

            <div className="form-group">
              <label htmlFor="name">이름</label>
              <input
                id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="홍길동"
                required
                minLength={2}
                disabled={isLoading}
              />
            </div>

            <div className="form-group">
              <label htmlFor="password">비밀번호</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
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
                className={passwordConfirm ? (password === passwordConfirm ? 'input-success' : 'input-error') : ''}
              />
              {passwordConfirm && (
                <p className={password === passwordConfirm ? 'form-success' : 'form-error'}>
                  {password === passwordConfirm ? '✓ 비밀번호가 일치합니다' : '✗ 비밀번호가 일치하지 않습니다'}
                </p>
              )}
            </div>

            <button type="submit" className="auth-button" disabled={isLoading || !canSendCode}>
              {isLoading ? '전송 중...' : '인증코드 전송'}
            </button>
          </form>

          <div className="auth-divider">또는</div>

          <div className="auth-links">
            <Link to="/login" className="auth-link">로그인</Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-container">
      <div className="auth-box">
        <h1 className="auth-logo">reco-act</h1>
        <h2 className="auth-title">이메일 인증</h2>
        <p className="auth-subtitle">{email}로 인증코드를 보냈습니다.</p>

        {error && <div className="error-message">{error}</div>}

        <form onSubmit={handleSignup} className="auth-form">
          <div className="form-group">
            <label htmlFor="verificationCode">인증코드</label>
            <input
              id="verificationCode"
              type="text"
              inputMode="numeric"
              pattern="\d{6}"
              value={verificationCode}
              onChange={(e) => setVerificationCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              placeholder="123456"
              maxLength={6}
              required
              disabled={isLoading}
              autoFocus
            />
            <p className="form-help">6자리 인증코드를 입력하세요</p>
          </div>

          <button
            type="submit"
            className="auth-button"
            disabled={isLoading || verificationCode.length !== 6}
          >
            {isLoading ? '확인 중...' : '회원가입 완료'}
          </button>
        </form>

        <button
          type="button"
          className="auth-link-button"
          onClick={() => {
            setStep('info');
            setVerificationCode('');
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
