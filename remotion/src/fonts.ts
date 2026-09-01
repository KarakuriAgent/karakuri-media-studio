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
