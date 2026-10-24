// @capacitor/preferences 대역(airgap 웹 빌드). 웹에서도 로그인 토큰과 대기 중인 녹음 메타 저장에 쓰므로 localStorage로 실제로 동작한다.
const PREFIX = 'reco-act.';

export const Preferences = {
  async get({ key }: { key: string }): Promise<{ value: string | null }> {
    return { value: localStorage.getItem(PREFIX + key) };
  },
  async set({ key, value }: { key: string; value: string }): Promise<void> {
    localStorage.setItem(PREFIX + key, value);
  },
  async remove({ key }: { key: string }): Promise<void> {
    localStorage.removeItem(PREFIX + key);
  },
  async keys(): Promise<{ keys: string[] }> {
    const keys = Object.keys(localStorage).filter((k) => k.startsWith(PREFIX)).map((k) => k.slice(PREFIX.length));
    return { keys };
  },
  async clear(): Promise<void> {
    (await Preferences.keys()).keys.forEach((key) => localStorage.removeItem(PREFIX + key));
  },
};
