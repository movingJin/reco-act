// @capacitor/core 대역(airgap 웹 빌드). 항상 웹 플랫폼으로 동작한다.
import { unavailable } from './unavailable';

export const Capacitor = {
  isNativePlatform: (): boolean => false,
  getPlatform: (): string => 'web',
  isPluginAvailable: (): boolean => false,
  convertFileSrc: (filePath: string): string => filePath,
};

// RecorderControls가 모듈을 불러올 때 호출하므로 호출 자체는 허용하고, 메서드를 부를 때 예외를 낸다.
export function registerPlugin<T>(name: string): T {
  return unavailable<T>(name);
}
