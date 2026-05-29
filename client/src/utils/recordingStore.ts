import { Capacitor } from '@capacitor/core';
import { Filesystem, Directory } from '@capacitor/filesystem';
import { Preferences } from '@capacitor/preferences';

// 녹취 내용을 폰/브라우저에 파일로 영속화하고, 앱이 죽거나 업로드가 실패해도
// 다시 복구·재업로드할 수 있게 해주는 저장소.
//
// - 네이티브(Android/iOS): capacitor-voice-recorder가 녹음 중 Directory.Data의
//   파일에 실시간 기록한다(directory 옵션). 정지 시 base64 변환을 하지 않으므로
//   1시간+ 녹음에서도 메모리 부족(OOM) 크래시가 발생하지 않는다.
// - 웹: MediaRecorder를 timeslice로 돌려 들어오는 청크를 IndexedDB에 점진 저장한다.
//   탭/브라우저가 죽어도 청크가 디스크에 남아 다음 실행 때 복구할 수 있다.
//
// 어느 경로든 "업로드 대기 중" 메타데이터를 Preferences에 남겨두고, 업로드가
// 성공으로 확인된 뒤에만 파일/청크와 메타데이터를 삭제한다.

const isNative = Capacitor.isNativePlatform();

const PENDING_KEY = 'pending_recording';
export const NATIVE_DIRECTORY = Directory.Data;
const NATIVE_FILE_PREFIX = 'recording-'; // 플러그인이 생성하는 파일명 패턴
const NATIVE_FILE_SUFFIX = '.aac';

export const WEB_MIME_TYPE = 'audio/wav';
export const NATIVE_MIME_TYPE = 'audio/aac';
export const WEB_TIMESLICE_MS = 5000; // 5초마다 청크를 IndexedDB로 flush

export interface PendingRecording {
  kind: 'native' | 'web';
  meetingId: string;
  mimeType: string;
  savedAt: number;
  durationMs?: number;
  // 네이티브: Directory.Data 기준 상대 경로
  path?: string;
  // 웹: IndexedDB에 청크를 묶는 세션 식별자
  sessionId?: string;
}

// ---------------------------------------------------------------------------
// Preferences: 업로드 대기 메타데이터 (한 번에 하나의 녹음만 다룬다)
// ---------------------------------------------------------------------------
export async function savePending(p: PendingRecording): Promise<void> {
  await Preferences.set({ key: PENDING_KEY, value: JSON.stringify(p) });
}

export async function loadPending(): Promise<PendingRecording | null> {
  const { value } = await Preferences.get({ key: PENDING_KEY });
  if (!value) return null;
  try {
    return JSON.parse(value) as PendingRecording;
  } catch {
    return null;
  }
}

export async function clearPending(): Promise<void> {
  await Preferences.remove({ key: PENDING_KEY });
}

// ---------------------------------------------------------------------------
// 웹: IndexedDB 청크 저장소
// ---------------------------------------------------------------------------
const DB_NAME = 'reco-act-recordings';
const STORE = 'chunks';

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
        store.createIndex('sessionId', 'sessionId', { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function appendWebChunk(sessionId: string, blob: Blob): Promise<void> {
  const db = await openDb();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).add({ sessionId, blob });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

async function readWebChunks(sessionId: string): Promise<Blob[]> {
  const db = await openDb();
  try {
    return await new Promise<Blob[]>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const idx = tx.objectStore(STORE).index('sessionId');
      const req = idx.getAll(IDBKeyRange.only(sessionId));
      req.onsuccess = () => resolve((req.result || []).map((r: { blob: Blob }) => r.blob));
      req.onerror = () => reject(req.error);
    });
  } finally {
    db.close();
  }
}

async function webSessionHasChunks(sessionId: string): Promise<boolean> {
  const db = await openDb();
  try {
    return await new Promise<boolean>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const idx = tx.objectStore(STORE).index('sessionId');
      const req = idx.count(IDBKeyRange.only(sessionId));
      req.onsuccess = () => resolve(req.result > 0);
      req.onerror = () => reject(req.error);
    });
  } finally {
    db.close();
  }
}

async function deleteWebSession(sessionId: string): Promise<void> {
  const db = await openDb();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      const idx = tx.objectStore(STORE).index('sessionId');
      const req = idx.openCursor(IDBKeyRange.only(sessionId));
      req.onsuccess = () => {
        const cursor = req.result;
        if (cursor) {
          cursor.delete();
          cursor.continue();
        }
      };
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

// ---------------------------------------------------------------------------
// 네이티브: Directory.Data에 남은 녹음 파일(orphan) 탐색
// 앱이 녹음 도중 강제 종료되면 path를 Preferences에 기록하기 전에 죽을 수 있다.
// 이 경우에도 .aac 파일은 디스크에 남으므로(ADTS라 헤더 finalize 불필요해 재생 가능)
// 디렉토리를 훑어 가장 최근 파일을 복구 후보로 돌려준다.
// ---------------------------------------------------------------------------
async function newestNativeOrphan(): Promise<string | null> {
  if (!isNative) return null;
  try {
    const { files } = await Filesystem.readdir({ directory: NATIVE_DIRECTORY, path: '' });
    const recordings = files
      .filter(
        (f) =>
          f.type === 'file' &&
          f.name.startsWith(NATIVE_FILE_PREFIX) &&
          f.name.endsWith(NATIVE_FILE_SUFFIX) &&
          (f.size ?? 0) > 0
      )
      .sort((a, b) => (b.mtime ?? 0) - (a.mtime ?? 0));
    return recordings.length > 0 ? recordings[0].name : null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// 공개 API
// ---------------------------------------------------------------------------

// 업로드에 쓸 Blob을 만든다.
// - 웹: IndexedDB 청크를 모아 Blob 조립
// - 네이티브: WebView가 접근 가능한 file URL로 fetch → base64 왕복 없이 단일 Blob 로드
export async function getUploadBlob(p: PendingRecording): Promise<Blob> {
  if (p.kind === 'web') {
    const chunks = await readWebChunks(p.sessionId!);
    return new Blob(chunks, { type: p.mimeType || WEB_MIME_TYPE });
  }
  const { uri } = await Filesystem.getUri({ directory: NATIVE_DIRECTORY, path: p.path! });
  const src = Capacitor.convertFileSrc(uri);
  const res = await fetch(src);
  return await res.blob();
}

// 저장된 녹음(파일/청크)과 대기 메타데이터를 모두 제거한다.
export async function discardRecording(p: PendingRecording): Promise<void> {
  try {
    if (p.kind === 'web' && p.sessionId) {
      await deleteWebSession(p.sessionId);
    } else if (p.kind === 'native' && p.path) {
      await Filesystem.deleteFile({ directory: NATIVE_DIRECTORY, path: p.path });
    }
  } catch (e) {
    console.warn('Failed to delete recording', e);
  }
  await clearPending();
}

// 복구 가능한 녹음을 찾는다. 정상 경로(Preferences pending)를 우선하고,
// 네이티브에서 메타데이터 기록 전 크래시한 경우 디렉토리의 orphan 파일로 폴백한다.
export async function recoverPending(currentMeetingId: string): Promise<PendingRecording | null> {
  const pending = await loadPending();

  if (pending) {
    if (pending.kind === 'web') {
      // 청크가 실제로 남아 있을 때만 복구 대상
      if (pending.sessionId && (await webSessionHasChunks(pending.sessionId))) return pending;
      await clearPending();
      return null;
    }
    // native
    if (!pending.path) {
      const orphan = await newestNativeOrphan();
      if (orphan) return { ...pending, path: orphan };
      return null;
    }
    return pending;
  }

  // Preferences에 기록이 없어도 네이티브 디스크에 남은 파일이 있으면 복구 제안
  if (isNative) {
    const orphan = await newestNativeOrphan();
    if (orphan) {
      return {
        kind: 'native',
        meetingId: currentMeetingId,
        mimeType: NATIVE_MIME_TYPE,
        savedAt: 0,
        path: orphan,
      };
    }
  }
  return null;
}
