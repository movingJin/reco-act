// @capacitor/share 대역(airgap 웹 빌드). 웹 다운로드는 브라우저 다운로드를 쓴다(utils/download).
import { unavailable } from './unavailable';

export const Share = unavailable<Record<string, never>>('Share');
