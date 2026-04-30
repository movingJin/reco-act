import axios from 'axios';

// API 인스턴스 생성 (현재 origin 기준 상대 경로 사용 → dev: Vite proxy, prod: nginx proxy 가 처리)
const apiClient = axios.create();

// 토큰을 Authorization 헤더에 자동 첨부
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 401 응답 시 저장된 토큰을 제거하고 로그인 페이지로 이동
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token');
      localStorage.removeItem('user');
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

// 모든 컴포넌트가 공유하는 인증된 axios 인스턴스
export { apiClient };

export const authApi = {
  // 이메일 중복 확인
  checkEmail: (email: string) =>
    apiClient.post('/api/auth/check-email', { email }),

  // 인증코드 전송
  sendVerificationCode: (email: string) =>
    apiClient.post('/api/auth/send-verification-code', { email }),

  // 인증코드 확인
  verifyCode: (email: string, code: string) =>
    apiClient.post('/api/auth/verify-code', { email, code }),

  // 회원가입
  signup: (email: string, name: string, password: string, passwordConfirm: string, code: string) =>
    apiClient.post('/api/auth/signup', {
      email,
      name,
      password,
      password_confirm: passwordConfirm,
      code,
    }),

  // 로그인
  login: (email: string, password: string) =>
    apiClient.post('/api/auth/login', { email, password }),

  // 프로필 조회
  getProfile: () => apiClient.get('/api/auth/me'),

  // 프로필 업데이트 (이름/기본 도메인) - 전달된 필드만 변경됩니다
  updateProfile: (input: { name?: string; domain_id?: number | null }) =>
    apiClient.put('/api/auth/profile', input),

  // 비밀번호 재설정 요청
  requestPasswordReset: (email: string) =>
    apiClient.post('/api/auth/password-reset', { email }),

  // 비밀번호 재설정 확인
  confirmPasswordReset: (email: string, code: string, newPassword: string, passwordConfirm: string) =>
    apiClient.post('/api/auth/password-reset-confirm', {
      email,
      code,
      new_password: newPassword,
      password_confirm: passwordConfirm,
    }),

  // 계정 삭제
  deleteAccount: (password: string) =>
    apiClient.delete('/api/auth/account', { data: { password } }),

  // 로그아웃
  logout: () => apiClient.post('/api/auth/logout'),
};

export default apiClient;
