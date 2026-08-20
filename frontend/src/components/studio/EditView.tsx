import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Plus, Redo2, Undo2 } from 'lucide-react'

import { ApiError, api, formatDetail } from '../../api'
import type {
  StudioEpisode,
  StudioTimeline,
  StudioTimelineDetail,
  TimelineClip,
  TimelineExport,
  TimelineExportProgress,
} from '../../types'
import { Banner } from '../ui'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import ClipInspector from './ClipInspector'
import ExportPanel from './ExportPanel'
import PreviewMonitor from './PreviewMonitor'
import TimelinePane from './TimelinePane'
import { episodeLabel } from './studio'
import {
  AUTOSAVE_DELAY_MS,
  MIN_CLIP_MS,
  SAVE_STATE_CLASS,
  SAVE_STATE_LABEL,
  ZOOM_DEFAULT,
  type History,
  type SaveState,
  canRedo,
  canUndo,
  initHistory,
  moveClip,
  orderedClips,
  pushHistory,
  redo,
  removeClip,
  sameClips,
  splitClipAt,
  toClipInputs,
  totalDuration,
  trimClip,
  undo,
  videoTrackOf,
} from './timeline'

/**
 * 編集タブ。焼き上がったテイクを並べ直して 1 本の動画に書き出す面。
 *
 * 画面の持ち方は制作タブと違って**楽観的**: クリップの並びはここが正で、
 * 操作するたびに手元の配列を書き換え、1〜2 秒静まったら `PUT /clips` で
 * サーバーへ流す（保存状態はインジケータに出る）。やり直し（Undo / Redo）も
 * 画面の中の履歴で行う。
 *
 * フェーズ 1 が扱うのは V1（映像トラック 1 本）だけで、クリップは常に隙間なく
 * 詰まっている（リップル方式）。
 */
export default function EditView({
  projectId,
  episodes,
  exportEvent,
}: {
  projectId: string
  episodes: StudioEpisode[]
  /** WS の書き出し進捗（`type: "timeline_export"` の最新フレーム）。 */
  exportEvent?: TimelineExportProgress | null
}) {
  const [timelines, setTimelines] = useState<StudioTimeline[]>([])
  const [timeline, setTimeline] = useState<StudioTimelineDetail | null>(null)
  const [timelineId, setTimelineId] = useState<string | null>(null)
  const [episodeId, setEpisodeId] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [history, setHistory] = useState<History<TimelineClip[]>>(initHistory([]))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [playheadMs, setPlayheadMs] = useState(0)
  const [zoom, setZoom] = useState(ZOOM_DEFAULT)
  const [saveState, setSaveState] = useState<SaveState>('saved')

  const [exports, setExports] = useState<TimelineExport[]>([])
  const [savingId, setSavingId] = useState<string | null>(null)

  const clips = history.present

  const pushError = useCallback((cause: unknown) => {
    setError(
      cause instanceof ApiError
        ? formatDetail(cause.detail)
        : cause instanceof Error
          ? cause.message
          : String(cause),
    )
  }, [])

  // ------------------------------------------------------------ 読み込み
  const loadTimelines = useCallback(async () => {
    setLoading(true)
    try {
      const found = await api.listStudioTimelines(projectId)
      setTimelines(found)
      // 開くのは「まだ何も開いていなければ一番新しいもの」。既に開いている
      // タイムラインが消えていたら、そのときも新しいものへ落とす。
      setTimelineId((current) =>
        current && found.some((item) => item.id === current)
          ? current
          : (found[found.length - 1]?.id ?? null),
      )
    } catch (cause) {
      pushError(cause)
    } finally {
      setLoading(false)
    }
  }, [projectId, pushError])

  useEffect(() => {
    void loadTimelines()
  }, [loadTimelines])

  // 作品を切り替えたら編集の状態は持ち越さない。
  useEffect(() => {
    setTimelineId(null)
    setTimeline(null)
    setHistory(initHistory([]))
    setSelectedId(null)
    setPlayheadMs(0)
    setExports([])
  }, [projectId])

  /** サーバーの EDL を画面へ入れ直す（履歴もそこで切る）。 */
  const adoptTimeline = useCallback((detail: StudioTimelineDetail) => {
    setTimeline(detail)
    setHistory(initHistory(orderedClips(videoTrackOf(detail))))
    setSaveState('saved')
  }, [])

  const loadTimeline = useCallback(
    async (id: string) => {
      setLoading(true)
      try {
        adoptTimeline(await api.getStudioTimeline(id))
        setExports(await api.listStudioTimelineExports(id))
      } catch (cause) {
        pushError(cause)
      } finally {
        setLoading(false)
      }
    },
    [adoptTimeline, pushError],
  )

  useEffect(() => {
    if (!timelineId) {
      setTimeline(null)
      setHistory(initHistory([]))
      setExports([])
      return
    }
    void loadTimeline(timelineId)
  }, [timelineId, loadTimeline])

  // ------------------------------------------------------------ 自動保存
  //
  // 操作のたびに投げず、静まってから 1 回だけ送る。送る中身は「そのとき手元に
  // あった並び」なので、途中の状態は飛ばしてよい。
  const savedRef = useRef<TimelineClip[]>([])
  useEffect(() => {
    savedRef.current = orderedClips(videoTrackOf(timeline))
  }, [timeline])

  useEffect(() => {
    if (!timelineId || !timeline) return
    if (sameClips(clips, savedRef.current)) return
    setSaveState('pending')
    const timer = window.setTimeout(() => {
      void (async () => {
        setSaveState('saving')
        try {
          const fresh = await api.replaceStudioTimelineClips(
            timelineId,
            toClipInputs(clips),
          )
          setTimeline(fresh)
          // サーバーが採番した id を手元へ取り込む（分割した直後の一時 id が
          // 残っていると、次の保存で毎回作り直しになる）。履歴は切らずに、
          // 「いまの状態」だけ差し替える。
          setHistory((current) => ({
            ...current,
            present: orderedClips(videoTrackOf(fresh)),
          }))
          setSaveState('saved')
        } catch (cause) {
          setSaveState('failed')
          pushError(cause)
        }
      })()
    }, AUTOSAVE_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [clips, timelineId, timeline, pushError])

  // -------------------------------------------------------- 書き出しの進捗
  //
  // WS のフレームは「いま走っている 1 本」の状態。終端に来たら履歴を取り直して
  // 成果物の URL を拾う（フレームを取りこぼしても、次の操作で追いつける）。
  useEffect(() => {
    if (!exportEvent || !timelineId || exportEvent.timeline_id !== timelineId) return
    setExports((current) => {
      const index = current.findIndex((item) => item.id === exportEvent.export_id)
      if (index < 0) return current
      const next = [...current]
      next[index] = {
        ...next[index],
        status: exportEvent.status,
        progress: exportEvent.progress,
        output_url: exportEvent.output_url ?? next[index].output_url,
        error: exportEvent.error ?? next[index].error,
      }
      return next
    })
    if (exportEvent.status === 'done' || exportEvent.status === 'failed') {
      void api.listStudioTimelineExports(timelineId).then(setExports, pushError)
    }
  }, [exportEvent, timelineId, pushError])

  const running = useMemo(
    () =>
      exports.find(
        (item) => item.status === 'queued' || item.status === 'running',
      ) ?? null,
    [exports],
  )

  // WS を取りこぼしても止まらないように、走っているあいだは定期的に取り直す。
  useEffect(() => {
    if (!timelineId || !running) return
    const timer = window.setInterval(() => {
      void api.listStudioTimelineExports(timelineId).then(setExports, () => undefined)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [timelineId, running])

  // ---------------------------------------------------------------- 編集操作
  const apply = useCallback(
    (change: (current: TimelineClip[]) => TimelineClip[]) => {
      setHistory((current) => pushHistory(current, change(current.present), sameClips))
    },
    [],
  )

  const selected = clips.find((clip) => clip.id === selectedId) ?? null
  const selectedIndex = clips.findIndex((clip) => clip.id === selectedId)
  const total = totalDuration(clips)

  /** 再生ヘッドが選択クリップの中にあり、割っても両側が短くなりすぎないか。 */
  const canSplit = useMemo(() => {
    if (!selected) return false
    const offset = playheadMs - selected.start_ms
    return offset >= MIN_CLIP_MS && selected.duration_ms - offset >= MIN_CLIP_MS
  }, [selected, playheadMs])

  const splitSelected = useCallback(() => {
    if (!selectedId) return
    apply((current) => splitClipAt(current, selectedId, playheadMs))
  }, [apply, selectedId, playheadMs])

  const deleteSelected = useCallback(() => {
    if (!selectedId) return
    apply((current) => removeClip(current, selectedId))
    setSelectedId(null)
  }, [apply, selectedId])

  // ショートカット。入力欄にフォーカスがあるときは奪わない（作品名の編集など）。
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (
        target &&
        (target.isContentEditable ||
          ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
      )
        return

      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        setHistory((current) => (event.shiftKey ? redo(current) : undo(current)))
        return
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
        event.preventDefault()
        setHistory(redo)
        return
      }
      if (event.key === 'Delete' || event.key === 'Backspace') {
        if (!selectedId) return
        event.preventDefault()
        deleteSelected()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedId, deleteSelected])

  // ---------------------------------------------------------------- actions
  const createTimeline = () =>
    void (async () => {
      setBusy(true)
      setError(null)
      try {
        const created = await api.createStudioTimeline(projectId, {
          episode_id: episodeId || null,
        })
        setTimelines(await api.listStudioTimelines(projectId))
        setTimelineId(created.id)
        adoptTimeline(created)
        setExports([])
        setSelectedId(null)
        setPlayheadMs(0)
      } catch (cause) {
        pushError(cause)
      } finally {
        setBusy(false)
      }
    })()

  const startExport = () =>
    void (async () => {
      if (!timelineId) return
      setBusy(true)
      setError(null)
      try {
        await api.exportStudioTimeline(timelineId)
        setExports(await api.listStudioTimelineExports(timelineId))
      } catch (cause) {
        pushError(cause)
      } finally {
        setBusy(false)
      }
    })()

  const saveToLibrary = (exportId: string) =>
    void (async () => {
      setSavingId(exportId)
      setError(null)
      try {
        await api.saveStudioExportToLibrary(exportId, timeline?.name ?? '')
      } catch (cause) {
        pushError(cause)
      } finally {
        setSavingId(null)
      }
    })()

  // ----------------------------------------------------------------- render
  const banner = error && (
    <Banner onClose={() => setError(null)}>{error}</Banner>
  )

  return (
    <div className="flex flex-col gap-3">
      {banner}

      {/* 話を選んでタイムラインを作る / 既にあるものを開く */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-2">
        <label className="text-xs text-muted-foreground" htmlFor="edit-episode">
          話
        </label>
        <select
          id="edit-episode"
          className="h-8 rounded-md border border-border bg-background px-2 text-xs"
          value={episodeId}
          disabled={busy}
          onChange={(event) => setEpisodeId(event.target.value)}
        >
          <option value="">作品まるごと（空のタイムライン）</option>
          {episodes.map((episode, index) => (
            <option key={episode.id} value={episode.id}>
              {episodeLabel(episode, index)}
            </option>
          ))}
        </select>
        <Button type="button" size="sm" onClick={createTimeline} disabled={busy}>
          {busy ? (
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <Plus className="size-4" aria-hidden="true" />
          )}
          タイムラインを作成
        </Button>

        {timelines.length > 0 && (
          <>
            <label
              className="ml-2 text-xs text-muted-foreground"
              htmlFor="edit-timeline"
            >
              開く
            </label>
            <select
              id="edit-timeline"
              className="h-8 max-w-56 rounded-md border border-border bg-background px-2 text-xs"
              value={timelineId ?? ''}
              disabled={busy}
              onChange={(event) => setTimelineId(event.target.value || null)}
            >
              {timelines.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name || item.id}
                </option>
              ))}
            </select>
          </>
        )}

        <div className="ml-auto flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setHistory(undo)}
            disabled={!canUndo(history)}
            title="元に戻す（Ctrl+Z）"
          >
            <Undo2 className="size-4" aria-hidden="true" />
            <span className="sr-only">元に戻す</span>
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setHistory(redo)}
            disabled={!canRedo(history)}
            title="やり直す（Ctrl+Shift+Z）"
          >
            <Redo2 className="size-4" aria-hidden="true" />
            <span className="sr-only">やり直す</span>
          </Button>
          <Badge className={SAVE_STATE_CLASS[saveState]}>
            {SAVE_STATE_LABEL[saveState]}
          </Badge>
        </div>
      </div>

      {loading && !timeline && (
        <p className="text-xs text-muted-foreground">読み込んでいます…</p>
      )}

      {!loading && !timeline && (
        <p className="rounded-lg border border-border bg-card p-4 text-xs text-muted-foreground">
          まだタイムラインがありません。話を選んで「タイムラインを作成」を押すと、
          その話の採用テイクを順番に並べたタイムラインができます。
        </p>
      )}

      {timeline && (
        <div className="flex flex-col gap-3 lg:flex-row">
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <PreviewMonitor
              clips={clips}
              playheadMs={playheadMs}
              onSeek={setPlayheadMs}
            />
            <TimelinePane
              clips={clips}
              selectedId={selectedId}
              playheadMs={playheadMs}
              zoom={zoom}
              onZoom={setZoom}
              onSelect={setSelectedId}
              onSeek={(ms) => setPlayheadMs(Math.min(ms, Math.max(total, 0)))}
              onMove={(id, to) => apply((current) => moveClip(current, id, to))}
              onTrim={(id, edge, deltaMs) =>
                apply((current) => trimClip(current, id, edge, deltaMs))
              }
            />
          </div>

          <div className="flex w-full shrink-0 flex-col gap-3 lg:w-80">
            <ClipInspector
              clip={selected}
              index={selectedIndex}
              canSplit={canSplit}
              onSplit={splitSelected}
              onDelete={deleteSelected}
            />
            <ExportPanel
              exports={exports}
              running={running}
              busy={busy}
              savingId={savingId}
              canExport={clips.length > 0}
              onExport={startExport}
              onSaveToLibrary={saveToLibrary}
            />
          </div>
        </div>
      )}
    </div>
  )
}
