import React from 'react';
import { outlineShadow } from '../lib/fx';

/**
 * 縁取り付きのテキストブロック。歌詞・クレジット・カード・端末表示で共有する。
 * 縁取りは text-shadow の 8 方向(背景クリップと併用できるようにするため)。
 */
export const FxText: React.FC<{
  children: React.ReactNode;
  fontFamily: string;
  /** 実ピクセル。1080p 基準の値は ctx.fs() で直してから渡す。 */
  fontSize: number;
  color: string;
  outlineColor?: string;
  /** 実ピクセル。 */
  outlineWidth?: number;
  bold?: boolean;
  style?: React.CSSProperties;
}> = ({
  children,
  fontFamily,
  fontSize,
  color,
  outlineColor,
  outlineWidth = 0,
  bold = true,
  style,
}) => (
  <div
    style={{
      fontFamily,
      fontSize,
      fontWeight: bold ? 800 : 500,
      lineHeight: 1.25,
      color,
      whiteSpace: 'pre-wrap',
      margin: 0,
      textShadow: outlineColor ? outlineShadow(outlineColor, outlineWidth) : undefined,
      ...style,
    }}
  >
    {children}
  </div>
);
