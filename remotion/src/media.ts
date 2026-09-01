import { staticFile } from 'remotion';

const VIDEO_EXTENSIONS = ['.mp4', '.webm', '.mov', '.mkv', '.m4v', '.avi'];

export type ResolvedSource =
  | { kind: 'video'; url: string }
  | { kind: 'image'; url: string }
  | { kind: 'color'; css: string };

const parsePseudoColor = (src: string): string | null => {
  if (src.startsWith('color:')) {
    return src.slice('color:'.length).trim();
  }
  if (src.startsWith('gradient:')) {
    const body = src.slice('gradient:'.length).trim();
    // "135:#223344,#5566aa" or "#223344,#5566aa"
    const colonAt = body.indexOf(':');
    const hasAngle = colonAt > 0 && !body.slice(0, colonAt).includes('#');
    const angle = hasAngle ? body.slice(0, colonAt).trim() : '180';
    const colors = (hasAngle ? body.slice(colonAt + 1) : body)
      .split(',')
      .map((c) => c.trim())
      .filter(Boolean);
    if (colors.length === 1) {
      colors.push(colors[0]);
    }
    return `linear-gradient(${angle}deg, ${colors.join(', ')})`;
  }
  return null;
};

/**
 * src を Remotion が読める URL に変換する。
 * - color: / gradient: の疑似ソースは CSS の背景として扱う
 * - http(s) / file: / data: はそのまま
 * - 絶対パスは file:// を付ける
 * - それ以外は public/ 以下の相対パスとして staticFile() で解決
 */
export const resolveSource = (src: string): ResolvedSource => {
  const pseudo = parsePseudoColor(src);
  if (pseudo !== null) {
    return { kind: 'color', css: pseudo };
  }

  let url = src;
  if (/^(https?|file|data|blob):/.test(src)) {
    url = src;
  } else if (src.startsWith('/')) {
    url = `file://${src.split('/').map(encodeURIComponent).join('/')}`;
  } else {
    url = staticFile(src);
  }

  const pathPart = url.split('?')[0].split('#')[0].toLowerCase();
  const isVideo = VIDEO_EXTENSIONS.some((ext) => pathPart.endsWith(ext));
  return { kind: isVideo ? 'video' : 'image', url };
};

/** 音声など、必ず URL として扱いたいときに使う。 */
export const resolveMediaUrl = (src: string): string => {
  const resolved = resolveSource(src);
  return resolved.kind === 'color' ? src : resolved.url;
};

export const isColorSource = (src: string): boolean =>
  src.startsWith('color:') || src.startsWith('gradient:');
