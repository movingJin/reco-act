import React, { createContext, useContext, useState, useEffect } from 'react';
import { authApi } from '../api/authApi';
import { User, AuthContextType, UpdateProfileInput, getAuthFromStorage, setAuthToStorage, clearAuthFromStorage } from '../types/auth';

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // 초기 로드 시 저장된 인증 정보 복원
  useEffect(() => {
    const { token, user: storedUser } = getAuthFromStorage();
    if (token && storedUser) {
      setUser(storedUser);
    }
    setIsLoading(false);
  }, []);

  const login = async (email: string, password: string) => {
    try {
      const response = await authApi.login(email, password);
      const { access_token, user: userData } = response.data;
      setAuthToStorage(access_token, userData);
      setUser(userData);
    } catch (error: any) {
      throw new Error(error.response?.data?.detail || '로그인 실패');
    }
  };

  const logout = () => {
    clearAuthFromStorage();
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
      setAuthToStorage(localStorage.getItem('access_token')!, updatedUser);
      setUser(updatedUser);
    } catch (error: any) {
      throw new Error(error.response?.data?.detail || '프로필 업데이트 실패');
    }
  };

  const deleteAccount = async (password: string) => {
    try {
      await authApi.deleteAccount(password);
      clearAuthFromStorage();
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
