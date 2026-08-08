import { useEffect, useMemo, useState } from 'react'
import type {
  JobProgress,
  StudioAsset,
  StudioShot,
  StudioTake,
} from '../../types'
import { Banner, Section } from '../ui'
import {
  TAKE_STATUS_CLASS,
  TAKE_STATUS_LABEL,
  assetHasFile,
  isStale,
  selectedTakeOf,
  splitMentions,
  staleTooltip,
  takesByShot,
  unresolvedMentions,
} from './studio'

function fileNameOf(url: string): string {
  const clean = url.split('?')[0]
  return clean.slice(clean.lastIndexOf('/') + 1) || 'take.mp4'
}

/**
 * 解決できた `@素材名` 1 つ。
 *
 * ファイルを持つ素材は参照として添付されるが、メタデータのみの素材は添付されず
 * 投入時に説明文へ置き換わる——どちらの扱いになるか本文の上で分かるように、色と
 * 📝 の印で分ける。
 */
function MentionSpan({ text, asset }: { text: string; asset: StudioAsset }) {
  const description = asset.prompt_caption || asset.caption
  if (assetHasFile(asset)) {
    return (
      <span
        className="rounded bg-accent-500/15 px-1 font-mono text-accent-400"
        title={description}
      >
        {text}
      </span>
    )
  }
  return (
    <span
      className="rounded bg-slate-700/50 px-1 font-mono text-slate-300 underline decoration-dotted"
      title={`ファイルなしの素材です。投入時は説明文として展開されます${
        description ? `: ${description}` : ''
      }`}
    >
      {text}
      <span className="ml-0.5 text-[10px] text-slate-400">📝</span>
    </span>
  )
}

/** プロンプトを読み下しつつ `@素材名` を色分けする（未解決は赤）。 */
function PromptPreview({
  prompt,
  assets,
}: {
  prompt: string
  assets: StudioAsset[]
}) {
  const segments = useMemo(() => splitMentions(prompt, assets), [prompt, assets])
  if (!prompt.trim()) {
    return (
      <p className="text-xs text-slate-600">
        プロンプトが空です（脚本タブで書いてください）
      </p>
    )
  }
  const metaOnly = segments.some(
    (segment) => segment.asset && !assetHasFile(segment.asset),
  )
  return (
    <>
      <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-300">
        {segments.map((segment, index) =>
          segment.asset ? (
            <MentionSpan key={index} text={segment.text} asset={segment.asset} />
          ) : segment.unresolved ? (
            <span
              key={index}
              className="rounded bg-red-950/60 px-1 font-mono text-red-300"
              title="World Bible に無い素材です"
            >
              {segment.text}
            </span>
          ) : (
            <span key={index}>{segment.text}</span>
          ),
        )}
      </p>
      {metaOnly && (
        <p className="mt-1 text-[10px] text-slate-500">
          📝 が付いた素材はファイルを持たないため、参照には添付されず説明文として
          本文に展開されます。
        </p>
      )}
    </>
  )
}

function TakeCard({
  take,
  index,
  active,
  progress,
  onPreview,
  onSelect,
  onReject,
  onDelete,
  busy,
}: {
  take: StudioTake
  index: number
  active: boolean
  progress?: JobProgress
  onPreview: () => void
  onSelect: () => void
  onReject: () => void
  onDelete: () => void
  busy: boolean
}) {
  const percent =
    take.status === 'rendering' && progress?.progress != null
      ? Math.round(progress.progress * 100)
      : null
  const stale = isStale(take)
  return (
    <li
      className={`rounded-md border p-1.5 ${
        active ? 'border-accent-500 bg-accent-500/10' : 'border-ink-600 bg-ink-800'
      }`}
    >
      <button
        className="flex w-full items-center gap-2 text-left"
        onClick={onPreview}
        aria-current={active ? 'true' : undefined}
      >
        <span className="h-10 w-14 shrink-0 overflow-hidden rounded bg-ink-900">
          {take.last_frame_url ? (
            <img src={take.last_frame_url} alt="" className="h-full w-full object-cover" />
          ) : (
            <span className="flex h-full w-full items-center justify-center text-[10px] text-slate-600">
              {take.status === 'rendering' ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink-500 border-t-accent-500" />
              ) : (
                '—'
              )}
            </span>
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1">
            <span className="text-[11px] text-slate-400">Take {index + 1}</span>
            <span
              className={`chip !px-1.5 !py-0 text-[10px] ${TAKE_STATUS_CLASS[take.status]}`}
            >
              {TAKE_STATUS_LABEL[take.status]}
            </span>
            {stale && (
              <span
                className="chip !px-1.5 !py-0 border-amber-800 bg-amber-950 text-[10px] text-amber-300"
                title={staleTooltip(take)}
              >
                ⚠ 要再生成
              </span>
            )}
          </span>
          <span className="mt-0.5 block truncate text-[10px] text-slate-600">
            {percent != null ? `${percent}%` : (take.video_workflow ?? take.job_id)}
          </span>
        </span>
      </button>

      {stale && (take.stale_reasons?.length ?? 0) > 0 && (
        <ul className="mt-1 space-y-0.5">
          {take.stale_reasons?.map((reason) => (
            <li key={reason} className="text-[10px] text-amber-400/90">
              ⚠ {reason}
            </li>
          ))}
        </ul>
      )}

      {take.warning && (
        <p className="mt-1 text-[10px] text-amber-400" title={take.warning}>
          ⚠ {take.warning}
        </p>
      )}

      {take.error && (
        <p className="mt-1 line-clamp-2 text-[10px] text-red-400" title={take.error}>
          {take.error}
        </p>
      )}

      <div className="mt-1 flex flex-wrap items-center gap-1">
        <button
          className="btn-ghost !px-1.5 !py-0.5 text-[10px]"
          onClick={onSelect}
          disabled={busy || take.status === 'rendering' || !take.video_url}
        >
          採用
        </button>
        <button
          className="btn-ghost !px-1.5 !py-0.5 text-[10px]"
          onClick={onReject}
          disabled={busy || take.status === 'rendering'}
        >
          却下
        </button>
        {take.video_url && (
          <a
            className="btn-ghost !px-1.5 !py-0.5 text-[10px]"
            href={take.video_url}
            download={fileNameOf(take.video_url)}
          >
            保存
          </a>
        )}
        <button
          className="btn-danger !px-1.5 !py-0.5 text-[10px]"
          onClick={onDelete}
          disabled={busy}
        >
          削除
        </button>
      </div>
    </li>
  )
}

/**
 * 選んでいる Take の詳細: 実際に投入した本文と、英訳する前の原文。
 *
 * 自動英訳をオンにしていると投入本文は英語になるので、原文を畳んで併記して
 * 「どこがどう訳されたのか」を後から追えるようにする。
 */
function TakeDetail({ take, index }: { take: StudioTake; index: number }) {
  if (!take.prompt && !take.source_prompt && !take.warning) return null
  return (
    <Section
      title={`Take ${index + 1} の投入内容`}
      right={
        take.video_workflow ? (
          <span className="text-[11px] text-slate-500">{take.video_workflow}</span>
        ) : undefined
      }
    >
      {take.warning && (
        <div className="mb-2">
          <Banner tone="warn">{take.warning}</Banner>
        </div>
      )}
      {take.prompt && (
        <>
          <h4 className="label">実際に投入したプロンプト</h4>
          <p className="whitespace-pre-wrap break-words rounded-md border border-ink-600 bg-ink-900/60 p-2 text-xs leading-relaxed text-slate-300">
            {take.prompt}
          </p>
        </>
      )}
      {take.source_prompt && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-slate-500">
            英訳する前の原文を見る
          </summary>
          <p className="mt-1 whitespace-pre-wrap break-words rounded-md border border-ink-600 bg-ink-900/60 p-2 text-xs leading-relaxed text-slate-400">
            {take.source_prompt}
          </p>
        </details>
      )}
    </Section>
  )
}

/**
 * 制作タブ: 選んだカットのプロンプト確認 -> 生成 -> Take の採用まで。
 *
 * 中央にプレイヤー、右に Take レール、下に全カットのタイムライン。
 */
export default function ProductionView({
  shots,
  assets,
  allTakes,
  selectedShot,
  onSelectShot,
  progress,
  onRender,
  onSelectTake,
  onRejectTake,
  onDeleteTake,
  busy,
}: {
  shots: StudioShot[]
  assets: StudioAsset[]
  allTakes: StudioTake[]
  selectedShot: StudioShot | null
  onSelectShot: (id: string) => void
  progress: Record<string, JobProgress>
  onRender: (shotId: string) => void
  onSelectTake: (takeId: string) => void
  onRejectTake: (takeId: string) => void
  onDeleteTake: (takeId: string) => void
  busy: boolean
}) {
  const grouped = useMemo(() => takesByShot(allTakes), [allTakes])
  // 参照を安定させておく（下の useEffect の依存に入るので、毎回新しい [] を
  // 作ると効果が回りっぱなしになる）。
  const takes = useMemo(
    () => (selectedShot ? (grouped[selectedShot.id] ?? []) : []),
    [grouped, selectedShot],
  )
  const [previewId, setPreviewId] = useState<string | null>(null)

  // カットを切り替えたら、採用済み（無ければ最後の再生できる Take）を出す。
  useEffect(() => {
    if (previewId && takes.some((take) => take.id === previewId)) return
    const fallback =
      (selectedShot ? selectedTakeOf(selectedShot, takes) : null) ??
      [...takes].reverse().find((take) => take.video_url) ??
      null
    setPreviewId(fallback?.id ?? null)
  }, [takes, selectedShot, previewId])

  if (!selectedShot) {
    return (
      <p className="rounded-md border border-ink-600 bg-ink-800 px-3 py-8 text-center text-xs text-slate-600">
        左のレールでカットを選ぶと、ここで生成できます
      </p>
    )
  }

  const preview = takes.find((take) => take.id === previewId) ?? null
  const rendering = takes.some((take) => take.status === 'rendering')
  const renderingTake = takes.find((take) => take.status === 'rendering') ?? null
  const renderingProgress = renderingTake ? progress[renderingTake.job_id] : undefined
  const unresolved = unresolvedMentions(selectedShot.prompt, assets)

  return (
    <div className="space-y-3">
      <div className="grid min-h-0 gap-3 lg:grid-cols-[1fr_18rem]">
        <div className="space-y-3">
          <Section
            title={`${selectedShot.title || 'カット'} のプロンプト`}
            right={
              <span className="text-[11px] text-slate-500">
                {selectedShot.duration_seconds}s
                {selectedShot.carry_over_end_frame ? ' / 続きから' : ''}
              </span>
            }
          >
            <PromptPreview prompt={selectedShot.prompt} assets={assets} />
            {unresolved.length > 0 && (
              <div className="mt-2">
                <Banner tone="warn">
                  World Bible に無い素材を指しています: {unresolved.join(' ')}
                </Banner>
              </div>
            )}
            <div className="mt-3 flex items-center gap-2">
              <button
                className="btn-primary"
                onClick={() => onRender(selectedShot.id)}
                disabled={busy || rendering}
              >
                {rendering ? '生成中…' : '生成'}
              </button>
              {rendering && (
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-700">
                    <div
                      className="h-full bg-accent-500 transition-all"
                      style={{
                        width: `${Math.round((renderingProgress?.progress ?? 0) * 100)}%`,
                      }}
                    />
                  </div>
                  <span className="shrink-0 text-[11px] text-slate-500">
                    {renderingProgress?.message ??
                      renderingProgress?.status ??
                      'キュー待ち'}
                  </span>
                </div>
              )}
            </div>
          </Section>

          <Section title="プレビュー">
            {preview?.video_url ? (
              <video
                key={preview.id}
                src={preview.video_url}
                controls
                className="max-h-[46vh] w-full rounded-md bg-black"
              />
            ) : (
              <p className="px-3 py-10 text-center text-xs text-slate-600">
                再生できる Take がまだありません
              </p>
            )}
          </Section>

          {preview && <TakeDetail take={preview} index={takes.indexOf(preview)} />}
        </div>

        <Section title={`Take（${takes.length}）`}>
          {takes.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-slate-600">
              まだ生成していません
            </p>
          ) : (
            <ul className="max-h-[60vh] space-y-1.5 overflow-y-auto">
              {takes.map((take, index) => (
                <TakeCard
                  key={take.id}
                  take={take}
                  index={index}
                  active={take.id === previewId}
                  progress={progress[take.job_id]}
                  busy={busy}
                  onPreview={() => setPreviewId(take.id)}
                  onSelect={() => onSelectTake(take.id)}
                  onReject={() => onRejectTake(take.id)}
                  onDelete={() => onDeleteTake(take.id)}
                />
              ))}
            </ul>
          )}
        </Section>
      </div>

      <Section title="タイムライン">
        <div className="flex gap-2 overflow-x-auto pb-1">
          {shots.length === 0 && (
            <p className="w-full py-4 text-center text-xs text-slate-600">
              カットがありません
            </p>
          )}
          {shots.map((shot, index) => {
            const take = selectedTakeOf(shot, allTakes)
            const active = shot.id === selectedShot.id
            return (
              <button
                key={shot.id}
                className={`w-28 shrink-0 overflow-hidden rounded-md border text-left transition-colors ${
                  active
                    ? 'border-accent-500 ring-2 ring-accent-500/60'
                    : 'border-ink-600 hover:border-ink-500'
                }`}
                onClick={() => onSelectShot(shot.id)}
                title={shot.title || `カット ${index + 1}`}
              >
                <span className="block h-16 w-full bg-ink-900">
                  {take?.last_frame_url ? (
                    <img
                      src={take.last_frame_url}
                      alt=""
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <span className="flex h-full w-full items-center justify-center text-[10px] text-slate-600">
                      未採用
                    </span>
                  )}
                </span>
                <span className="block truncate bg-ink-800 px-1.5 py-1 text-[10px] text-slate-300">
                  {String(index + 1).padStart(2, '0')}{' '}
                  {shot.title || `カット ${index + 1}`}
                </span>
              </button>
            )
          })}
        </div>
      </Section>
    </div>
  )
}
