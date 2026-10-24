// capacitor-voice-recorder 대역(airgap 웹 빌드). 웹 녹음은 MediaRecorder를 쓴다(RecorderControls).
import { unavailable } from './unavailable';

export const VoiceRecorder = unavailable<Record<string, never>>('VoiceRecorder');
