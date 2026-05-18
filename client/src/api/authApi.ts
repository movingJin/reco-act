import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import {
  clearAuth,
  getAuthToken,
  getRefreshToken,
  setTokens,
} from '../types/auth';

// API base URL
// - dev: 빈 문자열 → Vite proxy가 /api/ 처리
// - web prod: 빈 문자열 → nginx가 /api/ 프록시
// - mobile: 절대 URL (.env.mobile) → 모바일 앱에서 절대경로로 직접 호출
const baseURL = import.meta.env.VITE_API_BASE_URL || '';

const apiClient = axios.create({ baseURL });

// 토큰을 Authorization 헤더에 자동 첨부
apiClient.interceptors.request.use(async (config) => {
  const token = await getAuthToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 401 응답 시 외부에서 등록한 콜백 호출 (라우터 이동은 호출자가 처리)
let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (handler: () => void) => {
  onUnauthorized = handler;
};

// 동시에 여러 요청이 401을 받아도 refresh는 한 번만 실행되도록 단일 promise를 공유
let refreshPromise: Promise<string | null> | null = null;

const refreshAccessToken = async (): Promise<string | null> => {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      const refreshToken = await getRefreshToken();
      if (!refreshToken) return null;
      // refresh 호출은 인터셉터를 거치지 않는 raw axios로 (재귀 방지)
      const response = await axios.post(`${baseURL}/api/auth/refresh`, {
        refresh_token: refreshToken,
      });
      const { access_token, refresh_token } = response.data;
      await setTokens(access_token, refresh_token);
      return access_token;
    } catch {
      return null;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
};

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as
      | (InternalAxiosRequestConfig & { _retried?: boolean })
      | undefined;

    // refresh/login 자체의 401은 재시도하지 않음 (무한 루프 방지)
    const url = original?.url ?? '';
    const isAuthEndpoint =
      url.includes('/api/auth/refresh') || url.includes('/api/auth/login');

    if (error.response?.status === 401 && original && !original._retried && !isAuthEndpoint) {
      original._retried = true;
      const newAccess = await refreshAccessToken();
      if (newAccess) {
        original.headers.Authorization = `Bearer ${newAccess}`;
        return apiClient.request(original);
      }
      // refresh 실패 → 완전 로그아웃
      await clearAuth();
      onUnauthorized();
    }
    return Promise.reject(error);
  },
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

  // 토큰 갱신 (수동 호출용; 일반적으로 인터셉터가 자동 처리)
  refresh: (refreshToken: string) =>
    apiClient.post('/api/auth/refresh', { refresh_token: refreshToken }),

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

  // 로그아웃 (refresh token을 함께 보내 서버에서 무효화)
  logout: (refreshToken?: string | null) =>
    apiClient.post('/api/auth/logout', { refresh_token: refreshToken ?? null }),
};

export default apiClient;
