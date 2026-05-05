import { useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Capacitor } from '@capacitor/core';
import { App as CapacitorApp } from '@capacitor/app';
import { Toast } from '@capacitor/toast';

// "루트" 라우트 — 뒤로가기 두 번 누르면 앱 종료되는 화면들.
// /meetings: 인증된 사용자의 메인. /login: 비인증 사용자의 메인.
const ROOT_PATHS = new Set(['/meetings', '/login']);

const DOUBLE_TAP_INTERVAL_MS = 2000;

// 열려있는 modal의 close 콜백 stack. 가장 마지막에 push된 것이 LIFO로 먼저 닫힌다.
// modal이 nested로 열린 경우(예: 도메인 설정 안에서 confirm)에도 자연스럽게 동작.
const modalCloseStack: Array<() => void> = [];

/**
 * Modal에서 호출. isOpen=true 동안 close 콜백을 stack에 등록하고,
 * Android 백버튼이 눌리면 가장 마지막에 등록된 modal부터 닫힌다.
 *
 * onClose가 매 render마다 새 함수여도 ref로 최신값을 유지하므로
 * effect 재실행 없이 안정적으로 동작.
 */
export function useModalBackButton(isOpen: boolean, onClose: () => void) {
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!isOpen) return;

    const handler = () => onCloseRef.current();
    modalCloseStack.push(handler);

    return () => {
      const idx = modalCloseStack.indexOf(handler);
      if (idx >= 0) modalCloseStack.splice(idx, 1);
    };
  }, [isOpen]);
}

/**
 * Android 하드웨어 뒤로가기 버튼 처리.
 *
 * 우선순위:
 * 1. 열려있는 modal이 있으면 가장 위 modal부터 닫음 (navigate 안 함)
 * 2. 루트 화면(/meetings, /login)에서 한 번 누르면 토스트, 2초 안에 한 번 더 누르면 종료
 * 3. 그 외 화면: history pop으로 이전 화면 이동
 *
 * AppShell 같은 라우터 내부 컴포넌트에서 한 번만 호출.
 */
export function useAndroidBackButton() {
  const navigate = useNavigate();
  const location = useLocation();
  const locationRef = useRef(location);
  const lastBackPressRef = useRef(0);

  // 핸들러는 ref를 통해 최신 location을 참조 — 매 navigation마다 listener 재등록을 피한다.
  useEffect(() => {
    locationRef.current = location;
  }, [location]);

  useEffect(() => {
    if (!Capacitor.isNativePlatform()) return;

    let handlePromise = CapacitorApp.addListener('backButton', ({ canGoBack }) => {
      // 1) 열린 modal이 있으면 그것부터 닫는다
      if (modalCloseStack.length > 0) {
        const closeTopModal = modalCloseStack[modalCloseStack.length - 1];
        closeTopModal();
        return;
      }

      const path = locationRef.current.pathname;
      const isRoot = ROOT_PATHS.has(path);

      // 2) 루트가 아니면 history pop
      if (!isRoot && canGoBack) {
        navigate(-1);
        return;
      }

      // 3) 루트 화면: 더블탭으로 종료
      const now = Date.now();
      if (now - lastBackPressRef.current < DOUBLE_TAP_INTERVAL_MS) {
        CapacitorApp.exitApp();
      } else {
        lastBackPressRef.current = now;
        void Toast.show({ text: '한 번 더 누르면 앱이 종료됩니다', duration: 'short' });
      }
    });

    return () => {
      void handlePromise.then((handle) => handle.remove());
    };
  }, [navigate]);
}
