// @capacitor/dialog 대역(airgap 웹 빌드). 웹에서는 window.confirm을 쓴다(utils/dialog).
import { unavailable } from './unavailable';

export const Dialog = unavailable<Record<string, never>>('Dialog');
