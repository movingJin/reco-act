import React, { createContext, useContext, useState, useEffect } from 'react';
import { authApi } from '../api/authApi';
import {
  User,
  AuthContextType,
  UpdateProfileInput,
  getAuthFromStorage,
  getRefreshToken,
  setAuthToStorage,
  setUserToStorage,
  clearAuth,
  primeAuthTokens,
} from '../types/auth';

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // 초기 로드 시 저장된 인증 정보 복원 (Preferences는 async)
  useEffect(() => {
    (async () => {
      const { token: storedToken, refreshToken: storedRefresh, user: storedUser } =
        await getAuthFromStorage();
      if (storedToken && storedRefresh && storedUser) {
        primeAuthTokens(storedToken, storedRefresh);
        setUser(storedUser);
      } else {
        primeAuthTokens(null, null);
      }
      setIsLoading(false);
    })();
  }, []);

  const login = async (email: string, password: string) => {
    try {
      const response = await authApi.login(email, password);
      const { access_token, refresh_token, user: userData } = response.data;
      await setAuthToStorage(access_token, refresh_token, userData);
      setUser(userData);
    } catch (error: any) {
      throw new Error(error.response?.data?.detail || '로그인 실패');
    }
  };

  const logout = async () => {
    // 서버에 refresh token 무효화 요청 (실패해도 로컬 정리는 계속)
    try {
      const refreshToken = await getRefreshToken();
      await authApi.logout(refreshToken);
    } catch {
      // 네트워크 실패 무시
    }
    await clearAuth();
    setUser(null);
  };

  const signup = async (email: string, name: string, password: string, passwordConfirm: string, code: string) => {
    try {
      await authApi.signup(email, name, password, passwordConfirm, code);
    } catch (error: any) {
      throw new Error(error.response?.data?.detail || '회원가입 실패');
    }
  };

  const updateProfile = async (input: UpdateProfileInput) => {
    try {
      const response = await authApi.updateProfile(input);
      const updatedUser = response.data.user;
      // 토큰은 그대로 유지, user 정보만 저장
      await setUserToStorage(updatedUser);
      setUser(updatedUser);
    } catch (error: any) {
      throw new Error(error.response?.data?.detail || '프로필 업데이트 실패');
    }
  };

  const deleteAccount = async (password: string) => {
    try {
      await authApi.deleteAccount(password);
      await clearAuth();
      setUser(null);
    } catch (error: any) {
      throw new Error(error.response?.data?.detail || '계정 삭제 실패');
    }
  };

  const value: AuthContextType = {
    user,
    isLoading,
    login,
    logout,
    signup,
    updateProfile,
    deleteAccount,
    isAuthenticated: !!user,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
