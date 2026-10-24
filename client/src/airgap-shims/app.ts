// @capacitor/app 대역(airgap 웹 빌드). 뒤로 가기 처리(backButton)는 네이티브에서만 부른다.
import { unavailable } from './unavailable';

export const App = unavailable<Record<string, never>>('App');
