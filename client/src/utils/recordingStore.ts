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

// 업로드 완료 후 원본을 영구 보관하는 하위 디렉토리 (단락별 재생 기능용, Android 전용).
// listNativeRecordings()가 Directory.Data 루트만 훑으므로 이 하위 폴더의 파일은
// "정리 대상 임시 녹음"으로 오인되지 않는다.
const PERSIST_SUBDIR = 'recordings';

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

// keepSessionId를 제외한 모든 청크를 삭제한다(업로드 실패 후 방치된 옛 세션 청소).
async function cleanupWebSessions(keepSessionId?: string): Promise<void> {
  const db = await openDb();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      const req = tx.objectStore(STORE).openCursor();
      req.onsuccess = () => {
        const cursor = req.result;
        if (cursor) {
          if (cursor.value.sessionId !== keepSessionId) cursor.delete();
          cursor.continue();
        }
      };
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch (e) {
    console.warn('Failed to cleanup web sessions', e);
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
// Directory.Data의 녹음 파일 이름을 최신순으로 반환한다.
async function listNativeRecordings(): Promise<string[]> {
  if (!isNative) return [];
  try {
    const { files } = await Filesystem.readdir({ directory: NATIVE_DIRECTORY, path: '' });
    return files
      .filter(
        (f) =>
          f.type === 'file' &&
          f.name.startsWith(NATIVE_FILE_PREFIX) &&
          f.name.endsWith(NATIVE_FILE_SUFFIX) &&
          (f.size ?? 0) > 0
      )
      .sort((a, b) => (b.mtime ?? 0) - (a.mtime ?? 0))
      .map((f) => f.name);
  } catch {
    return [];
  }
}

// keepName을 제외한 모든 녹음 파일을 삭제한다(방치된 옛 파일 청소).
async function cleanupNativeRecordings(keepName?: string): Promise<void> {
  const names = await listNativeRecordings();
  for (const name of names) {
    if (name === keepName) continue;
    try {
      await Filesystem.deleteFile({ directory: NATIVE_DIRECTORY, path: name });
    } catch (e) {
      console.warn('Failed to delete stale recording', name, e);
    }
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
//
// 이 함수는 화면 진입 시(녹음 진행 중이 아닐 때) 호출되므로, 복구 대상으로 유지할
// 단 하나의 녹음을 제외한 나머지 stale 파일/세션을 함께 청소해 디바이스에 파일이
// 무한정 누적되지 않게 한다. (가장 최근 미업로드 녹음 1개만 보존)
export async function recoverPending(currentMeetingId: string): Promise<PendingRecording | null> {
  const result = await resolvePending(currentMeetingId);

  // 유지할 식별자 외 나머지 청소
  if (isNative) {
    await cleanupNativeRecordings(result?.kind === 'native' ? result.path : undefined);
  } else {
    await cleanupWebSessions(result?.kind === 'web' ? result.sessionId : undefined);
  }

  return result;
}

async function resolvePending(currentMeetingId: string): Promise<PendingRecording | null> {
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
      const recordings = await listNativeRecordings();
      if (recordings.length > 0) return { ...pending, path: recordings[0] };
      await clearPending();
      return null;
    }
    return pending;
  }

  // Preferences에 기록이 없어도 네이티브 디스크에 남은 파일이 있으면 복구 제안
  if (isNative) {
    const recordings = await listNativeRecordings();
    if (recordings.length > 0) {
      return {
        kind: 'native',
        meetingId: currentMeetingId,
        mimeType: NATIVE_MIME_TYPE,
        savedAt: 0,
        path: recordings[0],
      };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// 네이티브: 업로드 완료된 녹음의 영구 보관 (단락별 재생, Android 전용)
// 서버는 STT 완료 직후 오디오 사본을 지우므로, 기기가 유일한 원본이 된다.
// ---------------------------------------------------------------------------

function persistedRecordingPath(meetingId: string): string {
  return `${PERSIST_SUBDIR}/${meetingId}.aac`;
}

// 업로드 성공이 확인된 네이티브 녹음을 임시 위치에서 미팅별 영구 보관 경로로 옮긴다.
export async function persistFinishedRecording(p: PendingRecording): Promise<void> {
  if (p.kind !== 'native' || !p.path) return;
  await Filesystem.mkdir({
    directory: NATIVE_DIRECTORY,
    path: PERSIST_SUBDIR,
    recursive: true,
  }).catch(() => {});
  await Filesystem.rename({
    from: p.path,
    to: persistedRecordingPath(p.meetingId),
    directory: NATIVE_DIRECTORY,
    toDirectory: NATIVE_DIRECTORY,
  });
  await clearPending();
}

// 영구 보관된 녹음이 있으면 <audio>에 바로 쓸 수 있는 로컬 src를 반환한다.
export async function getPersistedRecordingSrc(meetingId: string): Promise<string | null> {
  if (!isNative) return null;
  try {
    await Filesystem.stat({ directory: NATIVE_DIRECTORY, path: persistedRecordingPath(meetingId) });
  } catch {
    return null;
  }
  const { uri } = await Filesystem.getUri({ directory: NATIVE_DIRECTORY, path: persistedRecordingPath(meetingId) });
  return Capacitor.convertFileSrc(uri);
}

// 기기에 보관된 녹음을 삭제한다("기기에서 녹취 삭제" 버튼). 서버엔 이미 사본이 없다.
export async function deletePersistedRecording(meetingId: string): Promise<void> {
  try {
    await Filesystem.deleteFile({ directory: NATIVE_DIRECTORY, path: persistedRecordingPath(meetingId) });
  } catch (e) {
    console.warn('Failed to delete persisted recording', e);
  }
}
