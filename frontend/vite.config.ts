import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const BACKEND = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  // `/assets` is taken by the backend's uploaded-asset mount, so build bundles
  // into dist/static/ instead to avoid shadowing them in production serving.
  build: { assetsDir: 'static' },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true, ws: true },
      '/outputs': { target: BACKEND, changeOrigin: true },
      '/assets': { target: BACKEND, changeOrigin: true },
    },
  },
})
