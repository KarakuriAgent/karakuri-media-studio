/**
 * 日本語を焼き込むので、レンダリングマシンにインストールされている CJK フォントを
 * 優先して指定する。Web フォントは使わない(オフラインでも同じ結果になるようにするため)。
 * 別のフォントを使いたい場合はここだけ差し替える。
 */
export const FONT_FAMILY = [
  '"Noto Sans CJK JP"',
  '"Noto Sans JP"',
  '"Hiragino Sans"',
  '"Yu Gothic"',
  '"Meiryo"',
  '"IPAexGothic"',
  'system-ui',
  'sans-serif',
].join(', ');

/**
 * 端末表示・カウンタなど「等幅で出したい」ところで使う。
 * こちらもインストール済みのフォントだけを並べる(Web フォントは使わない)。
 */
export const MONO_FONT_FAMILY = [
  '"DejaVu Sans Mono"',
  '"Noto Sans Mono CJK JP"',
  '"Noto Sans Mono"',
  '"Liberation Mono"',
  '"Menlo"',
  '"Consolas"',
  'ui-monospace',
  'monospace',
].join(', ');
