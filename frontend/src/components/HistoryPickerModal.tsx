import { useState } from 'react'
import { EyeOff, Film, Music } from 'lucide-react'
import type { Job } from '../types'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Modal, NsfwBadge } from './ui'

/** 入力欄の種別（そのままアセットのアップロード先になる）。 */
export type HistoryKind = 'image' | 'video' | 'audio'

/** 履歴の 1 出力（1 ジョブが画像とラストフレームの 2 件を出すこともある）。 */
export interface HistoryCandidate {
  /** 一覧の key（ジョブ 1 件から複数出るので job.id だけでは足りない） */
  id: string
  job: Job
  kind: HistoryKind
  /** 元の出力 URL（/outputs 配下。assets へはコピーしてから使う） */
  url: string
  /** サムネイルに使う画像 URL（音声・サムネの無い動画では null） */
  thumb: string | null
  /** 何の出力か（タイル上のラベル、コピー後のファイル名にも使う） */
  source: 'image' | 'last_frame' | 'video' | 'audio'
  label: string
}

/** バックエンド routers/assets.py の拡張子ホワイトリスト（種別ごと）。 */
const ALLOWED_EXT: Record<HistoryKind, string[]> = {
  image: ['.png', '.jpg', '.jpeg', '.webp', '.bmp'],
  video: ['.mp4', '.webm', '.mkv', '.mov'],
  audio: ['.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus'],
}

const FALLBACK_EXT: Record<HistoryKind, string> = {
  image: '.png',
  video: '.mp4',
  audio: '.mp3',
}

/**
 * 出力 URL から assets に許される拡張子を取り出す。
 *
 * 判別できない / ホワイトリストに無いものはその種別の既定にする（アップロードは
 * 拡張子で弾かれるので、推測できないまま投げて 400 にしない）。
 */
export function assetExtension(url: string, kind: HistoryKind): string {
  const name = url.split(/[?#]/)[0].split('/').pop() ?? ''
  const dot = name.lastIndexOf('.')
  const ext = dot >= 0 ? name.slice(dot).toLowerCase() : ''
  return ALLOWED_EXT[kind].includes(ext) ? ext : FALLBACK_EXT[kind]
}

/** ジョブが何を作ろうとしたか（タイルの説明・ツールチップ・検索対象）。 */
export function jobText(job: Job): string {
  return (
    job.video_prompt ??
    job.image_prompt ??
    job.audio_prompt ??
    job.user_input ??
    job.id
  )
}

/** そのジョブのサムネイルに使える画像（動画はラストフレームで代用）。 */
function thumbOf(job: Job): string | null {
  return job.last_frame_url ?? job.image_url ?? null
}

/**
 * 履歴から選べる出力を種別ごとに並べる（新しい順、完了ジョブのみ）。
 *
 * 画像入力には生成画像とラストフレームの両方が使えるので、1 ジョブから 2 件
 * 出ることがある（ラベルで区別する）。`showNsfw` がオフのあいだは NSFW の
 * ジョブを一覧に出さない。`query` を渡すと、そのジョブの文言（プロンプト等）に
 * 部分一致するものだけを残す。
 */
export function historyCandidates(
  jobs: Job[],
  kind: HistoryKind,
  showNsfw: boolean,
  query = '',
): HistoryCandidate[] {
  const needle = query.trim().toLowerCase()
  const candidates: HistoryCandidate[] = []
  for (const job of jobs) {
    if (job.status !== 'done') continue
    if (job.nsfw && !showNsfw) continue
    if (needle && !jobText(job).toLowerCase().includes(needle)) continue
    const add = (
      source: HistoryCandidate['source'],
      label: string,
      url: string | null,
      thumb: string | null,
    ) => {
      if (url) {
        candidates.push({ id: `${job.id}:${source}`, job, kind, url, thumb, source, label })
      }
    }
    if (kind === 'image') {
      add('image', '生成画像', job.image_url, job.image_url)
      add('last_frame', 'ラストフレーム', job.last_frame_url, job.last_frame_url)
    } else if (kind === 'video') {
      add('video', '動画', job.video_url, thumbOf(job))
    } else {
      add('audio', '生成音声', job.audio_output_url, null)
    }
  }
  return candidates
}

const EMPTY_HINT: Record<HistoryKind, string> = {
  image: '履歴に使える画像がまだありません（生成画像・動画のラストフレーム）。',
  video: '履歴に使える動画がまだありません。',
  audio: '履歴に使える音声がまだありません（音声モードで生成したもの）。',
}

function timestamp(job: Job): string {
  return job.created_at.replace('T', ' ').replace('+00:00', '').slice(0, 16)
}

/**
 * 履歴の生成物を入力リソースとして選ぶモーダル（開始フレーム / 最後のフレーム /
 * 参照動画 / リファレンス音声で共用）。
 *
 * NSFW 表示はモーダルの中だけの状態にする: 初期値はヘッダーのグローバル設定に
 * 従うが、ここでの切り替えは sessionStorage には残さない。
 */
export default function HistoryPickerModal({
  kind,
  title,
  jobs,
  showNsfw,
  onSelect,
  onClose,
}: {
  kind: HistoryKind
  title: string
  /** NSFW フィルタ前の全ジョブ（モーダル内で表示を切り替えるため） */
  jobs: Job[]
  /** ヘッダーの NSFW 表示トグル（初期値としてだけ使う） */
  showNsfw: boolean
  onSelect: (candidate: HistoryCandidate) => void
  onClose: () => void
}) {
  const [nsfw, setNsfw] = useState(showNsfw)
  const [query, setQuery] = useState('')
  const candidates = historyCandidates(jobs, kind, nsfw, query)

  return (
    <Modal title={title} onClose={onClose} closeOnBackdrop wide>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          className="max-w-[16rem] flex-1"
          type="search"
          aria-label="履歴を検索"
          placeholder="プロンプトで検索"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <span className="tnum text-xs text-muted-foreground">
          {candidates.length} 件
        </span>
        <div
          className={`chip ml-auto select-none !py-1 ${
            nsfw
              ? 'border-primary bg-primary/15 text-foreground'
              : 'border-border bg-surface-sunken text-muted-foreground'
          }`}
          title="オフのあいだは NSFW の作品を一覧から隠します（この画面かぎりの設定）"
        >
          <Switch id="history-picker-nsfw" checked={nsfw} onCheckedChange={setNsfw} />
          <Label
            htmlFor="history-picker-nsfw"
            className="flex cursor-pointer items-center gap-1 text-xs"
          >
            <EyeOff className="size-3" aria-hidden="true" />
            NSFW表示
          </Label>
        </div>
      </div>

      {candidates.length === 0 ? (
        <p className="py-6 text-center text-xs text-muted-foreground">
          {query.trim() ? '検索に一致する生成物がありません。' : EMPTY_HINT[kind]}
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {candidates.map((candidate) => (
            <button
              key={candidate.id}
              title={jobText(candidate.job)}
              className="group relative overflow-hidden rounded-md border border-border bg-surface-sunken text-left shadow-elevation-1 transition-colors hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              onClick={() => onSelect(candidate)}
            >
              <div className="flex h-24 items-center justify-center bg-background">
                {candidate.thumb ? (
                  <img src={candidate.thumb} alt="" className="size-full object-cover" />
                ) : candidate.kind === 'audio' ? (
                  <Music className="size-7 opacity-60" />
                ) : (
                  <Film className="size-7 opacity-60" />
                )}
              </div>
              <span className="absolute left-1 top-1 chip !px-1.5 !py-0.5 border-border bg-black/70 text-[10px] text-foreground/85">
                {candidate.label}
              </span>
              {nsfw && candidate.job.nsfw && (
                <span className="absolute right-1 top-1">
                  <NsfwBadge />
                </span>
              )}
              <div className="p-1.5">
                <p className="truncate text-[11px] text-foreground/85">
                  {jobText(candidate.job)}
                </p>
                <p className="tnum text-[10px] text-muted-foreground">
                  {timestamp(candidate.job)}
                </p>
              </div>
            </button>
          ))}
        </div>
      )}
    </Modal>
  )
}
