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
  TimelineExportProgress,
} from '../../types'
import { Banner } from '../ui'
import { ResizeHandle, useIsWide, useResizablePanel } from '../ui/resizable-panel'
import { Tabs, TabsList, TabsTrigger } from '../ui/tabs'
import TargetSelector from '../TargetSelector'
import CanvasView from '../canvas/CanvasView'
import EditView from './EditView'
import EpisodeFilter, { ALL_EPISODES } from './EpisodeFilter'
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
  firstShotId,
  moveId,
  moveShot,
  renderingJobIds,
} from './studio'

/** ジョブがまだ動いている状態（App.tsx と同じ定義）。 */
const ACTIVE_STATUSES = ['queued', 'prompting', 'running']

const SHOT_RAIL_WIDTH_KEY = 'studioShotRailWidth'
const SHOT_RAIL_WIDTH = { initial: 256, min: 200, max: 480 }

type StudioTab = 'overview' | 'script' | 'world' | 'production' | 'edit'

const TABS: { value: StudioTab; label: string }[] = [
  { value: 'overview', label: '概要' },
  { value: 'script', label: '脚本' },
  { value: 'world', label: 'World Bible' },
  { value: 'production', label: '制作' },
  // 制作（1 カットを焼く）の次が編集（焼いたものを並べて 1 本にする）。
  { value: 'edit', label: '編集' },
]

/** 話の絞り込みの置き場（作品ごと。次に開いたときも同じ話から始める）。 */
const EPISODE_FILTER_KEY = 'studio-episode-filter'

function rememberedEpisode(projectId: string): string {
  try {
    return window.localStorage.getItem(`${EPISODE_FILTER_KEY}:${projectId}`) ?? ALL_EPISODES
  } catch {
    return ALL_EPISODES /* localStorage が使えない環境では毎回「すべて」から */
  }
}

function rememberEpisode(projectId: string, episodeId: string): void {
  try {
    const key = `${EPISODE_FILTER_KEY}:${projectId}`
    if (episodeId === ALL_EPISODES) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, episodeId)
  } catch {
    /* 覚えられなくても表示には困らない */
  }
}

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
  timelineExportEvent = null,
  aspectRatios = [],
  showNsfw = true,
  comfyTarget = null,
  onComfyTarget,
}: {
  /** App が WS から集めているジョブ進捗（Take の生成中表示に使う）。 */
  progress: Record<string, JobProgress>
  /** キャンバスのエージェント実行の最新フレーム（WS）。 */
  canvasEvent?: CanvasProgress | null
  /** 編集タブの書き出し進捗の最新フレーム（WS）。 */
  timelineExportEvent?: TimelineExportProgress | null
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
  // 脚本・制作タブの話の絞り込み（`ALL_EPISODES` = 作品まるごと）。サーバー側で
  // 絞るので、値が変わったら詳細を取り直す。
  const [episodeFilter, setEpisodeFilter] = useState<string>(ALL_EPISODES)
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
  // 素材の静止画用のメガピクセル欄・ステップ数欄。動画側と同じ扱いだが、値は
  // 完全に別（静止画に動画用の値は流用しない）。
  const [imageMegapixelsDraft, setImageMegapixelsDraft] = useState('')
  const [imageStepsDraft, setImageStepsDraft] = useState('')
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

  /**
   * 発行した取り直しの世代。
   *
   * 詳細の取得は作品と話ごとに別のリクエストになるので、話タブを続けて押したり
   * 作品を続けて開いたりすると複数が同時に飛ぶ。到着した順に `setDetail` して
   * しまうと、遅れて届いた**古い**リクエストの結果が最後に残る（選んでいる話と
   * 中身が食い違う）ので、投げた時点の世代を控えておいて、返ってきたときに
   * 世代が進んでいたら捨てる。
   */
  const requestSeq = useRef(0)

  const reload = useCallback(async () => {
    if (!projectId) return
    const seq = ++requestSeq.current
    try {
      const fresh = await api.getStudioProject(
        projectId,
        episodeFilter === ALL_EPISODES ? null : episodeFilter,
      )
      if (seq !== requestSeq.current) return
      setDetail(fresh)
    } catch (cause) {
      // 追い越された取り直しの失敗は握りつぶす（いま見ている画面の話ではない）。
      if (seq !== requestSeq.current) return
      // 覚えていた話が消えていた（404）ときは、エラーを見せずに「すべて」へ
      // 落とす（この setState でもう一度ここへ来て、作品まるごとを取り直す）。
      if (cause instanceof ApiError && cause.status === 404 && episodeFilter !== ALL_EPISODES) {
        setEpisodeFilter(ALL_EPISODES)
        return
      }
      pushError(cause)
    }
  }, [projectId, episodeFilter, pushError])

  /** 作品を開く（前に見ていた話から始める）。null で一覧へ戻る。 */
  const openProject = useCallback((id: string | null) => {
    setProjectId(id)
    setEpisodeFilter(id ? rememberedEpisode(id) : ALL_EPISODES)
  }, [])

  const changeEpisodeFilter = (value: string) => {
    if (projectId) rememberEpisode(projectId, value)
    setEpisodeFilter(value)
  }

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

  // 素材画像の 2 欄も同じ（`null` / `0` = 既定を空欄で見せる）。
  const savedImageMegapixels = detail?.image_megapixels ?? null
  useEffect(() => {
    setImageMegapixelsDraft(
      savedImageMegapixels === null ? '' : String(savedImageMegapixels),
    )
  }, [savedImageMegapixels])

  const savedImageSteps = detail?.image_steps ?? 0
  useEffect(() => {
    setImageStepsDraft(savedImageSteps > 0 ? String(savedImageSteps) : '')
  }, [savedImageSteps])

  useEffect(() => {
    if (!projectId) {
      // 一覧へ戻るのも「取り直しの世代を進める」うちに入れる。飛びっぱなしの
      // リクエストが後から届いて、閉じたはずの作品が開き直るのを防ぐ。
      requestSeq.current += 1
      setDetail(null)
      return
    }
    void reload()
  }, [projectId, reload])

  // 別のプロジェクトを開いたら選択は持ち越さない（新しい detail が届くまでの
  // あいだ、前のプロジェクトのカットを選んだままに見えてしまうのを防ぐ）。
  useEffect(() => {
    setShotId(null)
    setAssetId(null)
  }, [projectId])

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
    openProject(null)
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

  // 左レールに出す 話 -> 場 -> Shot のツリー（場の中のカット番号もここで決まる）。
  //
  // 話を選んでいるあいだは場・カット・テイクがサーバー側で絞られている一方、話は
  // つねに全件返る（タブバーを出すため）ので、ツリーに載せる話はここで絞る。
  // 未分類のカットは絞り込みの返り値に入らないので、自然と出なくなる。
  const tree = useMemo(() => {
    if (!detail) return buildShotTree({ episodes: [], scenes: [], shots: [] })
    return buildShotTree({
      ...detail,
      episodes:
        episodeFilter === ALL_EPISODES
          ? detail.episodes
          : detail.episodes.filter((episode) => episode.id === episodeFilter),
    })
  }, [detail, episodeFilter])

  // 選択が空 / 消えたカットを指しているなら、1 話目の最初のカットへ寄せる。
  // 制作タブは選択が無いと何も出せず、狭幅では左レールが畳まれていて行き止まり
  // になるため、詳細を読み直すたびに「必ずどれか選ばれている」状態へ戻す。
  //
  // 依存は detail（と、そこから作った tree）だけにしてある。shotId を見てしまうと
  // 「カットを足して選ぶ -> 読み直す」の途中（新しいカットはまだ detail に無い）で
  // 選択を奪ってしまうため。
  useEffect(() => {
    if (!detail) return
    setShotId((current) =>
      current && detail.shots.some((shot) => shot.id === current)
        ? current
        : firstShotId(tree),
    )
  }, [detail, tree])

  // ---------------------------------------------------------------- actions

  const createProject = (payload: StudioProjectCreate) =>
    void (async () => {
      setBusy(true)
      setError(null)
      try {
        const created = await api.createStudioProject(payload)
        await loadProjects()
        openProject(created.id)
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
        openProject(created.id)
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
        openProject(null)
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
            openProject(id)
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

  /**
   * 素材の静止画（`mode: "image_only"`）用の画質。動画側とまったく同じ確定の
   * 仕方で、書き込む先だけが `image_*` になる。
   */
  const commitImageMegapixels = () => {
    const text = imageMegapixelsDraft.trim()
    const next = text === '' ? null : Number(text)
    if (next !== null && (!Number.isFinite(next) || next <= 0)) {
      setImageMegapixelsDraft(
        savedImageMegapixels === null ? '' : String(savedImageMegapixels),
      )
      return
    }
    if (next === savedImageMegapixels) return
    saveProject({ image_megapixels: next })
  }

  const commitImageSteps = () => {
    const text = imageStepsDraft.trim()
    const next = text === '' ? 0 : Number(text)
    if (!Number.isInteger(next) || next < 0 || next > MAX_STEPS) {
      setImageStepsDraft(savedImageSteps > 0 ? String(savedImageSteps) : '')
      return
    }
    if (next === savedImageSteps) return
    saveProject({ image_steps: next })
  }

  const projectBar = (
    <StudioProjectBar
      name={detail.name}
      isWide={isWide}
      mode={mode}
      onModeChange={setMode}
      onBack={() => openProject(null)}
      comfyTarget={comfyTarget}
      onComfyTarget={onComfyTarget}
      quality={detail.quality}
      onQualityChange={(value) => saveProject({ quality: value })}
      imageQuality={detail.image_quality}
      onImageQualityChange={(value) => saveProject({ image_quality: value })}
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
      imageAspectRatio={detail.image_aspect_ratio}
      onImageAspectRatioChange={(value) =>
        saveProject({ image_aspect_ratio: value })
      }
      imageMegapixels={detail.image_megapixels}
      imageMegapixelsDraft={imageMegapixelsDraft}
      onImageMegapixelsDraftChange={setImageMegapixelsDraft}
      onCommitImageMegapixels={commitImageMegapixels}
      imageSteps={detail.image_steps}
      imageStepsDraft={imageStepsDraft}
      onImageStepsDraftChange={setImageStepsDraft}
      onCommitImageSteps={commitImageSteps}
      latentUpscale={detail.latent_upscale}
      onLatentUpscaleChange={(value) => saveProject({ latent_upscale: value })}
      busy={busy}
    />
  )

  if (mode === 'canvas') {
    return (
      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {banner}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2 p-3">
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
            episodeFiltered={episodeFilter !== ALL_EPISODES}
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

          {/* 話の絞り込みは脚本・制作でだけ効く（概要と World Bible は
              作品まるごとの面なので出さない）。 */}
          {(tab === 'script' || tab === 'production') && (
            <EpisodeFilter
              episodes={detail.episodes}
              value={episodeFilter}
              busy={busy}
              onChange={changeEpisodeFilter}
            />
          )}

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
                tree={tree}
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
                tree={tree}
                assets={detail.assets}
                allTakes={detail.takes}
                selectedShot={selectedShot}
                progress={progress}
                busy={busy}
                projectDefaults={{
                  megapixels: detail.megapixels,
                  aspect_ratio: detail.aspect_ratio,
                  steps: detail.steps,
                  latent_upscale: detail.latent_upscale,
                }}
                aspectRatios={aspectRatios}
                latentContinuity={detail.latent_continuity}
                showNsfw={showNsfw}
                showEpisodeLabels={episodeFilter === ALL_EPISODES}
                onSelectShot={setShotId}
                onRender={render}
                onSelectTake={selectTake}
                onRejectTake={rejectTake}
                onCancelTake={cancelTake}
                onDeleteTake={deleteTake}
              />
            )}
            {tab === 'edit' && (
              <EditView
                projectId={projectId}
                episodes={detail.episodes}
                exportEvent={timelineExportEvent}
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
