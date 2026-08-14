import React from 'react'
import ReactDOM from 'react-dom/client'
import { registerSW } from 'virtual:pwa-register'
import App from './App'
// 可変フォント（本文 = Inter、日本語 = Noto Sans JP）。index.css より前に読み込んで
// @font-face をカスケードの先頭に置く。
import '@fontsource-variable/inter'
import '@fontsource-variable/noto-sans-jp'
import './index.css'

// 新しい sw.js を見つけたら確認なしでリロードする（registerType: autoUpdate）。
registerSW({ immediate: true })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
