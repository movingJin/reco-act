import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// airgap(폐쇄망) 웹 빌드(`vite build --mode airgap`)는 Capacitor 패키지를 로컬 대역으로 바꿔 네이티브 코드를 번들에서 뺀다
// (01-plan 4.1). 경로는 프로젝트 루트 기준이다. public·mobile 빌드에는 alias가 없어 그대로다.
const AIRGAP_ALIASES = {
  '@capacitor/core': '/src/airgap-shims/core.ts',
  '@capacitor/preferences': '/src/airgap-shims/preferences.ts',
  '@capacitor/filesystem': '/src/airgap-shims/filesystem.ts',
  '@capacitor/app': '/src/airgap-shims/app.ts',
  '@capacitor/toast': '/src/airgap-shims/toast.ts',
  '@capacitor/dialog': '/src/airgap-shims/dialog.ts',
  '@capacitor/share': '/src/airgap-shims/share.ts',
  'capacitor-voice-recorder': '/src/airgap-shims/voice-recorder.ts',
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // airgap 산출물은 public dist와 섞이지 않게 따로 둔다.
  ...(mode === 'airgap' ? { resolve: { alias: AIRGAP_ALIASES }, build: { outDir: 'dist-airgap' } } : {}),
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
}))
