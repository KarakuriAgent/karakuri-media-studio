import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

const ROOT = path.dirname(fileURLToPath(import.meta.url))

// 同梱の Remotion プロジェクト（リポジトリルートの `remotion/`）の src。
// FxOverlay をプレビューへ重ねるために frontend からバンドルする。
const REMOTION_SRC = path.resolve(ROOT, '../remotion/src')

// run.sh --dev が HOST/PORT を export するため、バックエンドのポート変更に追従する
const BACKEND =
  process.env.VITE_BACKEND_URL ??
  `http://${process.env.HOST || '127.0.0.1'}:${process.env.PORT || '8000'}`

export default defineConfig({
  plugins: [
    react(),
    // PWA（インストール可能・オフラインでシェルだけ開ける）。生成物は
    // dist/manifest.webmanifest / dist/sw.js。登録は main.tsx 側。
    VitePWA({
      // 新しいビルドを見つけたら確認なしで差し替える（常時 online 前提のアプリのため）
      registerType: 'autoUpdate',
      // main.tsx の virtual:pwa-register が更新検知してリロードする。
      // 注入スクリプトだけだと SW が差し替わっても画面が古いまま残る。
      injectRegister: false,
      manifest: {
        name: 'Karakuri Media Studio',
        short_name: 'Karakuri Media Studio',
        description: 'ローカル ComfyUI で画像・動画・音声を生成するスタジオ',
        lang: 'ja',
        dir: 'ltr',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        // index.css の --background (#0a0c11) に合わせる
        theme_color: '#0a0c11',
        background_color: '#0a0c11',
        icons: [
          { src: '/pwa-192x192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/pwa-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          {
            src: '/maskable-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // プリキャッシュはビルド成果物（シェル）だけ。生成物・アップロード素材は対象外。
        globPatterns: ['**/*.{js,css,html,woff2,svg,png,webmanifest}'],
        // Noto Sans JP は 120 個以上のサブセットに分かれていて全部で 6MB 近くある。
        // ブラウザは実際に使う数個しか取りに行かないので、全部をプリキャッシュすると
        // インストール時のダウンロードだけが無駄に膨らむ。オフライン時に落ちても
        // system-ui にフォールバックするだけなので除外する（Inter は残す）。
        globIgnores: ['**/noto-sans-jp-*.woff2', 'index.html'],
        // 可変フォント（Noto Sans JP など）が数 MB になることがあるので上限を上げる
        maximumFileSizeToCacheInBytes: 6 * 1024 * 1024,
        // index.html はプリキャッシュしない。ナビゲーションは毎回ネットワークへ
        // 行き、サーバの no-cache なシェル（新しい JS ハッシュ）を取る。
        // 常時 online 前提なので、古い HTML を SW が掴み続ける方が困る。
        navigateFallback: undefined,
        // ランタイムキャッシュは持たない（動画・画像はキャッシュしない）
        runtimeCaching: [],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        skipWaiting: true,
        // public/push-sw.js を生成 SW に読み込む（既存のプリキャッシュは変えない）
        importScripts: ['/push-sw.js'],
      },
      // dev サーバーでは SW を登録しない（/api などの proxy を邪魔しないため）
      devOptions: { enabled: false },
    }),
  ],
  // `@/…` で src 配下を、`@fx/…` で同梱 Remotion プロジェクトの src を参照する。
  // FX トラックのプレビューは `remotion/src/FxOverlay.tsx` を**そのまま**描くので、
  // 演出の実装を SPA 側へ写さない（写すと 2 か所を直すことになる）。
  resolve: {
    alias: {
      '@': path.resolve(ROOT, './src'),
      '@fx': REMOTION_SRC,
    },
    // remotion/node_modules と frontend/node_modules の両方に React が
    // 入っているので、束ねるときは 1 つに寄せる（2 つ載ると hooks が壊れる）。
    dedupe: ['react', 'react-dom', 'remotion'],
  },
  // 上の alias で外（frontend/ の外）のファイルを読むので、dev サーバーにも許す。
  optimizeDeps: {
    include: ['remotion', '@remotion/player', '@remotion/media-utils'],
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
    // `@fx/…`（frontend の外）を dev サーバーからも読めるようにする
    fs: { allow: [ROOT, REMOTION_SRC] },
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true, ws: true },
      '/outputs': { target: BACKEND, changeOrigin: true },
      '/assets': { target: BACKEND, changeOrigin: true },
    },
  },
})
