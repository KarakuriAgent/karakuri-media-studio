import { z } from 'zod';

/**
 * すべての時間指定は「秒」。フレーム換算は fps を使ってコンポーネント側で行う。
 * 座標系は左上原点、width x height のピクセル空間。
 */

export const transitionTypeSchema = z.enum([
  'cut',
  'crossfade',
  'fadeblack',
  'fadewhite',
  'slide',
  'wipe',
]);

export const transitionSchema = z.object({
  /** トランジションの種別。cut は瞬間切り替え(既定)。 */
  type: transitionTypeSchema,
  /** トランジションにかける秒数。cut では無視される。 */
  duration: z.number().min(0).default(0.4),
  /** slide / wipe の進行方向。既定は left(新しいカットが右から左へ入る)。 */
  direction: z.enum(['left', 'right', 'up', 'down']).default('left'),
});

export const kenBurnsSchema = z.object({
  /** 開始スケール(1.0 = 等倍) */
  from: z.number().min(0.1).default(1.0),
  /** 終了スケール */
  to: z.number().min(0.1).default(1.1),
  /** ズームの中心。0..1 の相対座標(0.5, 0.5 で中央) */
  originX: z.number().min(0).max(1).default(0.5),
  originY: z.number().min(0).max(1).default(0.5),
});

export const cutSchema = z.object({
  /**
   * 素材の場所。
   * - http(s) URL: アプリの /outputs 配信をそのまま渡せる
   * - 絶対パス: file:// に変換して読む
   * - public/ 以下の相対パス: staticFile() で解決
   * - "color:#223344": 単色の疑似素材
   * - "gradient:#223344,#5566aa" / "gradient:135:#223344,#5566aa": グラデーションの疑似素材
   */
  src: z.string(),
  /** タイムライン上の開始秒。ビート同期はここをビート時刻に合わせる。 */
  start: z.number().min(0),
  /** 素材側の頭出し秒(動画のみ)。既定 0。 */
  in: z.number().min(0).default(0),
  /** 画面に出す長さ(秒)。 */
  duration: z.number().min(0.01),
  /** 画面へのはめ込み方。 */
  fit: z.enum(['cover', 'contain', 'fill']).default('cover'),
  /** 静止画・動画に掛けるゆっくりしたズーム。 */
  kenBurns: kenBurnsSchema.optional(),
  /** このカットが「入ってくるとき」のトランジション。 */
  transition: transitionSchema.optional(),
  /** 動画素材の音量(0 で無音、既定 0)。BGM を主にするので既定は無音。 */
  volume: z.number().min(0).max(1).default(0),
  /** 動画素材の再生速度。 */
  playbackRate: z.number().min(0.1).max(4).default(1),
  /** 全体に掛ける不透明度。 */
  opacity: z.number().min(0).max(1).default(1),
  /** カットの上に重ねる CSS フィルター(例: "saturate(1.2) contrast(1.1)")。 */
  filter: z.string().optional(),
});

export const lyricLineSchema = z.object({
  text: z.string(),
  /** 表示開始秒 */
  start: z.number().min(0),
  /** 表示終了秒 */
  end: z.number().min(0),
  /**
   * fade: フェードイン/アウト
   * karaoke: 表示中に左から色が送られる(文字送り)
   * pop: 下から跳ね上がって出る
   */
  style: z.enum(['fade', 'karaoke', 'pop']).default('fade'),
  position: z.enum(['top', 'center', 'bottom']).default('bottom'),
  /** ピクセル指定。1080p 基準。width に応じて自動スケールする。 */
  fontSize: z.number().min(1).default(64),
  /** 基本の文字色。karaoke では「まだ歌っていない」色。 */
  color: z.string().default('#ffffff'),
  /** karaoke で色が送られたあとの色。 */
  highlightColor: z.string().default('#ffd54a'),
  /** 縁取りの色(空文字で無効)。 */
  outlineColor: z.string().default('rgba(0,0,0,0.75)'),
  /** 太字にするか。 */
  bold: z.boolean().default(true),
});

export const titleSchema = z.object({
  text: z.string(),
  artist: z.string().default(''),
  /** この秒数まで表示し、直前 0.6 秒でフェードアウトする。 */
  showUntil: z.number().min(0).default(3.0),
  position: z.enum(['topLeft', 'center', 'bottomLeft']).default('bottomLeft'),
  color: z.string().default('#ffffff'),
  fontSize: z.number().min(1).default(72),
});

export const audioSchema = z.object({
  src: z.string(),
  volume: z.number().min(0).max(1).default(1),
  /** 音源側の頭出し秒。 */
  startFrom: z.number().min(0).default(0),
  /** 末尾のフェードアウト秒(0 で無効)。 */
  fadeOut: z.number().min(0).default(0),
});

export const musicVideoSchema = z.object({
  fps: z.number().min(1).max(120).default(30),
  width: z.number().min(16).default(1920),
  height: z.number().min(16).default(1080),
  /** 明示的に尺を決めたいとき(秒)。省略時は cuts / lyrics / title から自動算出。 */
  durationInSeconds: z.number().min(0.1).optional(),
  /** 何も映っていないところの背景色。 */
  backgroundColor: z.string().default('#000000'),
  audio: audioSchema.optional(),
  cuts: z.array(cutSchema).default([]),
  /** ビート時刻(秒)の配列。beatPulse を使うときだけ必要。 */
  beats: z.array(z.number().min(0)).default([]),
  /** beats に合わせてカット全体を軽く脈打たせる。 */
  beatPulse: z.boolean().default(false),
  /** パルスの強さ(1.02 なら最大 2% 拡大)。 */
  beatPulseScale: z.number().min(1).max(1.5).default(1.02),
  lyrics: z.array(lyricLineSchema).default([]),
  title: titleSchema.optional(),
});

export const slateSchema = z.object({
  text: z.string().default('karakuri-remotion'),
  subtitle: z.string().default(''),
  fps: z.number().min(1).max(120).default(30),
  width: z.number().min(16).default(1920),
  height: z.number().min(16).default(1080),
  durationInSeconds: z.number().min(0.1).default(5),
  backgroundColor: z.string().default('#101820'),
  color: z.string().default('#ffffff'),
});

export type Transition = z.infer<typeof transitionSchema>;
export type KenBurns = z.infer<typeof kenBurnsSchema>;
export type Cut = z.infer<typeof cutSchema>;
export type LyricLine = z.infer<typeof lyricLineSchema>;
export type TitleProps = z.infer<typeof titleSchema>;
export type AudioProps = z.infer<typeof audioSchema>;
export type MusicVideoProps = z.infer<typeof musicVideoSchema>;
export type SlateProps = z.infer<typeof slateSchema>;
