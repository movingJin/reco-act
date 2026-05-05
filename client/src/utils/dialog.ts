import { Capacitor } from '@capacitor/core';
import { Dialog } from '@capacitor/dialog';

// 웹: window.confirm으로 fallback
// 네이티브: 시스템 다이얼로그 사용 (iOS UIAlertController, Android AlertDialog)
export async function confirmDialog(message: string, title = '확인'): Promise<boolean> {
  if (!Capacitor.isNativePlatform()) {
    return window.confirm(message);
  }
  const { value } = await Dialog.confirm({
    title,
    message,
    okButtonTitle: '예',
    cancelButtonTitle: '아니오',
  });
  return value;
}
