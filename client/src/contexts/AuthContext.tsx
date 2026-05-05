import React, { createContext, useContext, useState, useEffect } from 'react';
import { authApi } from '../api/authApi';
import {
  User,
  AuthContextType,
  UpdateProfileInput,
  getAuthFromStorage,
  setAuthToStorage,
  clearAuth,
  primeAuthToken,
} from '../types/auth';

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [token, setToken] = useState<string | null>(null);

  // 초기 로드 시 저장된 인증 정보 복원 (Preferences는 async)
  useEffect(() => {
    (async () => {
      const { token: storedToken, user: storedUser } = await getAuthFromStorage();
      if (storedToken && storedUser) {
        primeAuthToken(storedToken);
        setToken(storedToken);
        setUser(storedUser);
      } else {
        primeAuthToken(null);
      }
      setIsLoading(false);
    })();
  }, []);

  const login = async (email: string, password: string) => {
    try {
      const response = await authApi.login(email, password);
      const { access_token, user: userData } = response.data;
      await setAuthToStorage(access_token, userData);
      setToken(access_token);
      setUser(userData);
    } catch (error: any) {
      throw new Error(error.response?.data?.detail || '로그인 실패');
    }
  };

  const logout = async () => {
    await clearAuth();
    setToken(null);
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
      // 현재 토큰을 그대로 유지하면서 user 정보만 갱신
      if (token) {
        await setAuthToStorage(token, updatedUser);
      }
      setUser(updatedUser);
    } catch (error: any) {
      throw new Error(error.response?.data?.detail || '프로필 업데이트 실패');
    }
  };

  const deleteAccount = async (password: string) => {
    try {
      await authApi.deleteAccount(password);
      await clearAuth();
      setToken(null);
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
