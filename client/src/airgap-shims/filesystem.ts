// @capacitor/filesystem 대역(airgap 웹 빌드). recordingStore가 모듈을 불러올 때 Directory.Data를 읽으므로 상수는 실제 값을 둔다.
import { unavailable } from './unavailable';

export const Directory = {
  Documents: 'DOCUMENTS',
  Data: 'DATA',
  Library: 'LIBRARY',
  Cache: 'CACHE',
  External: 'EXTERNAL',
  ExternalStorage: 'EXTERNAL_STORAGE',
} as const;

export const Encoding = { UTF8: 'utf8', ASCII: 'ascii', UTF16: 'utf16' } as const;

export const Filesystem = unavailable<Record<string, never>>('Filesystem');
