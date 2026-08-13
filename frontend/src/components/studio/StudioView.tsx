import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ClipboardList, LayoutGrid } from 'lucide-react'

import { ApiError, api, formatDetail } from '../../api'
import type {
  CanvasProgress,
  ComfyTarget,
  JobProgress,
  StudioAssetCategory,
  StudioAssetFileRole,
  StudioAssetUpdate,
  StudioProjectCreate,
  StudioProjectDetail,
  StudioProjectSummary,
  StudioProjectUpdate,
  StudioRevision,
  StudioShotUpdate,
  StudioVideoQuality,
} from '../../types'
import { DEFAULT_MEGAPIXELS } from '../../form'
import { Banner } from '../ui'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { ResizeHandle, useIsWide, useResizablePanel } from '../ui/resizable-panel'
import { Tabs, TabsList, TabsTrigger } from '../ui/tabs'
import { NativeSelect } from '../NativeSelect'
import TargetSelector from '../TargetSelector'
import CanvasView from '../canvas/CanvasView'
import OverviewView from './OverviewView'
import ProductionView from './ProductionView'
import ProjectPicker from './ProjectPicker'
import RevisionsModal from './RevisionsModal'
import ScriptView from './ScriptView'
import ShotRail from './ShotRail'
import WorldView from './WorldView'
import {
  VIDEO_QUALITIES,
  VIDEO_QUALITY_HINT,
  VIDEO_QUALITY_LABEL,
  assetKindFromFile,
  buildShotTree,
  moveId,
  moveShot,
  renderingJobIds,
} from './studio'

/** ジョブがまだ動いている状態（App.tsx と同じ定義）。 */
const ACTIVE_STATUSES = ['queued', 'prompting', 'running']

const SHOT_RAIL_WIDTH_KEY = 'studioShotRailWidth'
const SHOT_RAIL_WIDTH = { initial: 256, min: 200, max: 480 }

type StudioTab = 'overview' | 'script' | 'world' | 'production'

/**
 * 同じプロジェクトの見せ方（キャンバスはスタジオの別ビューで、DB は同じ）。
 */
type ProjectMode = 'studio' | 'canvas'

const MODES: { value: ProjectMode; label: string; icon: typeof ClipboardList }[] = [
  { value: 'studio', label: 'スタジオ表示', icon: ClipboardList },
  { value: 'canvas', label: 'キャンバス表示', icon: LayoutGrid },
]

const TABS: { value: StudioTab; label: string }[] = [
  { value: 'overview', label: '概要' },
  { value: 'script', label: '脚本' },
  { value: 'world', label: 'World Bible' },
  { value: 'production', label: '制作' },
]

/**
 * ドラマスタジオ画面。
 *
 * プロジェクト未選択ならプロジェクト一覧、選択後は 3 ペイン（左 = Shot レール /
 * 中央 = 4 タブ）。画面 1 枚ぶんの状態は `GET /api/studio/projects/{id}` が丸ごと
 * 返すので、操作のたびとジョブの進捗が動いたときにそれを取り直す。
 */
export default function StudioView({
  progress,
  canvasEvent = null,
  aspectRatios = [],
  showNsfw = true,
  comfyTarget = null,
  onComfyTarget,
}: {
  /** App が WS から集めているジョブ進捗（Take の生成中表示に使う）。 */
  progress: Record<string, JobProgress>
  /** キャンバスのエージェント実行の最新フレーム（WS）。 */
  canvasEvent?: CanvasProgress | null
  /** 生成フォームと同じアスペクト比の候補（無ければ Shot 側は自由入力）。 */
  aspectRatios?: string[]
  /**
   * ヘッダーの「NSFW表示」。オフのあいだは Take の絵をぼかし、NSFW プロジェクト
   * は一覧から存在ごと消す（ジョブ一覧と同じ扱い）。
   */
  showNsfw?: boolean
  /** ComfyUI の接続先（生成タブと同じグローバル設定）。 */
  comfyTarget?: ComfyTarget | null
  onComfyTarget?: (target: ComfyTarget) => void
}) {
  const [projects, setProjects] = useState<StudioProjectSummary[]>([])
  const [loadingProjects, setLoadingProjects] = useState(false)
  const [projectId, setProjectId] = useState<string | null>(null)
  const [detail, setDetail] = useState<StudioProjectDetail | null>(null)
  const [tab, setTab] = useState<StudioTab>('overview')
  const [mode, setMode] = useState<ProjectMode>('studio')
  const [shotId, setShotId] = useState<string | null>(null)
  const [assetId, setAssetId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [revisions, setRevisions] = useState<StudioRevision[] | null>(null)
  const [loadingRevisions, setLoadingRevisions] = useState(false)
  // ヘッダーのメガピクセル欄。1 文字打つたびに PATCH しないよう、確定
  // （フォーカスを外す / Enter）までは打った文字列をここに置いて映す。
  const [megapixelsDraft, setMegapixelsDraft] = useState('')
  // ショット一覧の列は lg 以上でだけドラッグで広げられる（狭幅は縦積み）。
  const isWide = useIsWide()
  const shotRail = useResizablePanel(SHOT_RAIL_WIDTH_KEY, SHOT_RAIL_WIDTH, 'x')

  const pushError = useCallback((cause: unknown) => {
    setError(
      cause instanceof ApiError
        ? formatDetail(cause.detail)
        : cause instanceof Error
          ? cause.message
          : String(cause),
    )
  }, [])

  const loadProjects = useCallback(async () => {
    setLoadingProjects(true)
    try {
      setProjects(await api.listStudioProjects())
    } catch (cause) {
      pushError(cause)
    } finally {
      setLoadingProjects(false)
    }
  }, [pushError])

  const reload = useCallback(async () => {
    if (!projectId) return
    try {
      setDetail(await api.getStudioProject(projectId))
    } catch (cause) {
      pushError(cause)
    }
  }, [projectId, pushError])

  useEffect(() => {
    void loadProjects()
  }, [loadProjects])

  // 保存後の読み直しとプロジェクトの開き直しに追随させる（未確定の入力は
  // 保存が終わった時点で捨てる）。
  const savedMegapixels = detail?.megapixels ?? null
  useEffect(() => {
    setMegapixelsDraft(savedMegapixels === null ? '' : String(savedMegapixels))
  }, [savedMegapixels])

  useEffect(() => {
    if (!projectId) {
      setDetail(null)
      return
    }
    void reload()
  }, [projectId, reload])

  // ------------------------------------------------------------ NSFW の表示
  // ヘッダーのトグルがオフのあいだは、NSFW プロジェクトを一覧から存在ごと消す
  // （ジョブ一覧の App.tsx `isVisible` と同じ流儀。ぼかしではなく非表示）。
  const visibleProjects = useMemo(
    () => (showNsfw ? projects : projects.filter((project) => !project.nsfw)),
    [projects, showNsfw],
  )

  /** 開いているプロジェクトの最新（下のトグル監視から読むだけ）。 */
  const detailRef = useRef<StudioProjectDetail | null>(null)
  useEffect(() => {
    detailRef.current = detail
  }, [detail])

  // 表示をオフに戻したら、開いている NSFW プロジェクトを閉じて一覧へ戻す
  // （App.tsx が activeJob / detailJob の選択を解除するのと同じ）。
  //
  // 効くのは showNsfw が変わった瞬間だけにしてある。オフのまま概要タブで NSFW を
  // ON にした場合は画面を閉じない = 編集中のフォームを飛ばさず、一覧へ戻った
  // 時点（次回以降）で見えなくなる、という方針。
  useEffect(() => {
    if (showNsfw) return
    if (!detailRef.current?.nsfw) return
    setProjectId(null)
    setDetail(null)
    setShotId(null)
    setAssetId(null)
  }, [showNsfw])

  /**
   * 変更操作の共通の型: 走らせて、成功したら画面を取り直す。エラーはバナーに
   * 出すだけで、途中の状態は残さない（次の取り直しでサーバー側に揃う）。
   */
  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true)
      setError(null)
      try {
        await action()
        await reload()
      } catch (cause) {
        pushError(cause)
      } finally {
        setBusy(false)
      }
    },
    [pushError, reload],
  )

  // ------------------------------------------------------------ 進捗の追従
  // 生成中の Take のジョブが終わった（WS のフレームが終端状態になった）ら、
  // 成果物を含む Take を取りにプロジェクトを読み直す。同じフレームで何度も
  // 走らないよう、処理済みの job_id を覚えておく。
  const settled = useRef(new Set<string>())
  useEffect(() => {
    if (!detail) return
    const done = renderingJobIds(detail.takes).filter((jobId) => {
      const frame = progress[jobId]
      return frame != null && !ACTIVE_STATUSES.includes(frame.status)
    })
    const fresh = done.filter((jobId) => !settled.current.has(jobId))
    if (fresh.length === 0) return
    for (const jobId of fresh) settled.current.add(jobId)
    void reload()
  }, [progress, detail, reload])

  // WS を取りこぼしても止まらないように、生成中があいだは定期的に取り直す。
  useEffect(() => {
    if (!detail || renderingJobIds(detail.takes).length === 0) return
    const timer = window.setInterval(() => void reload(), 5000)
    return () => window.clearInterval(timer)
  }, [detail, reload])

  // 左レールに出す 話 -> 場 -> Shot のツリー（Shot の通し番号もここで決まる）。
  const tree = useMemo(
    () =>
      buildShotTree(detail ?? { episodes: [], scenes: [], shots: [] }),
    [detail],
  )

  // ---------------------------------------------------------------- actions

  const createProject = (payload: StudioProjectCreate) =>
    void (async () => {
      setBusy(true)
      setError(null)
      try {
        const created = await api.createStudioProject(payload)
        await loadProjects()
        setProjectId(created.id)
        setTab('overview')
      } catch (cause) {
        pushError(cause)
      } finally {
        setBusy(false)
      }
    })()

  /** デモ作品を 1 本まるごと作って、そのまま開く（同じコードがあれば 409）。 */
  const createDemo = (code: string) =>
    void (async () => {
      setBusy(true)
      setError(null)
      try {
        const created = await api.createStudioDemoProject(code)
        await loadProjects()
        setProjectId(created.id)
        setShotId(null)
        setAssetId(null)
        setTab('overview')
      } catch (cause) {
        pushError(cause)
      } finally {
        setBusy(false)
      }
    })()

  const saveProject = (patch: StudioProjectUpdate) =>
    void run(async () => {
      if (!projectId) return
      await api.updateStudioProject(projectId, patch)
      await loadProjects()
    })

  const deleteProject = () => {
    if (!projectId) return
    if (!window.confirm('このプロジェクトを削除しますか？')) return
    void (async () => {
      setBusy(true)
      setError(null)
      try {
        await api.deleteStudioProject(projectId)
        setProjectId(null)
        setDetail(null)
        setShotId(null)
        await loadProjects()
      } catch (cause) {
        pushError(cause)
      } finally {
        setBusy(false)
      }
    })()
  }

  const addShot = () =>
    void run(async () => {
      if (!projectId) return
      const created = await api.createStudioShot(projectId, {
        title: `カット ${(detail?.shots.length ?? 0) + 1}`,
      })
      setShotId(created.id)
      setTab('script')
    })

  /** その場に属するカットを足す（レールの場の「＋」ボタンから）。 */
  const addShotToScene = (sceneId: string) =>
    void run(async () => {
      if (!projectId) return
      const created = await api.createStudioShot(projectId, {
        title: `カット ${(detail?.shots.length ?? 0) + 1}`,
        scene_id: sceneId,
      })
      setShotId(created.id)
      setTab('script')
    })

  const moveShotBy = (id: string, delta: number) => {
    if (!projectId || !detail) return
    const order = moveShot(detail.shots, id, delta)
    if (!order) return
    void run(() => api.reorderStudioShots(projectId, order))
  }

  const saveShot = (id: string, patch: StudioShotUpdate) =>
    void run(() => api.updateStudioShot(id, patch))

  const deleteShot = (id: string) => {
    if (!window.confirm('このカットを削除しますか？')) return
    void run(async () => {
      await api.deleteStudioShot(id)
      setShotId((current) => (current === id ? null : current))
    })
  }

  // ------------------------------------------------------- 話（Episode）と場
  const addEpisode = () =>
    void run(async () => {
      if (!projectId) return
      await api.createStudioEpisode(projectId, {
        title: `第 ${(detail?.episodes.length ?? 0) + 1} 話`,
      })
    })

  const renameEpisode = (id: string, title: string) =>
    void run(() => api.updateStudioEpisode(id, { title }))

  /** 話のあらすじ（脚本の書き出しに効く設定なので、レールからも直せるように）。 */
  const editEpisodeSynopsis = (id: string, synopsis: string) =>
    void run(() => api.updateStudioEpisode(id, { synopsis }))

  const moveEpisodeBy = (id: string, delta: number) => {
    if (!projectId || !detail) return
    const order = moveId(
      detail.episodes.map((episode) => episode.id),
      id,
      delta,
    )
    if (!order) return
    void run(() => api.reorderStudioEpisodes(projectId, order))
  }

  const deleteEpisode = (id: string) => {
    if (!window.confirm('この話を削除しますか？（中の場も消え、カットは未分類に戻ります）'))
      return
    void run(() => api.deleteStudioEpisode(id))
  }

  const addScene = (episodeId: string) =>
    void run(async () => {
      const count =
        detail?.scenes.filter((scene) => scene.episode_id === episodeId).length ?? 0
      await api.createStudioScene(episodeId, { title: `場 ${count + 1}` })
    })

  const renameScene = (id: string, title: string) =>
    void run(() => api.updateStudioScene(id, { title }))

  /** 場のあらすじと時間帯（キャンバスの場カードと同じ項目）。 */
  const editSceneSynopsis = (id: string, synopsis: string) =>
    void run(() => api.updateStudioScene(id, { synopsis }))

  const editSceneTimeOfDay = (id: string, timeOfDay: string) =>
    void run(() => api.updateStudioScene(id, { time_of_day: timeOfDay }))

  const moveSceneBy = (episodeId: string, id: string, delta: number) => {
    if (!detail) return
    const order = moveId(
      detail.scenes
        .filter((scene) => scene.episode_id === episodeId)
        .map((scene) => scene.id),
      id,
      delta,
    )
    if (!order) return
    void run(() => api.reorderStudioScenes(episodeId, order))
  }

  const deleteScene = (id: string) => {
    if (!window.confirm('この場を削除しますか？（中のカットは未分類に戻ります）')) return
    void run(() => api.deleteStudioScene(id))
  }

  // --------------------------------------------------------- リビジョン履歴
  const openRevisions = () => {
    if (!projectId) return
    setRevisions([])
    setLoadingRevisions(true)
    void (async () => {
      try {
        setRevisions(await api.listStudioRevisions(projectId))
      } catch (cause) {
        setRevisions(null)
        pushError(cause)
      } finally {
        setLoadingRevisions(false)
      }
    })()
  }

  const restoreRevision = (seq: number) => {
    if (!projectId) return
    if (!window.confirm(`#${seq} の時点に戻しますか？`)) return
    setRevisions(null)
    void run(async () => {
      await api.restoreStudioRevision(projectId, seq)
      await loadProjects()
    })
  }

  const addAsset = (
    file: File | null,
    name: string,
    category: StudioAssetCategory,
    caption: string,
  ) =>
    void run(async () => {
      if (!projectId) return
      const created = file
        ? await api.uploadStudioAsset(projectId, file, {
            name,
            kind: assetKindFromFile(file.name),
            category,
            caption,
          })
        : await api.createStudioAsset(projectId, { name, category, caption })
      setAssetId(created.id)
    })

  const saveAsset = (id: string, patch: StudioAssetUpdate) =>
    void run(() => api.updateStudioAsset(id, patch))

  /** 素材のメインのファイルを付ける / 差し替える。 */
  const uploadAssetFile = (id: string, file: File) =>
    void run(() => api.uploadStudioAssetFile(id, file))

  /** 声サンプル・動画リファレンス・追加画像を素材に足す。 */
  const addAssetReference = (
    id: string,
    file: File,
    role: StudioAssetFileRole,
    caption: string,
  ) => void run(() => api.addStudioAssetFile(id, file, { role, caption }))

  const removeAssetReference = (fileId: string) =>
    void run(() => api.deleteStudioAssetFile(fileId))

  const deleteAsset = (id: string) => {
    if (!window.confirm('この素材を削除しますか？')) return
    void run(async () => {
      await api.deleteStudioAsset(id)
      setAssetId((current) => (current === id ? null : current))
    })
  }

  const render = (id: string) => void run(() => api.renderStudioShot(id))
  const selectTake = (id: string) => void run(() => api.selectStudioTake(id))
  const rejectTake = (id: string) => void run(() => api.rejectStudioTake(id))
  const deleteTake = (id: string) => {
    if (!window.confirm('この Take を削除しますか？')) return
    void run(() => api.deleteStudioTake(id))
  }

  // ---------------------------------------------------------------- render

  const banner = error && (
    <div className="px-4 pt-2">
      <Banner onClose={() => setError(null)}>{error}</Banner>
    </div>
  )

  /** 生成タブと同じ接続先セレクタ（グローバル設定。どこで変えても全体に効く）。 */
  const targetSelector = onComfyTarget ? (
    <TargetSelector
      target={comfyTarget}
      onChange={onComfyTarget}
      id="studio-comfy-target"
      className="w-52 shrink-0"
    />
  ) : null

  if (!projectId || !detail) {
    return (
      <main className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        {banner}
        {targetSelector && <div className="px-4 pt-3">{targetSelector}</div>}
        <ProjectPicker
          projects={visibleProjects}
          loading={loadingProjects}
          busy={busy}
          onOpen={(id) => {
            setProjectId(id)
            setShotId(null)
            setAssetId(null)
            setTab('overview')
          }}
          onCreate={createProject}
          onCreateDemo={createDemo}
          onReload={() => void loadProjects()}
        />
      </main>
    )
  }

  const selectedShot = detail.shots.find((shot) => shot.id === shotId) ?? null

  /**
   * スタジオ表示 ⇔ キャンバス表示（同じプロジェクトの別の見せ方）。
   *
   * 中央のタブ（概要 / 脚本 / …）と同じ Tabs で並べると 2 組が区別できないので、
   * こちらはアイコン付きのセグメントトグルにして右端へ置く。
   */
  const modeToggle = (
    <div
      role="group"
      aria-label="表示モード"
      className="flex shrink-0 items-center gap-0.5 rounded-md border border-border bg-card p-0.5"
    >
      {MODES.map((item) => {
        const current = item.value === mode
        return (
          <Button
            key={item.value}
            variant={current ? 'secondary' : 'ghost'}
            size="sm"
            aria-pressed={current}
            title={item.label}
            onClick={() => setMode(item.value)}
          >
            <item.icon />
            {item.label}
          </Button>
        )
      })}
    </div>
  )

  /**
   * 動画生成の品質（プロジェクト設定）。接続先セレクタの隣に**常時**出すのは、
   * これがテイクを 1 本焼くたびの待ち時間をそのまま決める設定だから。
   *
   * 変更はその場でプロジェクトへ保存する（`saveProject` が PATCH と読み直しを
   * やるので、楽観更新は持たず、保存が終わるまで `busy` で塞ぐ）。ラテント
   * 連続性が ON のあいだは turbo / opt に保存付きのバリアントが無く、投入時に
   * 素へフォールバックするので、選ばせたまま注記だけを添える。
   */
  const qualityIgnored = detail.latent_continuity && detail.quality !== 'normal'
  const qualitySelector = (
    <div className="flex shrink-0 items-center gap-2">
      <Label className="shrink-0" htmlFor="studio-quality">
        品質
      </Label>
      <div className="w-28">
        <NativeSelect
          id="studio-quality"
          value={detail.quality}
          disabled={busy}
          title={VIDEO_QUALITIES.map((value) => VIDEO_QUALITY_HINT[value]).join('\n')}
          onChange={(event) =>
            saveProject({ quality: event.target.value as StudioVideoQuality })
          }
        >
          {VIDEO_QUALITIES.map((value) => (
            <option key={value} value={value}>
              {VIDEO_QUALITY_LABEL[value]}
            </option>
          ))}
        </NativeSelect>
      </div>
      {qualityIgnored && (
        <span
          className="text-[11px] leading-tight text-amber-400"
          title="turbo / opt には AV ラテントを保存する版が無いので、連続性が ON のあいだは通常品質で投入します"
        >
          連続性が有効なため
          <br />
          品質設定は無効
        </span>
      )}
    </div>
  )

  /**
   * 画質（アスペクト比とメガピクセル）のプロジェクト既定。項目も表記も生成
   * フォームの「解像度」セクションに揃えてある。
   *
   * 効き方は **Shot 個別 > ここ > 既定** なので、ここは「Shot が何も言わな
   * かったときの既定」でしかない。メガピクセルを上げるほど生成が遅くなり、
   * ローカルの低 VRAM GPU ではメモリ不足になることがある。
   *
   * どちらも空欄（＝未指定）に戻せる。PATCH では null を明示して送る。
   */
  const commitMegapixels = () => {
    const text = megapixelsDraft.trim()
    const next = text === '' ? null : Number(text)
    // 数として読めない / 0 以下は捨てて、保存済みの値に戻す。
    if (next !== null && (!Number.isFinite(next) || next <= 0)) {
      setMegapixelsDraft(savedMegapixels === null ? '' : String(savedMegapixels))
      return
    }
    if (next === savedMegapixels) return
    saveProject({ megapixels: next })
  }

  const qualitySettings = (
    <div className="flex shrink-0 items-center gap-2">
      <Label className="shrink-0" htmlFor="studio-aspect-ratio">
        画質
      </Label>
      <div className="w-40">
        <NativeSelect
          id="studio-aspect-ratio"
          value={detail.aspect_ratio ?? ''}
          disabled={busy}
          title="この作品のアスペクト比の既定（Shot 個別の指定があればそちらが優先）"
          onChange={(event) =>
            saveProject({ aspect_ratio: event.target.value || null })
          }
        >
          <option value="">既定のまま</option>
          {detail.aspect_ratio && !aspectRatios.includes(detail.aspect_ratio) && (
            <option value={detail.aspect_ratio}>{detail.aspect_ratio}</option>
          )}
          {aspectRatios.map((ratio) => (
            <option key={ratio} value={ratio}>
              {ratio}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="w-24">
        <Input
          id="studio-megapixels"
          aria-label="メガピクセル"
          className="tnum"
          type="number"
          step="0.05"
          min="0.1"
          value={megapixelsDraft}
          disabled={busy}
          placeholder={`${DEFAULT_MEGAPIXELS}`}
          title="この作品のメガピクセルの既定（空欄でワークフローの既定。Shot 個別の指定があればそちらが優先）"
          onChange={(event) => setMegapixelsDraft(event.target.value)}
          onBlur={commitMegapixels}
          onKeyDown={(event) => {
            if (event.key === 'Enter') event.currentTarget.blur()
          }}
        />
      </div>
      <span className="shrink-0 text-[11px] text-muted-foreground">MP</span>
    </div>
  )

  const backToProjects = (
    <Button variant="outline" size="sm" onClick={() => setProjectId(null)}>
      <ArrowLeft />
      プロジェクト一覧
    </Button>
  )

  if (mode === 'canvas') {
    return (
      <main className="flex min-h-0 flex-1 flex-col">
        {banner}
        <div className="flex min-h-0 flex-1 flex-col gap-2 p-3">
          <div className="flex flex-wrap items-center gap-2">
            {backToProjects}
            <h2 className="min-w-0 truncate text-base font-semibold text-foreground">
              {detail.name}
            </h2>
            <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
              {targetSelector}
              {qualitySelector}
              {qualitySettings}
              {modeToggle}
            </div>
          </div>
          <CanvasView
            detail={detail}
            event={canvasEvent}
            progress={progress}
            onReloadStudio={reload}
          />
        </div>
      </main>
    )
  }

  return (
    <main className="flex min-h-0 flex-1 flex-col">
      {banner}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 lg:flex-row lg:overflow-hidden">
        <aside
          className="w-full shrink-0 rounded-lg border border-border bg-card shadow-elevation-1 lg:w-64 lg:overflow-hidden"
          style={isWide ? { width: shotRail.size } : undefined}
        >
          <ShotRail
            tree={tree}
            total={detail.shots.length}
            takes={detail.takes}
            selectedId={shotId}
            busy={busy}
            onSelect={setShotId}
            onMove={moveShotBy}
            onAdd={addShot}
            onAddEpisode={addEpisode}
            onRenameEpisode={renameEpisode}
            onEditEpisodeSynopsis={editEpisodeSynopsis}
            onMoveEpisode={moveEpisodeBy}
            onDeleteEpisode={deleteEpisode}
            onAddScene={addScene}
            onAddShotToScene={addShotToScene}
            onRenameScene={renameScene}
            onEditSceneSynopsis={editSceneSynopsis}
            onEditSceneTimeOfDay={editSceneTimeOfDay}
            onMoveScene={moveSceneBy}
            onDeleteScene={deleteScene}
          />
        </aside>

        <ResizeHandle panel={shotRail} label="ショット一覧の幅" className="-mx-2" />

        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
          {/* 上段 = プロジェクトの identity と接続先・表示モード、
              下段 = このプロジェクトの中の行き先（タブ）。混ぜると
              「タブが 2 組」に見えるので段で分ける。 */}
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              {backToProjects}
              <h2 className="min-w-0 truncate text-base font-semibold text-foreground">
                {detail.name}
              </h2>
              <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                {targetSelector}
                {qualitySelector}
                {qualitySettings}
                {modeToggle}
              </div>
            </div>
            <Tabs value={tab} onValueChange={(value) => setTab(value as StudioTab)}>
              <TabsList>
                {TABS.map((item) => (
                  <TabsTrigger key={item.value} value={item.value}>
                    {item.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          </div>

          <div className="min-h-0 flex-1 lg:overflow-y-auto lg:pr-1">
            {tab === 'overview' && (
              <OverviewView
                detail={detail}
                busy={busy}
                onSave={saveProject}
                onDelete={deleteProject}
                onOpenRevisions={openRevisions}
                comfyTarget={comfyTarget}
              />
            )}
            {tab === 'script' && (
              <ScriptView
                shots={detail.shots}
                episodes={detail.episodes}
                scenes={detail.scenes}
                aspectRatios={aspectRatios}
                selectedShot={selectedShot}
                busy={busy}
                onSelectShot={setShotId}
                onSave={saveShot}
                onDelete={deleteShot}
              />
            )}
            {tab === 'world' && (
              <WorldView
                assets={detail.assets}
                selectedId={assetId}
                busy={busy}
                onSelect={setAssetId}
                onAdd={addAsset}
                onSave={saveAsset}
                onDelete={deleteAsset}
                onUploadFile={uploadAssetFile}
                onAddReference={addAssetReference}
                onRemoveReference={removeAssetReference}
              />
            )}
            {tab === 'production' && (
              <ProductionView
                shots={detail.shots}
                assets={detail.assets}
                allTakes={detail.takes}
                selectedShot={selectedShot}
                progress={progress}
                busy={busy}
                latentContinuity={detail.latent_continuity}
                showNsfw={showNsfw}
                onSelectShot={setShotId}
                onRender={render}
                onSelectTake={selectTake}
                onRejectTake={rejectTake}
                onDeleteTake={deleteTake}
              />
            )}
          </div>
        </div>
      </div>

      {revisions !== null && (
        <RevisionsModal
          revisions={revisions}
          loading={loadingRevisions}
          busy={busy}
          onRestore={restoreRevision}
          onClose={() => setRevisions(null)}
        />
      )}
    </main>
  )
}
