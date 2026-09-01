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

// ---------------------------------------------------------------------------
// FxOverlay: 出来上がった映像(base)の上に、イベントで演出を載せるコンポジション
// ---------------------------------------------------------------------------
//
// すべてのイベントは「秒」で置き、フレーム換算は round(t * fps)。
// 位置・大きさは画面比(0..1)で書くので、解像度・fps に依存しない。
// フォントサイズだけは 1080p 基準のピクセル値で、height に応じて自動スケールされる。

/**
 * 色の書き方。
 * - CSS の色そのまま: "#dc1428" / "red" / "rgba(0,0,0,0.5)"
 * - theme.palette の番号: "0" / "1" / "2"
 * - theme.palette の役割名: "accent"(=0) / "fg"(=1) / "bg"(=2)
 */
export const fxColorSchema = z.string();

/** 画面の 9 か所。sprite の貼り付け位置に使う。 */
export const fxAnchorSchema = z.enum([
  'topLeft',
  'top',
  'topRight',
  'left',
  'center',
  'right',
  'bottomLeft',
  'bottom',
  'bottomRight',
]);

/** テキストを寄せる隅。 */
export const fxCornerSchema = z.enum([
  'topLeft',
  'topRight',
  'bottomLeft',
  'bottomRight',
  'center',
]);

/** SVG で描ける記号。生成せずに済むものはここで賄う。 */
export const fxShapeKindSchema = z.enum([
  'bolt', // 雷
  'heart', // ハート
  'speedlines', // 集中線
  'bubble', // 吹き出し
  'star', // 星
  'circle', // 円(囲み)
  'arrow', // 矢印
  'burst', // 爆発(ギザギザの囲み)
  'cross', // ばつ印
]);

/** 記号・スプライトの動き。 */
export const fxMotionSchema = z.enum(['none', 'pop', 'float', 'spin', 'shake', 'stamp']);

/**
 * すべてのイベントに共通のフィールド。
 * - `t`: 開始秒(必須)
 * - `until` / `duration`: 終わり。どちらも無ければ型ごとの既定尺
 * - `seed`: 乱数の種。省略時は配列内の位置から決まる(props が同じなら毎回同じ絵)
 * - `z`: 重なりの順。省略時は型ごとの既定層(FxOverlay.tsx の EVENT_LAYER)
 */
const fxEventCommon = {
  t: z.number().min(0),
  until: z.number().min(0).optional(),
  duration: z.number().min(0).optional(),
  seed: z.number().int().optional(),
  /**
   * 重なりの順(小さいほど下)。省略時は型ごとの既定層。
   * 型の既定は invertShake/collapse=0, glitchCut=1, beatMarker=2,
   * sprite/stickerStack/shape=3, imageSlam=4, lyric/terminalText/credits=5,
   * screen=6, card=7, endCard=8, crtOff=9。
   * 同じ層なら events に書いた順。小数も書ける(例: screen の上なら 6.5)。
   */
  z: z.number().optional(),
};

/**
 * ハーフトーンの細かさを明示する書き方。
 * - `alpha`: 点そのものの不透明度(そのまま使う)
 * - `dot`: 点の間隔(1080p 基準 px)。小さいほど細かい
 * 既定は BAN!BAN!BAN! の値(720p で alpha 0.18 / dot 14px)。
 */
export const fxHalftoneSchema = z.object({
  alpha: z.number().min(0).max(1).default(0.18),
  dot: z.number().min(1).default(21),
});

/**
 * 出際の数フレームを走査線ずれ + RGB 分離で飛ばして消す(書かなければ無効)。
 * フェードではなく「電波が切れる」消え方。歌詞を黒画面の上から消すときなど。
 */
export const fxOutGlitchSchema = z.object({
  /** 終わりから数えて何フレーム荒らすか。 */
  frames: z.number().int().min(1).default(2),
  /** 走査線ずれの量(1080p 基準 px)。 */
  displace: z.number().min(0).default(39),
  /** RGB 分離の量。1 が 1080p の 14px 相当。 */
  chroma: z.number().min(0).max(1).default(0.85),
});

/** 出際のグリッチ。4 つの型(lyric / sprite / imageSlam / terminalText)で共通。 */
const fxOutGlitchField = { outGlitch: fxOutGlitchSchema.optional() };

/** 画像の不透明部分に薄く敷くハーフトーン・枠・微振動(積んだカードの見た目)。 */
const fxSpriteLookCommon = {
  /** 画像の輪郭(アルファ)に沿って付ける枠。color: の疑似素材では箱の外周に引く。 */
  border: z
    .object({
      color: fxColorSchema.default('fg'),
      /** 1080p 基準の太さ px。 */
      width: z.number().min(0).default(2),
      /**
       * 罫線を輪郭の「内側」に引く(既定 false = 外側)。
       * color: の疑似素材は箱の内側に引くので、画像をそれに揃えたいときに true。
       */
      inset: z.boolean().default(false),
    })
    .optional(),
  /**
   * 不透明部分に敷くハーフトーン。点の色は border.color、無ければ fg。
   * - 数値(0..1): 従来どおりの薄さと粗さ(0 で無効)
   * - {alpha, dot}: 濃さと細かさを直接指定する
   */
  halftone: z.union([z.number().min(0).max(1), fxHalftoneSchema]).default(0),
  /** 貼ったあとの微振動(積んだカードが小刻みに揺れる)。 */
  jitter: z
    .object({
      /** 揺れ幅(1080p 基準 px)。 */
      px: z.number().min(0).default(2.2),
      /** 揺れの周期(Hz)。 */
      hz: z.number().min(0).default(3.1),
      /** 併せて振る角度(度)。 */
      rotDeg: z.number().min(0).default(0.9),
    })
    .optional(),
};

/** 不透明部分をこの色 1 色で塗る(白抜きロゴを配色に合わせるとき)。 */
const fxTintField = fxColorSchema.optional();

/** 全画面の色地に極太文字を数フレームだけ叩き込む。決めの起点。 */
export const fxCardEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('card'),
  text: z.string().default(''),
  /** 1 枚あたりのフレーム数。 */
  frames: z.number().int().min(1).default(5),
  /** "背景色/文字色" を並べる。並べたぶんだけ frames ごとに切り替わる。 */
  sequence: z.array(z.string()).default(['accent/fg', 'fg/accent', 'bg/fg']),
  /** 傾きの振れ幅(度)。seed から ±jitterDeg の範囲で決まる。 */
  jitterDeg: z.number().min(0).default(6),
  /** 横ずれの振れ幅。画面幅に対する比。 */
  jitterPx: z.number().min(0).default(0.047),
  /** 1080p 基準の文字サイズ。 */
  fontSize: z.number().min(1).default(420),
  /**
   * 背景に敷くハーフトーンの点。
   * - true / false: 従来どおり(薄く粗い点)
   * - {alpha, dot}: 濃さと細かさを直接指定する(BAN!BAN!BAN! は alpha 0.18 / dot 21)
   */
  halftone: z.union([z.boolean(), fxHalftoneSchema]).default(true),
  /**
   * 斜めのカラーワイプで入る(既定は無効)。
   * 通り過ぎたところだけ文字と斜線が color に置き換わり、境界に線が走る。
   */
  wipe: z
    .object({
      /** ワイプの角度(度)。負で左下から右上へ。 */
      angle: z.number().default(-22),
      /** 渡りきるまでのフレーム数(カードの先頭から数える)。 */
      frames: z.number().int().min(1).default(5),
      /** ワイプの色。 */
      color: fxColorSchema.default('accent'),
    })
    .optional(),
  /** 端(画面の外周)のクロマ収差。0 で無効。1 が 1080p の 14px 相当。 */
  chroma: z.number().min(0).max(1).default(0),
});

/** 反転(ネガ)を数フレーム入れ、そのあと減衰しながら画面を揺らす。card の直後に置く。 */
export const fxInvertShakeEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('invertShake'),
  /** 反転させるフレーム数(0 でシェイクだけ)。 */
  frames: z.number().int().min(0).default(3),
  /** 反転が明けてからシェイクが収まるまでの秒数。 */
  shakeTail: z.number().min(0).default(0.15),
  /** 揺れ幅。画面幅に対する比。 */
  amplitude: z.number().min(0).default(0.0094),
  /** 反転の代わりに白飛ばしにする。 */
  mode: z.enum(['invert', 'flash']).default('invert'),
  /**
   * 反転が明けた最初の数フレームだけ base を拡大 + クロマ収差(既定は無効)。
   * 決めの「止め」。BAN!BAN!BAN! では 1f だけ 1.08 倍にしている。
   */
  hitStop: z
    .object({
      /** 拡大率。 */
      scale: z.number().min(1).default(1.08),
      /** クロマ収差の量(0..1)。1 が 1080p の 14px 相当。 */
      chroma: z.number().min(0).max(1).default(0.5),
      /** 何フレーム続けるか。 */
      frames: z.number().int().min(1).default(1),
    })
    .optional(),
});

/** 決め台詞の画像を叩き込む。位置と大きさは画面比で書く。 */
export const fxImageSlamEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('imageSlam'),
  src: z.string(),
  /** 中心の位置(画面比)。 */
  cx: z.number().default(0.5),
  cy: z.number().default(0.76),
  /** 表示幅(画面幅に対する比)。 */
  w: z.number().min(0.01).max(4).default(0.62),
  /** 高さの上限(画面高に対する比)。超えるときは幅を詰める。 */
  maxH: z.number().min(0.01).max(4).default(0.4),
  /** [叩き込みはじめの倍率, 着地の倍率]。 */
  snap: z.tuple([z.number(), z.number()]).default([1.35, 1.0]),
  /** 叩き込みにかけるフレーム数(spring が false のときだけ使う)。 */
  snapFrames: z.number().int().min(1).default(3),
  /** true なら spring(行き過ぎて数フレーム揺れて着地)で入る。 */
  spring: z.boolean().default(true),
  /** 出た瞬間だけ白く飛ばす量(0..1)。 */
  flash: z.number().min(0).max(1).default(0.55),
  /** 傾き(度、反時計回りが正)。 */
  rot: z.number().default(0),
  /** 引きぎわに少しだけ縮める倍率。 */
  outScale: z.number().min(0.1).default(1.0),
  ...fxOutGlitchField,
  tint: fxTintField,
});

/** 等幅フォントの端末表示。lines を出し、then があれば同じ場所で差し替える。 */
export const fxTerminalTextEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('terminalText'),
  lines: z.array(z.string()).default([]),
  /** lines のあとに差し替えて出す行。 */
  then: z
    .array(z.object({ text: z.string(), color: fxColorSchema.default('accent') }))
    .default([]),
  /** 1 段あたりのフレーム数。 */
  frames: z.number().int().min(1).default(5),
  corner: fxCornerSchema.default('topLeft'),
  /** 中心の位置(画面比)。どちらかを書くと corner より優先される(書かないほうは 0.5)。 */
  cx: z.number().optional(),
  cy: z.number().optional(),
  /** corner に寄せるときの余白(画面幅に対する比)。 */
  margin: z.number().min(0).default(0.045),
  color: fxColorSchema.default('fg'),
  /** 1080p 基準の文字サイズ。 */
  fontSize: z.number().min(1).default(34),
  opacity: z.number().min(0).max(1).default(1),
  /** 1 文字ずつ打ち出す(起動シーケンス風)。 */
  typing: z.boolean().default(false),
  /** typing のときの打鍵速度(文字/秒)。 */
  cps: z.number().min(1).default(24),
  /** 末尾にカーソルを点滅させる。 */
  cursor: z.boolean().default(false),
  ...fxOutGlitchField,
});

/** 全画面を塗りつぶす板。ブレイクの黒画面・タイトルカード・章の切れ目に使う。 */
export const fxScreenEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('screen'),
  bg: fxColorSchema.default('bg'),
  text: z.string().default(''),
  textColor: fxColorSchema.default('fg'),
  /** 1080p 基準の文字サイズ。 */
  fontSize: z.number().min(1).default(40),
  /** 等幅で描くか(端末風)。false なら見出し用のゴシック。 */
  mono: z.boolean().default(true),
  /** 中央に貼るロゴなどの画像(任意)。 */
  src: z.string().optional(),
  /** 画像の幅(画面幅に対する比)。 */
  imageWidth: z.number().min(0.01).max(2).default(0.44),
  /** 頭の数フレームを走査線ずれ + RGB 分離で荒らしてから確定させる。 */
  glitch: z.boolean().default(false),
  glitchFrames: z.number().int().min(1).default(3),
});

/** 走査線ずれ + ブロックノイズを数フレーム。カットの継ぎ目に差す。 */
export const fxGlitchCutEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('glitchCut'),
  frames: z.number().int().min(1).default(3),
  /** 走査線ずれの量。画面幅に対する比。 */
  displace: z.number().min(0).default(0.02),
  /** ブロックノイズの枚数。 */
  blocks: z.number().int().min(0).default(14),
  /** RGB 分離の量。画面幅に対する比。 */
  chroma: z.number().min(0).default(0.004),
});

/** 画面をタイルに割って落とす。ベースの絵は割れる直前で止める。 */
export const fxCollapseEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('collapse'),
  cols: z.number().int().min(1).max(24).default(6),
  rows: z.number().int().min(1).max(24).default(4),
  /** 落ちきるまでの秒数(duration を書かなければこれが尺)。 */
  fallSeconds: z.number().min(0.05).default(0.6),
  background: fxColorSchema.default('bg'),
});

/** CRT の電源断。横一線に潰れて白点になって消える。 */
export const fxCrtOffEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('crtOff'),
  frames: z.number().int().min(2).default(8),
  color: fxColorSchema.default('#ffffff'),
});

/** 透過画像を 1 枚貼る。ロゴ押印・小物・キャラの立ち絵。 */
export const fxSpriteEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('sprite'),
  src: z.string(),
  /** anchor を書くと隅・辺に寄せる。cx / cy を書けばそちらが優先。 */
  anchor: fxAnchorSchema.default('center'),
  cx: z.number().optional(),
  cy: z.number().optional(),
  /** anchor で寄せるときの余白(画面幅に対する比)。 */
  margin: z.number().min(0).default(0.05),
  /** 表示幅(画面幅に対する比)。 */
  w: z.number().min(0.01).max(4).default(0.18),
  /**
   * 高さの上限(画面高に対する比)。anchor で寄せるときの「箱」の高さでもあるので、
   * 素材の見た目の高さに近い値にしておくと隅にきちんと付く。
   */
  maxH: z.number().min(0.01).max(4).default(0.18),
  rot: z.number().default(0),
  opacity: z.number().min(0).max(1).default(1),
  motion: fxMotionSchema.default('stamp'),
  /** 出入りのフェード秒(0 で瞬間)。決めでは 0 のまま。 */
  fade: z.number().min(0).default(0),
  ...fxSpriteLookCommon,
  tint: fxTintField,
  ...fxOutGlitchField,
});

/** 同じ画像を、キーフレームで指定した位置へ次々に貼って積む。 */
export const fxStickerStackEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('stickerStack'),
  src: z.string(),
  target: z.object({
    /** 座標の単位。px はコンポジションのピクセル空間、ratio は画面比。 */
    space: z.enum(['px', 'ratio']).default('px'),
    keyframes: z
      .array(
        z.object({
          /** この位置になる秒。 */
          t: z.number().min(0),
          x: z.number(),
          y: z.number(),
          /** 表示幅。 */
          w: z.number().min(0),
          rot: z.number().default(0),
          /** この時刻に貼るか(false のキーフレームでは消える)。 */
          visible: z.boolean().default(true),
          /** このキーフレームで貼り直すときに pop させるか(false で等倍のまま出す)。 */
          pop: z.boolean().default(true),
        }),
      )
      .default([]),
  }),
  /** 貼った直後の 3 フレームで大きめから落として着地させる倍率。 */
  pop: z.number().min(1).default(1.5),
  /** この秒から外へ吹き飛ばして消す(省略時は吹き飛ばさない)。 */
  blowOutAt: z.number().min(0).optional(),
  blowOutSeconds: z.number().min(0.05).default(0.5),
  opacity: z.number().min(0).max(1).default(1),
  ...fxSpriteLookCommon,
});

/** 隅に出す小さなクレジット。白 + 縁取り。 */
export const fxCreditsEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('credits'),
  /**
   * 行。文字列だけなら 1 行目が fontSize、2 行目以降は 0.82 倍で出る。
   * 行ごとに変えたいときは {text, fontSize, color} で書く(fontSize は 1080p 基準)。
   */
  lines: z
    .array(
      z.union([
        z.string(),
        z.object({
          text: z.string(),
          fontSize: z.number().min(1).optional(),
          color: fxColorSchema.optional(),
        }),
      ]),
    )
    .default([]),
  corner: fxCornerSchema.default('topRight'),
  /** 中心の位置(画面比)。どちらかを書くと corner より優先される(書かないほうは 0.5)。 */
  cx: z.number().optional(),
  cy: z.number().optional(),
  /** 1080p 基準の文字サイズ。 */
  fontSize: z.number().min(1).default(34),
  color: fxColorSchema.default('fg'),
  outlineColor: fxColorSchema.default('accent'),
  /** 縁取りの太さ(1080p 基準 px、0 で無効)。 */
  outlineWidth: z.number().min(0).default(4),
  margin: z.number().min(0).default(0.045),
  opacity: z.number().min(0).max(1).default(1),
});

/** 歌詞テロップ。行そのままか、1 文字ずつ送るか。 */
export const fxLyricEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('lyric'),
  text: z.string(),
  /** karaoke のときの 1 文字ごとの発音秒。省略時は表示区間を等分する。 */
  chars: z.array(z.object({ c: z.string(), s: z.number().min(0) })).default([]),
  style: z.enum(['line', 'karaoke']).default('line'),
  position: z.enum(['top', 'center', 'bottom']).default('bottom'),
  /** 1080p 基準の文字サイズ。 */
  fontSize: z.number().min(1).default(56),
  color: fxColorSchema.default('fg'),
  /** karaoke で「まだ歌っていない」文字の色。 */
  pendingColor: fxColorSchema.default('rgb(168,170,175)'),
  outlineColor: fxColorSchema.default('accent'),
  /** 発音した瞬間の縁の色。 */
  activeColor: fxColorSchema.default('#5ad7ff'),
  outlineWidth: z.number().min(0).default(7),
  /** 行頭の数フレームだけ少し大きく出す。 */
  snapFrames: z.number().int().min(0).default(2),
  opacity: z.number().min(0).max(1).default(1),
  ...fxOutGlitchField,
});

/** 終わりの黒 + ロゴ。 */
export const fxEndCardEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('endCard'),
  /** ロゴが出るまでの黒の秒数。 */
  black: z.number().min(0).default(1.5),
  bg: fxColorSchema.default('bg'),
  logo: z
    .object({
      src: z.string(),
      /** ロゴを出しておく秒数。 */
      duration: z.number().min(0.1).default(2.0),
      /** 幅(画面幅に対する比)。 */
      w: z.number().min(0.01).max(2).default(0.34),
      tint: fxTintField,
    })
    .optional(),
  text: z.string().default(''),
  textColor: fxColorSchema.default('fg'),
  fontSize: z.number().min(1).default(44),
});

/** 隅で拍を刻むマーカー列。間奏の「間」を持たせるのに使う。 */
export const fxBeatMarkerEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('beatMarker'),
  /** 1 拍の秒数。 */
  beat: z.number().min(0.02).default(0.5),
  /** マーカーの数。 */
  count: z.number().int().min(1).max(32).default(8),
  corner: fxCornerSchema.default('bottomRight'),
  color: fxColorSchema.default('accent'),
  idleColor: fxColorSchema.default('fg'),
  /** 1080p 基準のマーカー 1 個の大きさ。 */
  size: z.number().min(1).default(18),
  /** 隅に添える等幅のラベル。 */
  label: z.string().default(''),
  /** この拍数ごとにブロックノイズを 1 回差す(0 で無効)。 */
  glitchEvery: z.number().int().min(0).default(8),
  opacity: z.number().min(0).max(1).default(0.9),
});

/** SVG で描く記号。生成した素材を使うまでもないものはこれで足りる。 */
export const fxShapeEventSchema = z.object({
  ...fxEventCommon,
  type: z.literal('shape'),
  shape: fxShapeKindSchema.default('bolt'),
  /** 中心(画面比)。 */
  cx: z.number().default(0.5),
  cy: z.number().default(0.5),
  /** 大きさ(画面幅に対する比)。 */
  size: z.number().min(0.01).max(3).default(0.2),
  /** 塗りの色("none" で塗らない)。 */
  fill: fxColorSchema.default('accent'),
  /** 線の色("none" で描かない)。 */
  stroke: fxColorSchema.default('none'),
  /** 線の太さ(1080p 基準 px)。 */
  strokeWidth: z.number().min(0).default(8),
  rot: z.number().default(0),
  opacity: z.number().min(0).max(1).default(1),
  motion: fxMotionSchema.default('pop'),
  /** bubble のときに中に入れる文字。 */
  text: z.string().default(''),
  textColor: fxColorSchema.default('bg'),
  fontSize: z.number().min(1).default(48),
});

export const fxEventSchema = z.discriminatedUnion('type', [
  fxCardEventSchema,
  fxInvertShakeEventSchema,
  fxImageSlamEventSchema,
  fxTerminalTextEventSchema,
  fxScreenEventSchema,
  fxGlitchCutEventSchema,
  fxCollapseEventSchema,
  fxCrtOffEventSchema,
  fxSpriteEventSchema,
  fxStickerStackEventSchema,
  fxCreditsEventSchema,
  fxLyricEventSchema,
  fxEndCardEventSchema,
  fxBeatMarkerEventSchema,
  fxShapeEventSchema,
]);

/** 全編に薄く掛ける系。BAN では最終的に OFF にした。既定も OFF。 */
export const fxAmbientSchema = z.object({
  scanline: z.boolean().default(false),
  vignette: z.boolean().default(false),
  scanlineAlpha: z.number().min(0).max(1).default(0.05),
  /** 走査線の周期(1080p 基準 px)。 */
  scanlinePeriod: z.number().min(2).default(4),
  vignetteAlpha: z.number().min(0).max(1).default(0.35),
});

/** 配色とフォント。イベントの色は "accent" / "fg" / "bg" / "0" / "1" / "2" で参照できる。 */
export const fxThemeSchema = z.object({
  /** [accent(0), fg(1), bg(2)] の順。4 つ目以降は "3" … で参照する。 */
  palette: z.array(z.string()).default(['#dc1428', '#f5f5f5', '#08080a']),
  /** 見出し・歌詞のフォント(空なら fonts.ts の既定)。 */
  fontFamily: z.string().default(''),
  /** 端末表示のフォント(空なら fonts.ts の既定)。 */
  monoFamily: z.string().default(''),
});

/** 演出を載せる下地。書かなければ backgroundColor だけの素の板になる。 */
export const fxBaseSchema = z.object({
  src: z.string(),
  /** 素材側の頭出し秒(動画のみ)。 */
  in: z.number().min(0).default(0),
  fit: z.enum(['cover', 'contain', 'fill']).default('fill'),
  /** 動画の音を鳴らすか。既定は鳴らさない(音は audio で別に足す)。 */
  muted: z.boolean().default(true),
  volume: z.number().min(0).max(1).default(0),
  playbackRate: z.number().min(0.1).max(4).default(1),
});

export const fxOverlaySchema = z.object({
  fps: z.number().min(1).max(120).default(30),
  width: z.number().min(16).default(1920),
  height: z.number().min(16).default(1080),
  /** 明示的に尺を決めたいとき(秒)。省略時は base の尺と events の終端から自動算出。 */
  durationInSeconds: z.number().min(0.1).optional(),
  /** base が無いところ・base が透けるところの色。 */
  backgroundColor: z.string().default('#000000'),
  /** 全体の乱数の種。イベント側で seed を書かなければ、これと並び順から決まる。 */
  seed: z.number().int().default(1),
  base: fxBaseSchema.optional(),
  audio: audioSchema.optional(),
  theme: fxThemeSchema.default(() => fxThemeSchema.parse({})),
  ambient: fxAmbientSchema.default(() => fxAmbientSchema.parse({})),
  events: z.array(fxEventSchema).default([]),
});

export type FxAnchor = z.infer<typeof fxAnchorSchema>;
export type FxCorner = z.infer<typeof fxCornerSchema>;
export type FxShapeKind = z.infer<typeof fxShapeKindSchema>;
export type FxMotion = z.infer<typeof fxMotionSchema>;
export type FxEvent = z.infer<typeof fxEventSchema>;
export type FxEventOf<T extends FxEvent['type']> = Extract<FxEvent, { type: T }>;
export type FxHalftone = z.infer<typeof fxHalftoneSchema>;
export type FxOutGlitch = z.infer<typeof fxOutGlitchSchema>;
export type FxTheme = z.infer<typeof fxThemeSchema>;
export type FxAmbient = z.infer<typeof fxAmbientSchema>;
export type FxBase = z.infer<typeof fxBaseSchema>;
export type FxOverlayProps = z.infer<typeof fxOverlaySchema>;
