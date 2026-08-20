import { AlertTriangle, Scissors, Trash2 } from 'lucide-react'

import type { TimelineClip, TimelineTrack } from '../../types'
import { Section } from '../ui'
import { Button } from '../ui/button'
import {
  SPEED_MAX,
  SPEED_MIN,
  SPEED_PRESETS,
  SUBTITLE_COLOR_LABEL,
  SUBTITLE_POSITION_LABEL,
  SUBTITLE_SIZE_LABEL,
  type SubtitleStyle,
  TRANSITION_LABEL,
  clampSpeed,
  formatSeconds,
  formatTimecode,
  isSpanless,
  overlapOf,
  speedOf,
  subtitleStyle,
  subtitleText,
} from './timeline'

/**
 * 選んでいるクリップの中身と、そのクリップに効く操作。
 *
 * トラックによって出るものが違う:
 *
 * - **映像** … 位置・切り出し・繋ぎの読み取りに加えて、**速度**（リタイム）。
 * - **音声** … **音量**（dB）と**フェードイン / アウト**。
 * - **字幕** … **本文**と見た目（位置・大きさ・色）。
 *
 * トリムと並べ替えはタイムライン上のドラッグで行う（ここは数値では触らせない）。
 */
export default function ClipInspector({
  clip,
  track,
  index,
  canSplit,
  onSplit,
  onDelete,
  onSpeed,
  onPatch,
  onSubtitle,
}: {
  clip: TimelineClip | null
  /** そのクリップが載っているトラック（種別で出す項目が変わる）。 */
  track: TimelineTrack | null
  /** タイムライン上の位置（0 起点。見出しの番号に使う）。 */
  index: number
  /** 再生ヘッドがこのクリップの中にあり、両側に十分な長さが残るか。 */
  canSplit: boolean
  onSplit: () => void
  onDelete: () => void
  /** 速度を変える（映像クリップだけ）。 */
  onSpeed: (speed: number) => void
  /** 音量・フェードなど、並びに影響しない項目を変える。 */
  onPatch: (patch: Partial<TimelineClip>) => void
  /** テロップの本文・見た目を変える。 */
  onSubtitle: (patch: { text?: string; style?: Partial<SubtitleStyle> }) => void
}) {
  if (!clip) {
    return (
      <Section title="クリップ">
        <p className="text-xs text-muted-foreground">
          タイムラインでクリップを選ぶと、ここに中身が出ます。
        </p>
      </Section>
    )
  }

  const kind = track?.kind ?? 'video'
  const source = clip.source_duration_ms
  const speed = speedOf(clip)
  const overlap = overlapOf(clip)
  const style = subtitleStyle(clip)

  return (
    <Section title={`${track?.name ?? 'V1'} のクリップ ${index + 1}`}>
      <div className="flex flex-col gap-2">
        {clip.missing && (
          <p className="flex items-start gap-1.5 rounded border border-red-900/70 bg-red-950/50 px-2 py-1.5 text-xs text-red-200">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>
              メディア欠落: 元のファイルが見つかりません。差し替えるか消すまで
              書き出せません（上のバナーから直せます）。
            </span>
          </p>
        )}

        <p className="break-words text-xs text-foreground/85">
          {kind === 'subtitle'
            ? subtitleText(clip) || '（本文なし）'
            : clip.label || '（見出しなし）'}
        </p>

        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Row label="位置" value={formatTimecode(clip.start_ms)} />
          <Row label="尺" value={formatSeconds(clip.duration_ms)} />
          {!isSpanless(clip) && (
            <>
              <Row
                label="切り出し"
                value={`${formatTimecode(clip.in_ms)} 〜 ${formatTimecode(clip.out_ms)}`}
              />
              <Row
                label="ソースの長さ"
                value={source == null ? '不明' : formatSeconds(source)}
              />
            </>
          )}
          {overlap > 0 && (
            <Row
              label="前との繋ぎ"
              value={`${
                TRANSITION_LABEL[clip.transition_kind ?? ''] ?? clip.transition_kind
              } / ${(overlap / 1000).toFixed(1)} 秒`}
            />
          )}
        </dl>

        {/* ------------------------------------------------- 映像: リタイム */}
        {kind === 'video' && !isSpanless(clip) && (
          <div className="flex flex-col gap-1.5 border-t border-border pt-2">
            <span className="text-[11px] text-muted-foreground">
              速度（{speed}x）
            </span>
            <div className="flex flex-wrap items-center gap-1">
              {SPEED_PRESETS.map((preset) => (
                <Button
                  key={preset}
                  type="button"
                  size="sm"
                  variant={speed === preset ? 'secondary' : 'ghost'}
                  onClick={() => onSpeed(preset)}
                >
                  {preset}x
                </Button>
              ))}
              <input
                type="number"
                className="h-7 w-20 rounded-md border border-border bg-background px-2 text-[11px]"
                min={SPEED_MIN}
                max={SPEED_MAX}
                step={0.05}
                value={speed}
                aria-label="速度"
                onChange={(event) =>
                  onSpeed(clampSpeed(Number(event.target.value)))
                }
              />
            </div>
            <p className="text-[10px] text-muted-foreground">
              切り出しはそのままで、タイムライン上の長さだけが変わります
              （{SPEED_MIN}〜{SPEED_MAX} 倍）。
            </p>
          </div>
        )}

        {/* ------------------------------------------- 音声: 音量とフェード */}
        {kind === 'audio' && (
          <div className="flex flex-col gap-2 border-t border-border pt-2">
            <label className="flex flex-col gap-1 text-[11px]">
              <span className="text-muted-foreground">
                音量 {clip.gain_db > 0 ? '+' : ''}
                {clip.gain_db} dB
              </span>
              <input
                type="range"
                min={-40}
                max={12}
                step={1}
                value={clip.gain_db}
                onChange={(event) =>
                  onPatch({ gain_db: Number(event.target.value) })
                }
              />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <NumberField
                label="フェードイン (ms)"
                value={clip.fade_in_ms}
                max={clip.duration_ms}
                onChange={(value) => onPatch({ fade_in_ms: value })}
              />
              <NumberField
                label="フェードアウト (ms)"
                value={clip.fade_out_ms}
                max={clip.duration_ms}
                onChange={(value) => onPatch({ fade_out_ms: value })}
              />
            </div>
            <p className="text-[10px] text-muted-foreground">
              尺より長い BGM は端をドラッグして切ってください（ループ再生は
              できません）。
            </p>
          </div>
        )}

        {/* --------------------------------------------- 字幕: 本文と見た目 */}
        {kind === 'subtitle' && (
          <div className="flex flex-col gap-2 border-t border-border pt-2">
            <label className="flex flex-col gap-1 text-[11px]">
              <span className="text-muted-foreground">本文</span>
              <textarea
                className="min-h-16 rounded-md border border-border bg-background px-2 py-1 text-xs"
                value={subtitleText(clip)}
                onChange={(event) => onSubtitle({ text: event.target.value })}
              />
            </label>
            <div className="grid grid-cols-3 gap-2 text-[11px]">
              <Choice
                label="位置"
                value={style.position}
                options={SUBTITLE_POSITION_LABEL}
                onChange={(value) =>
                  onSubtitle({ style: { position: value as SubtitleStyle['position'] } })
                }
              />
              <Choice
                label="大きさ"
                value={style.size}
                options={SUBTITLE_SIZE_LABEL}
                onChange={(value) =>
                  onSubtitle({ style: { size: value as SubtitleStyle['size'] } })
                }
              />
              <Choice
                label="色"
                value={style.color}
                options={SUBTITLE_COLOR_LABEL}
                onChange={(value) =>
                  onSubtitle({ style: { color: value as SubtitleStyle['color'] } })
                }
              />
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-2 border-t border-border pt-2">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={onSplit}
            disabled={!canSplit}
            title={
              canSplit
                ? '再生ヘッドの位置で 2 つに割る'
                : '再生ヘッドをこのクリップの中（端から少し内側）へ動かしてください'
            }
          >
            <Scissors className="size-4" aria-hidden="true" />
            分割
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={onDelete}>
            <Trash2 className="size-4" aria-hidden="true" />
            削除
          </Button>
        </div>
        <p className="text-[11px] text-muted-foreground">
          端をドラッグでトリム、本体をドラッグで
          {kind === 'video' ? '並べ替え' : '移動'}。Delete で削除、
          Ctrl+Z / Ctrl+Shift+Z でやり直し。
        </p>
      </div>
    </Section>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-mono text-foreground/90">{value}</dd>
    </>
  )
}

function NumberField({
  label,
  value,
  max,
  onChange,
}: {
  label: string
  value: number
  max: number
  onChange: (value: number) => void
}) {
  return (
    <label className="flex flex-col gap-1 text-[11px]">
      <span className="text-muted-foreground">{label}</span>
      <input
        type="number"
        className="h-7 rounded-md border border-border bg-background px-2 text-[11px]"
        min={0}
        max={max}
        step={100}
        value={value}
        onChange={(event) =>
          onChange(Math.max(0, Math.min(max, Number(event.target.value) || 0)))
        }
      />
    </label>
  )
}

function Choice({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: Record<string, string>
  onChange: (value: string) => void
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-muted-foreground">{label}</span>
      <select
        className="h-7 rounded-md border border-border bg-background px-1 text-[11px]"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {Object.entries(options).map(([key, text]) => (
          <option key={key} value={key}>
            {text}
          </option>
        ))}
      </select>
    </label>
  )
}
