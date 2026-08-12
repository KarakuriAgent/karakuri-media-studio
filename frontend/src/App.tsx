import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api, fieldErrorsFromError, formatDetail, wsUrl } from './api'
import ChatModal from './components/ChatModal'
import ContinueModal from './components/ContinueModal'
import GenerateForm from './components/GenerateForm'
import Header from './components/Header'
import HistoryGallery from './components/HistoryGallery'
import JobDetail from './components/JobDetail'
import ResultPane from './components/ResultPane'
import SettingsPage from './components/SettingsPage'
import AgentView from './components/agent/AgentView'
import StudioView from './components/studio/StudioView'
import { Banner } from './components/ui'
import {
  audioJobPayload,
  formStateFromParams,
  imageWorkflowNeedsSource,
  initialForm,
  jobModelOverrides,
  jobSelects,
  jobSteps,
  jobWorkflowIds,
  referenceFields,
  validateForm,
  type FormState,
} from './form'
import type {
  AgentProgress,
  CanvasProgress,
  ComfyTarget,
  Health,
  Job,
  JobContinue,
  JobCreate,
  JobProgress,
  LibraryProgress,
  Options,
  Settings,
} from './types'

const ACTIVE_STATUSES = ['queued', 'prompting', 'running']

const SHOW_NSFW_KEY = 'showNsfw'

/** 既定は非表示。sessionStorage 保存なのでタブを閉じる（新しいアクセス）とオフに戻る。 */
function initialShowNsfw(): boolean {
  try {
    return window.sessionStorage.getItem(SHOW_NSFW_KEY) === '1'
  } catch {
    return false
  }
}

export default function App() {
  const [form, setForm] = useState<FormState>(initialForm)
  const [health, setHealth] = useState<Health | null>(null)
  const [checkingHealth, setCheckingHealth] = useState(false)
  const [options, setOptions] = useState<Options | null>(null)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  // 生成フォームの接続先プルダウン用（SPEC §5）。実体はサーバー側の設定なので、
  // 設定ページで変えたときも読み直して揃える。
  const [settings, setSettings] = useState<Settings | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [loadingJobs, setLoadingJobs] = useState(false)
  const [activeJob, setActiveJob] = useState<Job | null>(null)
  const [detailJob, setDetailJob] = useState<Job | null>(null)
  // 続き生成の上書きモーダル（開いているジョブと、その中に出すエラー）。
  const [continueJob, setContinueJob] = useState<Job | null>(null)
  const [continueError, setContinueError] = useState<string | null>(null)
  const [progress, setProgress] = useState<Record<string, JobProgress>>({})
  const [wsConnected, setWsConnected] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [detailBusy, setDetailBusy] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [view, setView] = useState<'main' | 'agent' | 'studio' | 'settings'>('main')
  const [agentEvent, setAgentEvent] = useState<AgentProgress | null>(null)
  // キャンバスのエージェント実行（会話に足された 1 件と実行中フラグ）
  const [canvasEvent, setCanvasEvent] = useState<CanvasProgress | null>(null)
  const [chatSessionId, setChatSessionId] = useState<string | null>(null)
  const [showNsfw, setShowNsfw] = useState(initialShowNsfw)
  // エラーではない一言（パラメータ復元で LoRA を落としたとき等）。
  const [notice, setNotice] = useState<string | null>(null)
  // ライブラリが変わるたびに増える。開いているモーダルの読み直しに使う。
  const [libraryVersion, setLibraryVersion] = useState(0)

  const patch = useCallback(
    (changes: Partial<FormState>) => setForm((prev) => ({ ...prev, ...changes })),
    [],
  )

  /**
   * このセッションで投入したジョブの id（メモリだけ、保存しない）。
   *
   * NSFW の自動判定は生成中に確定するので、投げたジョブが途中で一覧から消えて
   * しまう。自分で投げたものは表示トグルに関わらず出し続け、NSFW ならぼかす。
   * リロードすれば空に戻る = ふつうの NSFW フィルタに従う。
   */
  const sessionJobIds = useRef<Set<string>>(new Set())

  /** 投入したジョブを「このセッションのもの」として覚え、結果ペインに出す。 */
  const trackJob = useCallback((job: Job) => {
    sessionJobIds.current.add(job.id)
    setActiveJob(job)
  }, [])

  /** 表示トグルがオフでも、このセッションで投げたジョブは隠さない。 */
  const isVisible = useCallback(
    (job: Job) => showNsfw || !job.nsfw || sessionJobIds.current.has(job.id),
    [showNsfw],
  )

  // ---------------------------------------------------------------- NSFW 表示
  useEffect(() => {
    try {
      window.sessionStorage.setItem(SHOW_NSFW_KEY, showNsfw ? '1' : '0')
    } catch {
      /* sessionStorage が使えない環境ではメモリ上のみ有効 */
    }
  }, [showNsfw])

  // 非表示に戻したら、開いている NSFW ジョブの選択も解除する（このセッションで
  // 投げたものは、ぼかしたうえで出し続ける）。
  useEffect(() => {
    if (showNsfw) return
    const keep = (job: Job | null) =>
      job?.nsfw && !sessionJobIds.current.has(job.id) ? null : job
    setActiveJob(keep)
    setDetailJob(keep)
  }, [showNsfw])

  const pushError = useCallback((error: unknown) => {
    const message =
      error instanceof ApiError
        ? formatDetail(error.detail)
        : error instanceof Error
          ? error.message
          : String(error)
    setErrors((previous) => [...previous.slice(-4), message])
  }, [])

  // ---------------------------------------------------------------- loaders

  const loadHealth = useCallback(async () => {
    setCheckingHealth(true)
    try {
      setHealth(await api.health())
    } catch (error) {
      setHealth(null)
      pushError(error)
    } finally {
      setCheckingHealth(false)
    }
  }, [pushError])

  const loadOptions = useCallback(async () => {
    try {
      const next = await api.options()
      setOptions(next)
      setOptionsError(next.comfy_error)
      setForm((previous) => {
        const changes: Partial<FormState> = {}
        if (
          next.aspect_ratios.length > 0 &&
          !next.aspect_ratios.includes(previous.aspectRatio)
        ) {
          changes.aspectRatio = next.aspect_ratios[0]
        }
        if (
          next.video_workflows.length > 0 &&
          !next.video_workflows.some((item) => item.id === previous.videoWorkflow)
        ) {
          changes.videoWorkflow =
            next.default_video_workflow || next.video_workflows[0].id
        }
        if (
          next.image_workflows.length > 0 &&
          !next.image_workflows.some((item) => item.id === previous.imageWorkflow)
        ) {
          changes.imageWorkflow =
            next.default_image_workflow || next.image_workflows[0].id
        }
        if (
          next.audio_workflows.length > 0 &&
          !next.audio_workflows.some((item) => item.id === previous.audioWorkflow)
        ) {
          changes.audioWorkflow =
            next.default_audio_workflow || next.audio_workflows[0].id
        }
        return Object.keys(changes).length > 0 ? { ...previous, ...changes } : previous
      })
    } catch (error) {
      setOptionsError(
        error instanceof ApiError ? formatDetail(error.detail) : String(error),
      )
    }
  }, [])

  const loadSettings = useCallback(async () => {
    try {
      const loaded = await api.getSettings()
      setSettings(loaded)
    } catch (error) {
      pushError(error)
    }
  }, [patch, pushError])

  /**
   * 接続先を切り替える（SPEC §5）。サーバー側の設定に保存し、選択肢と接続状態を
   * 取り直す（ComfyUI が変われば使えるモデル・LoRA も変わるため）。
   */
  const changeComfyTarget = useCallback(
    async (target: ComfyTarget) => {
      try {
        setSettings(await api.putSettings({ comfy_target: target }))
      } catch (error) {
        pushError(error)
        return
      }
      await Promise.all([loadOptions(), loadHealth()])
    },
    [loadHealth, loadOptions, pushError],
  )

  const loadJobs = useCallback(async () => {
    setLoadingJobs(true)
    try {
      const next = await api.listJobs()
      setJobs(next)
      setActiveJob((current) =>
        current ? (next.find((job) => job.id === current.id) ?? current) : current,
      )
    } catch (error) {
      pushError(error)
    } finally {
      setLoadingJobs(false)
    }
  }, [pushError])

  useEffect(() => {
    void loadHealth()
    void loadOptions()
    void loadJobs()
    void loadSettings()
  }, [loadHealth, loadOptions, loadJobs, loadSettings])

  // ------------------------------------------------------------------- WS

  const reloadRef = useRef(loadJobs)
  reloadRef.current = loadJobs
  const loadOptionsRef = useRef(loadOptions)
  loadOptionsRef.current = loadOptions

  useEffect(() => {
    let socket: WebSocket | null = null
    let timer: number | undefined
    let closed = false

    const connect = () => {
      socket = new WebSocket(wsUrl())
      socket.onopen = () => setWsConnected(true)
      socket.onclose = () => {
        setWsConnected(false)
        if (!closed) timer = window.setTimeout(connect, 3000)
      }
      socket.onerror = () => socket?.close()
      socket.onmessage = (event) => {
        try {
          const frame = JSON.parse(event.data as string) as
            | JobProgress
            | AgentProgress
            | CanvasProgress
            | LibraryProgress
          if (frame?.type === 'agent') {
            setAgentEvent(frame)
            return
          }
          if (frame?.type === 'canvas') {
            setCanvasEvent(frame)
            return
          }
          // ライブラリの自動タグ生成が終わった: 選択肢を取り直し、開いている
          // モーダルにも読み直させる（SPEC §7.2）。
          if (frame?.type === 'library') {
            setLibraryVersion((previous) => previous + 1)
            void loadOptionsRef.current()
            return
          }
          const payload = frame as JobProgress
          if (payload?.type !== 'job') return
          // progress を持たないフレーム（メッセージだけの通知など）でバーが 0%
          // に落ちないよう、直前の値を引き継いでからマージする。
          setProgress((previous) => {
            const before = previous[payload.job_id]
            return {
              ...previous,
              [payload.job_id]: {
                ...payload,
                progress: payload.progress ?? before?.progress ?? null,
              },
            }
          })
          // NSFW 判定が確定したフレームは一覧を取り直して反映する。
          if (
            payload.nsfw != null ||
            !ACTIVE_STATUSES.includes(payload.status)
          )
            void reloadRef.current()
          setActiveJob((current) =>
            current && current.id === payload.job_id
              ? { ...current, status: payload.status }
              : current,
          )
        } catch {
          /* ignore malformed frames */
        }
      }
    }
    connect()
    return () => {
      closed = true
      if (timer) window.clearTimeout(timer)
      socket?.close()
    }
  }, [])

  // Poll while something is in flight (WS covers most of it; this is a safety net).
  useEffect(() => {
    const pending = jobs.some((job) => ACTIVE_STATUSES.includes(job.status))
    if (!pending) return
    const id = window.setInterval(() => void loadJobs(), 5000)
    return () => window.clearInterval(id)
  }, [jobs, loadJobs])

  // ---------------------------------------------------------------- actions

  const showJob = useCallback(
    async (job: Job) => {
      setActiveJob(job)
      try {
        setActiveJob(await api.getJob(job.id))
      } catch (error) {
        pushError(error)
      }
    },
    [pushError],
  )

  const selectJob = useCallback((job: Job) => void showJob(job), [showJob])

  /** Toolbar "詳細": open the parameter drawer with a freshly fetched job. */
  const openDetail = useCallback(
    (job: Job) => {
      setDetailError(null)
      setDetailJob(job)
      void api
        .getJob(job.id)
        .then(setDetailJob)
        .catch((error: unknown) => pushError(error))
    },
    [pushError],
  )

  const submit = async () => {
    setSubmitting(true)
    setFieldErrors({})
    try {
      const workflow =
        options?.video_workflows.find((item) => item.id === form.videoWorkflow) ?? null
      const imageWorkflow =
        options?.image_workflows.find((item) => item.id === form.imageWorkflow) ??
        null
      const audioWorkflow =
        options?.audio_workflows.find((item) => item.id === form.audioWorkflow) ??
        null
      const problems = validateForm(form, imageWorkflow, audioWorkflow, workflow)
      if (Object.keys(problems).length > 0) {
        setFieldErrors(problems)
        return
      }
      // 音声は独立ジョブ: 画像・動画のフィールドは一切送らない。
      if (form.mode === 'audio') {
        const created = await api.createJob(
          audioJobPayload(form, audioWorkflow, options?.model_slots),
        )
        setChatSessionId(null)
        trackJob(created)
        await loadJobs()
        return
      }
      const needs = (name: string) =>
        form.mode !== 'image_only' && (workflow?.requires ?? []).includes(name as never)
      // 任意入力（開始フレーム・最後のフレーム）も、選ばれていれば送る
      const accepts = (name: string) =>
        needs(name) ||
        (form.mode !== 'image_only' && (workflow?.supports ?? []).includes(name))
      // an editing image workflow takes the picture itself, in every mode that
      // runs the image stage (including `full`)
      const imageNeedsSource =
        form.mode !== 'i2v' && imageWorkflowNeedsSource(imageWorkflow)
      // ショット割り / Elements は動画ステージのパラメータ（SPEC §3.1）
      const runsVideo = form.mode === 'full' || form.mode === 'i2v'
      const payload: JobCreate = {
        mode: form.mode,
        video_workflow: form.videoWorkflow,
        image_workflow: form.imageWorkflow,
        image_prompt: form.mode === 'i2v' ? '' : form.imagePrompt,
        // ショット割りのワークフローでは本文がショット側にあるので、
        // トップレベルのプロンプトは送らない（送ると 422、SPEC §3.1）
        video_prompt:
          form.mode === 'image_only' || (runsVideo && workflow?.multi_shot)
            ? ''
            : form.videoPrompt,
        negative_prompt: form.negativePrompt,
        aspect_ratio: form.aspectRatio,
        megapixels: form.megapixels,
        loras:
          form.mode === 'i2v'
            ? []
            : form.loras.map(({ lora_name, trigger_word, strength }) => ({
                lora_name,
                trigger_word,
                strength,
              })),
        trigger_text: form.mode === 'i2v' ? '' : form.triggerText,
        // the video LoRA chain only exists when a video stage runs
        video_loras:
          form.mode === 'image_only'
            ? []
            : form.videoLoras.map(({ lora_name, trigger_word, strength }) => ({
                lora_name,
                trigger_word,
                strength,
              })),
        video_trigger_text:
          form.mode === 'image_only' ? '' : form.videoTriggerText,
        duration: form.duration,
        fps: form.fps,
        // サンプリング回数（SPEC §3.1）: そのモードで走るワークフローが宣言して
        // いるときだけ送る（未指定 = 0 はテンプレートの既定値のまま）
        steps: jobSteps(
          form,
          form.mode === 'image_only' ? null : workflow,
          form.mode === 'i2v' ? null : imageWorkflow,
        ),
        audio_path: needs('audio') ? form.audioPath || null : null,
        // in full mode the image stage produces the start frame
        source_image:
          (form.mode === 'i2v' && accepts('image')) || imageNeedsSource
            ? form.sourceImage || null
            : null,
        end_image: accepts('end_image') ? form.endImage || null : null,
        reference_video: needs('video') ? form.referenceVideo || null : null,
        // マルチモーダル参照（SPEC §3.1）: そのワークフローが宣言している欄だけを
        // 送る（宣言していないワークフローに渡すと 422 になる）。
        ...Object.fromEntries(
          referenceFields(form.mode === 'i2v' ? workflow : null).map((item) => [
            item.name,
            form[item.field],
          ]),
        ),
        // ショット割り / Elements（SPEC §3.1）: 宣言のあるワークフローを動画
        // ステージで走らせるときだけ送る（そうでなければ空 = 送らないのと同じ）。
        multi_shots:
          runsVideo && workflow?.multi_shot ? form.multiShots : [],
        kling_elements:
          runsVideo && workflow?.elements ? form.klingElements : [],
        seed: form.seedLocked ? form.seed : null,
        // そのモードで走るワークフローが宣言している選択項目だけ（未指定は送らない）
        selects: jobSelects(
          form,
          form.mode === 'image_only' ? null : workflow,
          form.mode === 'i2v' ? null : imageWorkflow,
        ),
        // 走らせるワークフローのスロットだけ（既定値のままのものは送らない）
        model_overrides: jobModelOverrides(
          form,
          options?.model_slots,
          jobWorkflowIds(form),
        ),
        chat_session_id: chatSessionId,
        // チェックしたときだけ manual 指定として送る（オフ = null で自動判定）
        nsfw: form.nsfw ? true : null,
      }
      const job = await api.createJob(payload)
      setChatSessionId(null)
      trackJob(job)
      await loadJobs()
    } catch (error) {
      const fields = fieldErrorsFromError(error)
      setFieldErrors(fields)
      if (Object.keys(fields).length === 0) pushError(error)
    } finally {
      setSubmitting(false)
    }
  }

  /** 「続きを生成」: 上書きフォームを開く（そのまま押せば全項目引き継ぎ）。 */
  const openContinue = (job: Job) => {
    setContinueError(null)
    setContinueJob(job)
  }

  /**
   * 続き生成（`POST /api/jobs/{id}/continue`）。
   *
   * `body` に入れた項目だけが元ジョブの設定を上書きする（空 = 全部引き継ぐ）。
   */
  const continueFrom = async (job: Job, body: JobContinue = {}) => {
    setDetailBusy(true)
    setDetailError(null)
    setContinueError(null)
    try {
      const next = await api.continueJob(job.id, body)
      trackJob(next)
      setContinueJob(null)
      setDetailJob(null)
      await loadJobs()
    } catch (error) {
      const message =
        error instanceof ApiError ? formatDetail(error.detail) : String(error)
      setDetailError(message)
      setContinueError(message)
      pushError(error)
    } finally {
      setDetailBusy(false)
    }
  }

  /** 再実行。`randomizeSeed` を false にすると元ジョブと同じシードで流し直す。 */
  const rerun = async (job: Job, randomizeSeed = true) => {
    setDetailBusy(true)
    setDetailError(null)
    try {
      const next = await api.rerunJob(job.id, randomizeSeed)
      trackJob(next)
      setDetailJob(null)
      await loadJobs()
    } catch (error) {
      setDetailError(
        error instanceof ApiError ? formatDetail(error.detail) : String(error),
      )
    } finally {
      setDetailBusy(false)
    }
  }

  /**
   * 過去ジョブの params をフォームへ書き戻す（手直ししてから流し直すための入口）。
   *
   * 差分ではなく初期値の上に重ねる: そのジョブに無かった項目まで前の入力が残ると
   * 「復元したのに違うものが出る」ので、丸ごとそのジョブの状態にする。
   */
  const restoreParams = (job: Job) => {
    const { patch: changes, missingLoras } = formStateFromParams(
      job.params ?? {},
      options,
    )
    setForm({ ...initialForm, ...changes })
    setFieldErrors({})
    setNotice(
      missingLoras.length > 0
        ? `登録簿に無い LoRA をスキップしました: ${missingLoras.join(', ')}`
        : null,
    )
    // フォームを見せたいので、開いていれば詳細ドロワーを閉じる。
    setDetailJob(null)
  }

  const remove = async (job: Job) => {
    if (!window.confirm('このジョブを削除しますか？')) return
    setDetailBusy(true)
    try {
      await api.deleteJob(job.id)
      setDetailJob(null)
      setActiveJob((current) => (current?.id === job.id ? null : current))
      await loadJobs()
    } catch (error) {
      setDetailError(
        error instanceof ApiError ? formatDetail(error.detail) : String(error),
      )
    } finally {
      setDetailBusy(false)
    }
  }

  /** NSFW フラグの手動トグル（manual として保存され、自動判定に上書きされない）。 */
  const toggleNsfw = async (job: Job, nsfw: boolean) => {
    try {
      const next = await api.setJobNsfw(job.id, nsfw)
      const flags = { nsfw: next.nsfw, nsfw_source: next.nsfw_source }
      setJobs((previous) =>
        previous.map((item) => (item.id === next.id ? { ...item, ...flags } : item)),
      )
      setActiveJob((current) =>
        current?.id === next.id ? { ...current, ...flags } : current,
      )
      setDetailJob((current) =>
        current?.id === next.id ? { ...current, ...flags } : current,
      )
    } catch (error) {
      pushError(error)
    }
  }

  // 表示トグルがオフのあいだは NSFW を渡さない（このセッションで投げたものだけ、
  // 表示コンポーネント側でぼかしたうえで渡す）。
  const visibleJobs = jobs.filter(isVisible)
  const shownJob = activeJob && !isVisible(activeJob) ? null : activeJob
  const shownDetailJob = detailJob && !isVisible(detailJob) ? null : detailJob
  const queue = visibleJobs.filter((job) => ACTIVE_STATUSES.includes(job.status))

  return (
    <div className="flex h-full flex-col">
      <Header
        health={health}
        checking={checkingHealth}
        onRefresh={() => void loadHealth()}
        onOpenSettings={() => setView('settings')}
        wsConnected={wsConnected}
        view={view}
        onView={setView}
        showNsfw={showNsfw}
        onShowNsfw={setShowNsfw}
      />

      {notice && (
        <div className="px-4 py-2">
          <Banner tone="warn" onClose={() => setNotice(null)}>
            {notice}
          </Banner>
        </div>
      )}

      {errors.length > 0 && (
        <div className="flex flex-col gap-1 px-4 py-2">
          {errors.map((message, index) => (
            <Banner
              key={index}
              onClose={() =>
                setErrors((previous) => previous.filter((_, i) => i !== index))
              }
            >
              {message}
            </Banner>
          ))}
        </div>
      )}

      {view === 'settings' && (
        <SettingsPage
          options={options}
          onBack={() => setView('main')}
          onChanged={() => {
            void loadOptions()
            void loadHealth()
            void loadSettings()
          }}
        />
      )}

      {view === 'agent' && (
        <AgentView
          event={agentEvent}
          progress={progress}
          showNsfw={showNsfw}
          comfyTarget={settings?.comfy_target ?? null}
          onComfyTarget={(target) => void changeComfyTarget(target)}
        />
      )}

      {/* ドラマスタジオ（プロジェクト -> 脚本 -> Shot ごとの生成 -> Take の採用）。
          Take の進捗は生成フォームと同じ WS のジョブフレームで届く。 */}
      {view === 'studio' && (
        <StudioView
          progress={progress}
          canvasEvent={canvasEvent}
          aspectRatios={options?.aspect_ratios ?? []}
          showNsfw={showNsfw}
          comfyTarget={settings?.comfy_target ?? null}
          onComfyTarget={(target) => void changeComfyTarget(target)}
        />
      )}

      {view === 'main' && (
        <>
        {/* 狭幅は縦積み 1 カラム（ページ縦スクロール）、lg 以上は従来の 2 カラム */}
        <main className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 lg:flex-row lg:overflow-hidden">
          <aside className="w-full shrink-0 lg:w-[400px] lg:overflow-y-auto lg:pr-1">
            <GenerateForm
              form={form}
              patch={patch}
              options={options}
              optionsError={optionsError}
              onReloadOptions={() => void loadOptions()}
              onOpenChat={() => setChatOpen(true)}
              onSubmit={() => void submit()}
              submitting={submitting}
              fieldErrors={fieldErrors}
              comfyTarget={settings?.comfy_target ?? null}
              onComfyTarget={(target) => void changeComfyTarget(target)}
              // 履歴モーダルは自前で NSFW を切り替えるので、フィルタ前の全ジョブを渡す
              jobs={jobs}
              showNsfw={showNsfw}
              libraryVersion={libraryVersion}
            />
          </aside>

          {/* 狭幅ではこの列も ResultPane も高さを固定せず、内容なりに伸ばして
              main の縦スクロールへ逃がす（lg 以上は従来どおり高さを分け合う）。 */}
          <div className="flex min-w-0 flex-1 flex-col gap-2 lg:min-h-0">
            <div className="flex-1 lg:min-h-0">
              <ResultPane
                job={shownJob}
                progress={shownJob ? progress[shownJob.id] : undefined}
                onRerun={(job, randomizeSeed) => void rerun(job, randomizeSeed)}
                onRestoreParams={restoreParams}
                onContinue={(job) => openContinue(job)}
                onDelete={(job) => void remove(job)}
                onOpenDetail={(job) => openDetail(job)}
                onToggleNsfw={(job, nsfw) => void toggleNsfw(job, nsfw)}
                busy={detailBusy}
                queue={queue}
                showNsfw={showNsfw}
                onLibraryChanged={() => void loadOptions()}
                library={options?.library}
              />
            </div>

            <section className="h-36 shrink-0 rounded-lg border border-ink-700 bg-ink-800/60">
              <HistoryGallery
                jobs={visibleJobs}
                selectedId={shownJob?.id ?? null}
                loading={loadingJobs}
                onReload={() => void loadJobs()}
                onSelect={selectJob}
                showNsfw={showNsfw}
              />
            </section>
          </div>
        </main>
        </>
      )}

      {shownDetailJob && (
        <JobDetail
          job={shownDetailJob}
          busy={detailBusy}
          error={detailError}
          onClose={() => setDetailJob(null)}
          onRerun={(job, randomizeSeed) => void rerun(job, randomizeSeed)}
          onRestoreParams={restoreParams}
          onContinue={(job) => openContinue(job)}
          onDelete={(job) => void remove(job)}
          onToggleNsfw={(job, nsfw) => void toggleNsfw(job, nsfw)}
          showNsfw={showNsfw}
          onLibraryChanged={() => void loadOptions()}
          library={options?.library}
        />
      )}

      {/* 続き生成の上書きフォーム（既定は全項目「元ジョブを引き継ぐ」）。 */}
      {continueJob && (
        <ContinueModal
          job={continueJob}
          options={options}
          busy={detailBusy}
          error={continueError}
          onClose={() => setContinueJob(null)}
          onSubmit={(body) => void continueFrom(continueJob, body)}
        />
      )}

      {chatOpen && (
        <ChatModal
          form={form}
          patch={patch}
          options={options}
          onClose={() => setChatOpen(false)}
          onSessionId={setChatSessionId}
        />
      )}

    </div>
  )
}
