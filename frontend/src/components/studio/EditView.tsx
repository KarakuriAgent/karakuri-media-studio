import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Loader2, Plus, Redo2, RefreshCw, Type, Undo2 } from 'lucide-react'

import { ApiError, api, formatDetail } from '../../api'
import type {
  StudioEpisode,
  StudioTimeline,
  StudioTimelineDetail,
  TimelineClip,
  TimelineExport,
  TimelineExportProgress,
  TimelineExportRequest,
  TimelineFx,
  TimelineMediaItem,
  TimelineMissingFix,
  TimelineMissingReport,
  TimelineSyncPreview,
  TimelineSyncRequest,
} from '../../types'
import { Banner } from '../ui'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import ClipInspector from './ClipInspector'
import ExportPanel from './ExportPanel'
import FxInspector from './FxInspector'
import MediaBin from './MediaBin'
import MissingDialog from './MissingDialog'
import PreviewMonitor from './PreviewMonitor'
import SyncDialog from './SyncDialog'
import TimelinePane from './TimelinePane'
import { fxApplyLocal, fxMovedTo, fxResizedTo } from './fx'
import { episodeLabel } from './studio'
import {
  AUTOSAVE_DELAY_MS,
  MIN_CLIP_MS,
  SAVE_STATE_CLASS,
  SAVE_STATE_LABEL,
  ZOOM_DEFAULT,
  type History,
  type SaveState,
  type SubtitleStyle,
  allClipsOf,
  applyToTrack,
  audioTracksOf,
  canRedo,
  canUndo,
  clipFromMedia,
  clipsOfTrack,
  firstFreeSlot,
  initHistory,
  moveClip,
  moveClipTo,
  newSubtitleClip,
  patchClip,
  pushHistory,
  redo,
  removeClip,
  removeClipFree,
  ripple,
  sameClips,
  setSpeed,
  setSubtitle,
  setTransition,
  splitClipAt,
  subtitleTrackOf,
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
 * 履歴が持つのは**全トラックのクリップ**（V1 / A1… / T1）で、トラックの出し入れ
 * だけはサーバー側の操作（`POST /tracks` など）。トラックを触る前には手元の
 * 変更を流し切ってから走らせる（返ってきた EDL で丸ごと入れ替わるため）。
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

  const [sync, setSync] = useState<TimelineSyncPreview | null>(null)
  const [syncOpen, setSyncOpen] = useState(false)
  const [missing, setMissing] = useState<TimelineMissingReport | null>(null)

  // FX トラック（演出）。Remotion 連携が OFF のあいだは段ごと出さない
  // （プレビューの `@remotion/player` も読み込まない）。
  const [fxEnabled, setFxEnabled] = useState(false)
  const [fx, setFx] = useState<TimelineFx | null>(null)
  const [fxSelectedId, setFxSelectedId] = useState<string | null>(null)
  const [fxBusy, setFxBusy] = useState(false)
  /** 演出を書き換えるときに添える `base_revision`（§7.4 の楽観ロック）。 */
  const baseRevision = useRef<number | null>(null)

  const clips = history.present
  const tracks = timeline?.tracks ?? []
  const videoTrack = videoTrackOf(timeline)
  const videoTrackId = videoTrack?.id ?? null
  const audioTracks = audioTracksOf(timeline)
  const subtitleTrack = subtitleTrackOf(timeline)

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

  // Remotion 連携の ON / OFF（FX トラックを出すかどうか）。
  useEffect(() => {
    void api
      .getSettings()
      .then((settings) => setFxEnabled(settings.remotion_enabled))
      .catch(() => setFxEnabled(false))
  }, [])

  // 作品を切り替えたら編集の状態は持ち越さない。
  useEffect(() => {
    setTimelineId(null)
    setTimeline(null)
    setHistory(initHistory([]))
    setSelectedId(null)
    setPlayheadMs(0)
    setExports([])
    setSync(null)
  }, [projectId])

  /** サーバーの EDL を画面へ入れ直す（履歴もそこで切る）。 */
  const adoptTimeline = useCallback((detail: StudioTimelineDetail) => {
    setTimeline(detail)
    setHistory(initHistory(allClipsOf(detail)))
    setSaveState('saved')
  }, [])

  /** いまのリビジョン連番を控える（次の演出の書き換えに添える）。 */
  const refreshRevision = useCallback(async () => {
    const rows = await api.listStudioRevisions(projectId)
    baseRevision.current = rows[0]?.seq ?? 0
  }, [projectId])

  const loadTimeline = useCallback(
    async (id: string) => {
      setLoading(true)
      try {
        adoptTimeline(await api.getStudioTimeline(id))
        setExports(await api.listStudioTimelineExports(id))
        setSync(await api.getStudioTimelineSyncPreview(id))
        if (fxEnabled) {
          setFx(await api.getStudioTimelineFx(id))
          await refreshRevision()
        }
      } catch (cause) {
        pushError(cause)
      } finally {
        setLoading(false)
      }
    },
    [adoptTimeline, pushError, fxEnabled, refreshRevision],
  )

  useEffect(() => {
    if (!timelineId) {
      setTimeline(null)
      setHistory(initHistory([]))
      setExports([])
      setSync(null)
      setFx(null)
      setFxSelectedId(null)
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
    savedRef.current = allClipsOf(timeline)
  }, [timeline])

  const save = useCallback(
    async (target: TimelineClip[]) => {
      if (!timelineId) return null
      setSaveState('saving')
      const fresh = await api.replaceStudioTimelineClips(
        timelineId,
        toClipInputs(target),
      )
      setTimeline(fresh)
      // サーバーが採番した id を手元へ取り込む（分割した直後の一時 id が
      // 残っていると、次の保存で毎回作り直しになる）。履歴は切らずに、
      // 「いまの状態」だけ差し替える。
      setHistory((current) => ({ ...current, present: allClipsOf(fresh) }))
      setSaveState('saved')
      return fresh
    },
    [timelineId],
  )

  useEffect(() => {
    if (!timelineId || !timeline) return
    if (sameClips(clips, savedRef.current)) return
    setSaveState('pending')
    const timer = window.setTimeout(() => {
      void save(clips).catch((cause) => {
        setSaveState('failed')
        pushError(cause)
      })
    }, AUTOSAVE_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [clips, timelineId, timeline, save, pushError])

  /**
   * 手元の変更を流し切ってから、トラックを触る操作を走らせる。
   *
   * トラックの出し入れはサーバー側の操作で、返ってくるのは「サーバーが持って
   * いる EDL」。先に保存しておかないと、まだ送っていないクリップの編集が
   * その差し替えで消える。
   */
  const withFlush = useCallback(
    async (run: () => Promise<StudioTimelineDetail>) => {
      if (!sameClips(clips, savedRef.current)) await save(clips)
      adoptTimeline(await run())
    },
    [clips, save, adoptTimeline],
  )

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

  /** 演出（`fx: true`）のレンダリングが続いている書き出し。 */
  const fxRunning = useMemo(
    () =>
      exports.find(
        (item) => item.fx_status === 'queued' || item.fx_status === 'running',
      ) ?? null,
    [exports],
  )

  // WS を取りこぼしても止まらないように、走っているあいだは定期的に取り直す。
  // 演出付きの Remotion レンダリングは WS の書き出しフレームに乗らない
  // （別のジョブとして走る）ので、そのあいだも同じ間隔で取り直す。
  useEffect(() => {
    if (!timelineId || (!running && !fxRunning)) return
    const timer = window.setInterval(() => {
      void api.listStudioTimelineExports(timelineId).then(setExports, () => undefined)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [timelineId, running, fxRunning])

  // ---------------------------------------------------------------- 編集操作
  const apply = useCallback(
    (change: (current: TimelineClip[]) => TimelineClip[]) => {
      setHistory((current) => pushHistory(current, change(current.present), sameClips))
    },
    [],
  )

  /** V1 の中だけを書き換える（並べ替え・トリム・分割…）。 */
  const applyVideo = useCallback(
    (change: (current: TimelineClip[]) => TimelineClip[]) => {
      if (!videoTrackId) return
      apply((current) => applyToTrack(current, videoTrackId, change))
    },
    [apply, videoTrackId],
  )

  const selected = clips.find((clip) => clip.id === selectedId) ?? null
  const selectedTrack =
    tracks.find((track) => track.id === selected?.track_id) ?? null
  const videoClips = useMemo(
    () => (videoTrackId ? clipsOfTrack(clips, videoTrackId) : []),
    [clips, videoTrackId],
  )
  const selectedIndex = selected
    ? clipsOfTrack(clips, selected.track_id).findIndex(
        (clip) => clip.id === selected.id,
      )
    : -1
  const total = totalDuration(videoClips)
  const brokenCount = clips.filter((clip) => clip.missing).length

  /** 選んでいるクリップのトラックの中だけを書き換える。 */
  const applySelectedTrack = useCallback(
    (change: (current: TimelineClip[]) => TimelineClip[]) => {
      if (!selected) return
      apply((current) => applyToTrack(current, selected.track_id, change))
    },
    [apply, selected],
  )

  /** 再生ヘッドが選択クリップの中にあり、割っても両側が短くなりすぎないか。 */
  const canSplit = useMemo(() => {
    if (!selected) return false
    const offset = playheadMs - selected.start_ms
    return offset >= MIN_CLIP_MS && selected.duration_ms - offset >= MIN_CLIP_MS
  }, [selected, playheadMs])

  const splitSelected = useCallback(() => {
    if (!selectedId) return
    applySelectedTrack((current) => splitClipAt(current, selectedId, playheadMs))
  }, [applySelectedTrack, selectedId, playheadMs])

  const deleteSelected = useCallback(() => {
    if (!selectedId || !selected) return
    const rippleTrack = selected.track_id === videoTrackId
    applySelectedTrack((current) =>
      rippleTrack ? removeClip(current, selectedId) : removeClipFree(current, selectedId),
    )
    setSelectedId(null)
  }, [applySelectedTrack, selectedId, selected, videoTrackId])

  // ショートカット。入力欄にフォーカスがあるときは奪わない（本文の編集など）。
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
  const guard = (run: () => Promise<void>) =>
    void (async () => {
      setBusy(true)
      setError(null)
      try {
        await run()
      } catch (cause) {
        pushError(cause)
      } finally {
        setBusy(false)
      }
    })()

  const createTimeline = () =>
    guard(async () => {
      const created = await api.createStudioTimeline(projectId, {
        episode_id: episodeId || null,
      })
      setTimelines(await api.listStudioTimelines(projectId))
      setTimelineId(created.id)
      adoptTimeline(created)
      setExports([])
      setSelectedId(null)
      setPlayheadMs(0)
      setSync(null)
    })

  const startExport = (body: TimelineExportRequest) =>
    guard(async () => {
      if (!timelineId) return
      // 書き出しはサーバーが持っている EDL を焼くので、手元の変更を先に流す。
      if (!sameClips(clips, savedRef.current)) await save(clips)
      await api.exportStudioTimeline(timelineId, body)
      setExports(await api.listStudioTimelineExports(timelineId))
    })

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

  // ------------------------------------------------------------ トラック操作
  const addAudioTrack = () =>
    guard(async () => {
      if (!timelineId) return
      await withFlush(() =>
        api.addStudioTimelineTrack(timelineId, { kind: 'audio' }),
      )
    })

  const toggleMute = (trackId: string, muted: boolean) =>
    guard(async () => {
      if (!timelineId) return
      await withFlush(() =>
        api.updateStudioTimelineTrack(timelineId, trackId, { muted }),
      )
    })

  const deleteTrack = (trackId: string) =>
    guard(async () => {
      if (!timelineId) return
      if (!window.confirm('このトラックとその中のクリップを消します。よいですか？'))
        return
      await withFlush(() => api.deleteStudioTimelineTrack(timelineId, trackId))
      setSelectedId(null)
    })

  const generateSubtitles = () =>
    guard(async () => {
      if (!timelineId) return
      const warning = subtitleTrack?.clips.length
        ? '字幕トラックの今のテロップは全部置き換わります。よいですか？'
        : 'V1 の各カットの台詞からテロップを作ります。よいですか？'
      if (!window.confirm(warning)) return
      await withFlush(() => api.generateStudioTimelineSubtitles(timelineId, {}))
      setSelectedId(null)
    })

  // ------------------------------------------------------------ FX トラック
  //
  // 演出を**作る**のは外部 API（AI）。ここでできるのは調整（帯のドラッグと
  // プロパティ）と削除だけで、どちらも `base_revision` を添えて 1 件ずつ送る。
  const fxSelected =
    fx?.events.find((item) => item.id === fxSelectedId) ?? null

  /** サーバーの応答を採り、次に添える `base_revision` を取り直す。 */
  const adoptFx = useCallback(
    async (fresh: TimelineFx) => {
      setFx(fresh)
      await refreshRevision()
    },
    [refreshRevision],
  )

  const patchFxEvent = useCallback(
    (
      eventId: string,
      patch: Record<string, unknown>,
      enabled?: boolean,
    ) =>
      void (async () => {
        if (!timelineId) return
        setFxBusy(true)
        setError(null)
        try {
          await adoptFx(
            await api.updateStudioTimelineFxEvent(timelineId, eventId, {
              ...(Object.keys(patch).length > 0 ? { event: patch } : {}),
              ...(enabled === undefined ? {} : { enabled }),
              base_revision: baseRevision.current,
            }),
          )
        } catch (cause) {
          pushError(cause)
          // ぶつかった / 弾かれたときは手元を捨ててサーバーの中身へ戻す。
          try {
            await adoptFx(await api.getStudioTimelineFx(timelineId))
          } catch {
            /* 読み直しにも失敗したら、次の操作で追いつく */
          }
        } finally {
          setFxBusy(false)
        }
      })(),
    [timelineId, adoptFx, pushError],
  )

  /** 帯のドラッグ（本体で `t`、右端で `until`）。離すまでは手元だけ動かす。 */
  const dragFxEvent = useCallback(
    (
      eventId: string,
      change: { startMs?: number; endMs?: number },
      done: boolean,
    ) => {
      const item = fx?.events.find((event) => event.id === eventId)
      if (!item) return
      const patch =
        change.startMs !== undefined
          ? fxMovedTo(item, change.startMs)
          : fxResizedTo(item, change.endMs ?? 0)
      setFx((current) =>
        current
          ? { ...current, events: fxApplyLocal(current.events, eventId, patch) }
          : current,
      )
      if (done) patchFxEvent(eventId, patch)
    },
    [fx, patchFxEvent],
  )

  const deleteFxEvent = () =>
    void (async () => {
      if (!timelineId || !fxSelectedId) return
      setFxBusy(true)
      setError(null)
      try {
        await adoptFx(
          await api.deleteStudioTimelineFxEvent(
            timelineId,
            fxSelectedId,
            baseRevision.current,
          ),
        )
        setFxSelectedId(null)
      } catch (cause) {
        pushError(cause)
      } finally {
        setFxBusy(false)
      }
    })()

  // ------------------------------------------------------------ 素材ビン
  const addMedia = (item: TimelineMediaItem) => {
    if (item.media_kind === 'audio') {
      const track = audioTracks[0]
      if (!track || !timelineId) return
      apply((current) =>
        applyToTrack(current, track.id, (lane) => {
          const made = clipFromMedia(item, track.id, 0)
          return [
            ...lane,
            { ...made, start_ms: firstFreeSlot(lane, made.duration_ms, playheadMs) },
          ].sort((a, b) => a.start_ms - b.start_ms)
        }),
      )
      return
    }
    if (!videoTrackId) return
    // 動画・静止画は V1 の末尾へ（置いてから並べ替え・トリムする）。
    applyVideo((lane) => ripple([...lane, clipFromMedia(item, videoTrackId, 0)]))
  }

  const addSubtitle = () => {
    if (!subtitleTrack) return
    apply((current) =>
      applyToTrack(current, subtitleTrack.id, (lane) => {
        const made = newSubtitleClip(subtitleTrack.id, playheadMs)
        return [
          ...lane,
          { ...made, start_ms: firstFreeSlot(lane, made.duration_ms, playheadMs) },
        ].sort((a, b) => a.start_ms - b.start_ms)
      }),
    )
  }

  // ------------------------------------------------------------ 脚本との差分
  const applySync = (request: TimelineSyncRequest) =>
    guard(async () => {
      if (!timelineId) return
      await withFlush(() => api.applyStudioTimelineSync(timelineId, request))
      setSync(await api.getStudioTimelineSyncPreview(timelineId))
      setSyncOpen(false)
      setSelectedId(null)
    })

  const syncCount = sync
    ? sync.added.length + sync.retaken.length + sync.removed.length
    : 0

  // ------------------------------------------------------ メディア欠落の修復
  const openMissing = () =>
    guard(async () => {
      if (!timelineId) return
      if (!sameClips(clips, savedRef.current)) await save(clips)
      setMissing(await api.getStudioTimelineMissing(timelineId))
    })

  const resolveMissing = (fix: TimelineMissingFix) =>
    guard(async () => {
      if (!timelineId) return
      await withFlush(() => api.resolveStudioTimelineMissing(timelineId, fix))
      setMissing(null)
      setSelectedId(null)
    })

  // ----------------------------------------------------------------- render
  const banner = error && (
    <Banner onClose={() => setError(null)}>{error}</Banner>
  )

  return (
    <div className="flex flex-col gap-3">
      {banner}

      {timeline && syncCount > 0 && (
        <Banner tone="info">
          <div className="flex flex-wrap items-center gap-2">
            <span>
              このタイムラインを作ったあとに脚本が {syncCount} 件動いています。
            </span>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => setSyncOpen(true)}
              disabled={busy}
            >
              <RefreshCw className="size-4" aria-hidden="true" />
              変更を確認して反映
            </Button>
          </div>
        </Banner>
      )}

      {timeline && brokenCount > 0 && (
        <Banner tone="warn">
          <div className="flex flex-wrap items-center gap-2">
            <span>
              メディアが見つからないクリップが {brokenCount} 件あります
              （このままでは書き出せません）。
            </span>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={openMissing}
              disabled={busy}
            >
              <AlertTriangle className="size-4" aria-hidden="true" />
              修復する
            </Button>
          </div>
        </Banner>
      )}

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

        {timeline && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={generateSubtitles}
            disabled={busy || videoClips.length === 0}
            title="V1 の各カットの台詞からテロップを作る（今のテロップは置き換わります）"
          >
            <Type className="size-4" aria-hidden="true" />
            テロップを生成
          </Button>
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
              tracks={tracks}
              playheadMs={playheadMs}
              onSeek={setPlayheadMs}
              fx={fxEnabled ? fx : null}
              fps={timeline.fps}
              width={timeline.width}
              height={timeline.height}
            />
            <TimelinePane
              tracks={tracks}
              clips={clips}
              videoTrackId={videoTrackId}
              selectedId={selectedId}
              playheadMs={playheadMs}
              zoom={zoom}
              onZoom={setZoom}
              onSelect={(id) => {
                setSelectedId(id)
                if (id) setFxSelectedId(null)
              }}
              onSeek={(ms) => setPlayheadMs(Math.min(ms, Math.max(total, 0)))}
              onMove={(id, to) => applyVideo((current) => moveClip(current, id, to))}
              onMoveTo={(id, startMs) => {
                const clip = clips.find((item) => item.id === id)
                if (!clip) return
                apply((current) =>
                  applyToTrack(current, clip.track_id, (lane) =>
                    moveClipTo(lane, id, startMs),
                  ),
                )
              }}
              onTrim={(id, edge, deltaMs) => {
                const clip = clips.find((item) => item.id === id)
                if (!clip) return
                apply((current) =>
                  applyToTrack(current, clip.track_id, (lane) =>
                    trimClip(lane, id, edge, deltaMs),
                  ),
                )
              }}
              onSetTransition={(index, kind, ms) =>
                applyVideo((current) => setTransition(current, index, kind, ms))
              }
              onAddAudioTrack={addAudioTrack}
              onToggleMute={toggleMute}
              onDeleteTrack={deleteTrack}
              onAddSubtitle={addSubtitle}
              fxEvents={fxEnabled && fx ? fx.events : undefined}
              fxSelectedId={fxSelectedId}
              onFxSelect={(id) => {
                setFxSelectedId(id)
                if (id) setSelectedId(null)
              }}
              onFxDrag={dragFxEvent}
            />
          </div>

          <div className="flex w-full shrink-0 flex-col gap-3 lg:w-80">
            {fxEnabled && fxSelectedId ? (
              <FxInspector
                item={fxSelected}
                busy={fxBusy}
                onPatch={(patch) => patchFxEvent(fxSelectedId, patch)}
                onEnabled={(enabled) =>
                  patchFxEvent(fxSelectedId, {}, enabled)
                }
                onDelete={deleteFxEvent}
              />
            ) : null}
            <ClipInspector
              clip={selected}
              track={selectedTrack}
              index={selectedIndex}
              canSplit={canSplit}
              onSplit={splitSelected}
              onDelete={deleteSelected}
              onSpeed={(speed) => {
                if (!selectedId) return
                applyVideo((current) => setSpeed(current, selectedId, speed))
              }}
              onPatch={(patch: Partial<TimelineClip>) => {
                if (!selectedId) return
                applySelectedTrack((current) =>
                  patchClip(current, selectedId, patch),
                )
              }}
              onSubtitle={(patch: {
                text?: string
                style?: Partial<SubtitleStyle>
              }) => {
                if (!selectedId) return
                applySelectedTrack((current) =>
                  setSubtitle(current, selectedId, patch),
                )
              }}
            />
            <MediaBin
              projectId={projectId}
              canAddAudio={audioTracks.length > 0}
              onAdd={addMedia}
            />
            <ExportPanel
              exports={exports}
              running={running}
              busy={busy}
              savingId={savingId}
              canExport={videoClips.length > 0 && brokenCount === 0}
              canExportFx={fxEnabled && (fx?.events.length ?? 0) > 0}
              onExport={startExport}
              onSaveToLibrary={saveToLibrary}
            />
          </div>
        </div>
      )}

      {syncOpen && sync && (
        <SyncDialog
          preview={sync}
          busy={busy}
          onApply={applySync}
          onClose={() => setSyncOpen(false)}
        />
      )}

      {missing && (
        <MissingDialog
          report={missing}
          busy={busy}
          onResolve={resolveMissing}
          onClose={() => setMissing(null)}
        />
      )}
    </div>
  )
}
