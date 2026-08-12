import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// run.sh --dev が HOST/PORT を export するため、バックエンドのポート変更に追従する
const BACKEND =
  process.env.VITE_BACKEND_URL ??
  `http://${process.env.HOST || '127.0.0.1'}:${process.env.PORT || '8000'}`

export default defineConfig({
  plugins: [react()],
  // `@/…` で src 配下を参照できるようにする（shadcn/ui の標準的な書式に合わせる）
  resolve: {
    alias: {
      '@': path.resolve(path.dirname(fileURLToPath(import.meta.url)), './src'),
    },
  },
  // `/assets` is taken by the backend's uploaded-asset mount, so build bundles
  // into dist/static/ instead to avoid shadowing them in production serving.
  build: { assetsDir: 'static' },
  // vitest: コンポーネントは jsdom で描画してテストする
  test: {
    environment: 'jsdom',
    setupFiles: ['src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true, ws: true },
      '/outputs': { target: BACKEND, changeOrigin: true },
      '/assets': { target: BACKEND, changeOrigin: true },
    },
  },
})
