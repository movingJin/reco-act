import { Preferences } from '@capacitor/preferences';

// 사용자 정보
export interface User {
  email: string;
  name: string;
  created_at?: string;
  domain_id?: number | null;
}

// 프로필 업데이트 입력 (변경할 필드만 포함)
export interface UpdateProfileInput {
  name?: string;
  domain_id?: number | null;
}

// 인증 컨텍스트
export interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  signup: (email: string, name: string, password: string, passwordConfirm: string, code: string) => Promise<void>;
  updateProfile: (input: UpdateProfileInput) => Promise<void>;
  deleteAccount: (password: string) => Promise<void>;
  isAuthenticated: boolean;
}

const TOKEN_KEY = 'access_token';
const REFRESH_TOKEN_KEY = 'refresh_token';
const USER_KEY = 'user';

// 토큰 캐시 (axios 인터셉터의 hot path 최적화)
// null: 미초기화, string: 캐시된 값, '': 토큰 없음을 명시
let cachedAccessToken: string | null = null;
let cachedRefreshToken: string | null = null;

// 인증 정보 조회
export const getAuthFromStorage = async (): Promise<{
  token: string | null;
  refreshToken: string | null;
  user: User | null;
}> => {
  const { value: token } = await Preferences.get({ key: TOKEN_KEY });
  const { value: refreshToken } = await Preferences.get({ key: REFRESH_TOKEN_KEY });
  const { value: userStr } = await Preferences.get({ key: USER_KEY });
  const user = userStr ? JSON.parse(userStr) : null;
  return { token, refreshToken, user };
};

// 로그인 시 access/refresh/user 일괄 저장
export const setAuthToStorage = async (
  token: string,
  refreshToken: string,
  user: User,
): Promise<void> => {
  await Preferences.set({ key: TOKEN_KEY, value: token });
  await Preferences.set({ key: REFRESH_TOKEN_KEY, value: refreshToken });
  await Preferences.set({ key: USER_KEY, value: JSON.stringify(user) });
  cachedAccessToken = token;
  cachedRefreshToken = refreshToken;
};

// refresh 응답으로 토큰만 갱신
export const setTokens = async (token: string, refreshToken: string): Promise<void> => {
  await Preferences.set({ key: TOKEN_KEY, value: token });
  await Preferences.set({ key: REFRESH_TOKEN_KEY, value: refreshToken });
  cachedAccessToken = token;
  cachedRefreshToken = refreshToken;
};

// 프로필 갱신 시 user만 저장
export const setUserToStorage = async (user: User): Promise<void> => {
  await Preferences.set({ key: USER_KEY, value: JSON.stringify(user) });
};

// 인증 정보 제거
export const clearAuthFromStorage = async (): Promise<void> => {
  await Preferences.remove({ key: TOKEN_KEY });
  await Preferences.remove({ key: REFRESH_TOKEN_KEY });
  await Preferences.remove({ key: USER_KEY });
};

// axios 요청 인터셉터에서 사용. 첫 호출 후엔 캐시에서 빠르게 반환.
export const getAuthToken = async (): Promise<string | null> => {
  if (cachedAccessToken !== null) return cachedAccessToken || null;
  const { value } = await Preferences.get({ key: TOKEN_KEY });
  cachedAccessToken = value ?? '';
  return value;
};

export const getRefreshToken = async (): Promise<string | null> => {
  if (cachedRefreshToken !== null) return cachedRefreshToken || null;
  const { value } = await Preferences.get({ key: REFRESH_TOKEN_KEY });
  cachedRefreshToken = value ?? '';
  return value;
};

// 부트스트랩 시 캐시 시드 (Preferences 다시 읽지 않도록)
export const primeAuthTokens = (token: string | null, refreshToken: string | null) => {
  cachedAccessToken = token ?? '';
  cachedRefreshToken = refreshToken ?? '';
};

// 로그아웃 / refresh 실패: 캐시와 저장소 모두 정리
export const clearAuth = async (): Promise<void> => {
  cachedAccessToken = '';
  cachedRefreshToken = '';
  await clearAuthFromStorage();
};
