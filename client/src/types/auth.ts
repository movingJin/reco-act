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
  logout: () => void;
  signup: (email: string, name: string, password: string, passwordConfirm: string, code: string) => Promise<void>;
  updateProfile: (input: UpdateProfileInput) => Promise<void>;
  deleteAccount: (password: string) => Promise<void>;
  isAuthenticated: boolean;
}

// 인증 상태
export const getAuthFromStorage = (): { token: string | null; user: User | null } => {
  const token = localStorage.getItem('access_token');
  const userStr = localStorage.getItem('user');
  const user = userStr ? JSON.parse(userStr) : null;
  return { token, user };
};

export const setAuthToStorage = (token: string, user: User) => {
  localStorage.setItem('access_token', token);
  localStorage.setItem('user', JSON.stringify(user));
};

export const clearAuthFromStorage = () => {
  localStorage.removeItem('access_token');
  localStorage.removeItem('user');
};
