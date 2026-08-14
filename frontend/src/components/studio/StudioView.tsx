import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

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
  StudioRenderRequest,
  StudioRevision,
  StudioShotUpdate,
} from '../../types'
import { Banner } from '../ui'
import { ResizeHandle, useIsWide, useResizablePanel } from '../ui/resizable-panel'
import { Tabs, TabsList, TabsTrigger } from '../ui/tabs'
import TargetSelector from '../TargetSelector'
import CanvasView from '../canvas/CanvasView'
import OverviewView from './OverviewView'
import ProductionView from './ProductionView'
import ProjectPicker from './ProjectPicker'
import RevisionsModal from './RevisionsModal'
import ScriptView from './ScriptView'
import ShotRail from './ShotRail'
import StudioProjectBar, { type StudioProjectMode } from './StudioProjectBar'
import WorldView from './WorldView'
import {
  MAX_STEPS,
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
  const [mode, setMode] = useState<StudioProjectMode>('studio')
  const [shotId, setShotId] = useState<string | null>(null)
  const [assetId, setAssetId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [revisions, setRevisions] = useState<StudioRevision[] | null>(null)
  const [loadingRevisions, setLoadingRevisions] = useState(false)
  // ヘッダーのメガピクセル欄。1 文字打つたびに PATCH しないよう、確定
  // （フォーカスを外す / Enter）までは打った文字列をここに置いて映す。
  const [megapixelsDraft, setMegapixelsDraft] = useState('')
  // ヘッダーのステップ数欄。空欄 = 0 = おまかせ（テンプレートの既定のまま）。
  const [stepsDraft, setStepsDraft] = useState('')
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

  // ステップ数も同じ扱い（0 = おまかせ、を空欄で見せる）。
  const savedSteps = detail?.steps ?? 0
  useEffect(() => {
    setStepsDraft(savedSteps > 0 ? String(savedSteps) : '')
  }, [savedSteps])

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

  /** テイクを 1 本焼く（`body` は生成ダイアログで決めたその 1 回ぶんの設定）。 */
  const render = (id: string, body: StudioRenderRequest) =>
    void run(() => api.renderStudioShot(id, body))
  const selectTake = (id: string) => void run(() => api.selectStudioTake(id))
  const rejectTake = (id: string) => void run(() => api.rejectStudioTake(id))
  const cancelTake = (id: string) => void run(() => api.cancelStudioTake(id))
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
   * 画質（アスペクト比とメガピクセル）のプロジェクト既定。
   *
   * 効き方は **Shot 個別 > ここ > 既定**。どちらも空欄（＝未指定）に戻せる。
   * PATCH では null を明示して送る。
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

  /**
   * ステップ数（サンプリング回数）のプロジェクト既定。
   *
   * **0（空欄）= おまかせ**で、そのときはワークフローのテンプレートの既定値
   * （品質 turbo なら 4、normal / opt なら 20）のまま焼く。
   */
  const commitSteps = () => {
    const text = stepsDraft.trim()
    const next = text === '' ? 0 : Number(text)
    // 整数でない / 範囲外は捨てて、保存済みの値に戻す。
    if (!Number.isInteger(next) || next < 0 || next > MAX_STEPS) {
      setStepsDraft(savedSteps > 0 ? String(savedSteps) : '')
      return
    }
    if (next === savedSteps) return
    saveProject({ steps: next })
  }

  const projectBar = (
    <StudioProjectBar
      name={detail.name}
      isWide={isWide}
      mode={mode}
      onModeChange={setMode}
      onBack={() => setProjectId(null)}
      comfyTarget={comfyTarget}
      onComfyTarget={onComfyTarget}
      quality={detail.quality}
      onQualityChange={(value) => saveProject({ quality: value })}
      aspectRatio={detail.aspect_ratio}
      aspectRatios={aspectRatios}
      onAspectRatioChange={(value) => saveProject({ aspect_ratio: value })}
      megapixels={detail.megapixels}
      megapixelsDraft={megapixelsDraft}
      onMegapixelsDraftChange={setMegapixelsDraft}
      onCommitMegapixels={commitMegapixels}
      steps={detail.steps}
      stepsDraft={stepsDraft}
      onStepsDraftChange={setStepsDraft}
      onCommitSteps={commitSteps}
      busy={busy}
    />
  )

  if (mode === 'canvas') {
    return (
      <main className="flex min-h-0 flex-1 flex-col">
        {banner}
        <div className="flex min-h-0 flex-1 flex-col gap-2 p-3">
          {projectBar}
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
            {projectBar}
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
                projectDefaults={{
                  megapixels: detail.megapixels,
                  aspect_ratio: detail.aspect_ratio,
                  steps: detail.steps,
                }}
                aspectRatios={aspectRatios}
                latentContinuity={detail.latent_continuity}
                showNsfw={showNsfw}
                onSelectShot={setShotId}
                onRender={render}
                onSelectTake={selectTake}
                onRejectTake={rejectTake}
                onCancelTake={cancelTake}
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
