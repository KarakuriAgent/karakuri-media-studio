import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api, fieldErrorsFromError, formatDetail, wsUrl } from './api'
import ChatModal from './components/ChatModal'
import GenerateForm from './components/GenerateForm'
import Header from './components/Header'
import HistoryGallery from './components/HistoryGallery'
import JobDetail from './components/JobDetail'
import ResultPane from './components/ResultPane'
import SettingsPage from './components/SettingsPage'
import AgentView from './components/agent/AgentView'
import { Banner } from './components/ui'
import { initialForm, type FormState } from './form'
import type {
  AgentProgress,
  Health,
  Job,
  JobCreate,
  JobProgress,
  Options,
} from './types'

const ACTIVE_STATUSES = ['queued', 'prompting', 'running']

export default function App() {
  const [form, setForm] = useState<FormState>(initialForm)
  const [health, setHealth] = useState<Health | null>(null)
  const [checkingHealth, setCheckingHealth] = useState(false)
  const [options, setOptions] = useState<Options | null>(null)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [loadingJobs, setLoadingJobs] = useState(false)
  const [activeJob, setActiveJob] = useState<Job | null>(null)
  const [detailJob, setDetailJob] = useState<Job | null>(null)
  const [progress, setProgress] = useState<Record<string, JobProgress>>({})
  const [wsConnected, setWsConnected] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [detailBusy, setDetailBusy] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [view, setView] = useState<'main' | 'agent' | 'settings'>('main')
  const [agentEvent, setAgentEvent] = useState<AgentProgress | null>(null)
  const [chatSessionId, setChatSessionId] = useState<string | null>(null)

  const patch = useCallback(
    (changes: Partial<FormState>) => setForm((prev) => ({ ...prev, ...changes })),
    [],
  )

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
      setForm((previous) =>
        next.aspect_ratios.length > 0 &&
        !next.aspect_ratios.includes(previous.aspectRatio)
          ? { ...previous, aspectRatio: next.aspect_ratios[0] }
          : previous,
      )
    } catch (error) {
      setOptionsError(
        error instanceof ApiError ? formatDetail(error.detail) : String(error),
      )
    }
  }, [])

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
  }, [loadHealth, loadOptions, loadJobs])

  // ------------------------------------------------------------------- WS

  const reloadRef = useRef(loadJobs)
  reloadRef.current = loadJobs

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
          if (frame?.type === 'agent') {
            setAgentEvent(frame)
            return
          }
          const payload = frame as JobProgress
          if (payload?.type !== 'job') return
          setProgress((previous) => ({ ...previous, [payload.job_id]: payload }))
          if (!ACTIVE_STATUSES.includes(payload.status)) void reloadRef.current()
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
      const payload: JobCreate = {
        mode: form.mode,
        image_prompt: form.mode === 'i2v' ? '' : form.imagePrompt,
        video_prompt: form.mode === 'image_only' ? '' : form.videoPrompt,
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
        duration: form.duration,
        fps: form.fps,
        audio_path: form.mode === 'image_only' ? null : form.audioPath || null,
        source_image: form.mode === 'i2v' ? form.sourceImage || null : null,
        seed: form.seedLocked ? form.seed : null,
        chat_session_id: chatSessionId,
      }
      const job = await api.createJob(payload)
      setChatSessionId(null)
      setActiveJob(job)
      await loadJobs()
    } catch (error) {
      const fields = fieldErrorsFromError(error)
      setFieldErrors(fields)
      if (Object.keys(fields).length === 0) pushError(error)
    } finally {
      setSubmitting(false)
    }
  }

  const continueFrom = async (job: Job) => {
    setDetailBusy(true)
    setDetailError(null)
    try {
      const next = await api.continueJob(job.id)
      setActiveJob(next)
      setDetailJob(null)
      await loadJobs()
    } catch (error) {
      const message =
        error instanceof ApiError ? formatDetail(error.detail) : String(error)
      setDetailError(message)
      pushError(error)
    } finally {
      setDetailBusy(false)
    }
  }

  const rerun = async (job: Job) => {
    setDetailBusy(true)
    setDetailError(null)
    try {
      const next = await api.rerunJob(job.id)
      setActiveJob(next)
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

  const queue = jobs.filter((job) => ACTIVE_STATUSES.includes(job.status))

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
      />

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
          }}
        />
      )}

      {view === 'agent' && <AgentView event={agentEvent} progress={progress} />}

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
              jobs={jobs}
            />
          </aside>

          <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
            <div className="min-h-[40vh] flex-1 lg:min-h-0">
              <ResultPane
                job={activeJob}
                progress={activeJob ? progress[activeJob.id] : undefined}
                onRerun={(job) => void rerun(job)}
                onContinue={(job) => void continueFrom(job)}
                onDelete={(job) => void remove(job)}
                onOpenDetail={(job) => openDetail(job)}
                busy={detailBusy}
                queue={queue}
              />
            </div>

            <section className="h-36 shrink-0 rounded-lg border border-ink-700 bg-ink-800/60">
              <HistoryGallery
                jobs={jobs}
                selectedId={activeJob?.id ?? null}
                loading={loadingJobs}
                onReload={() => void loadJobs()}
                onSelect={selectJob}
              />
            </section>
          </div>
        </main>
        </>
      )}

      {detailJob && (
        <JobDetail
          job={detailJob}
          busy={detailBusy}
          error={detailError}
          onClose={() => setDetailJob(null)}
          onRerun={(job) => void rerun(job)}
          onContinue={(job) => void continueFrom(job)}
          onDelete={(job) => void remove(job)}
        />
      )}

      {chatOpen && (
        <ChatModal
          form={form}
          patch={patch}
          onClose={() => setChatOpen(false)}
          onSessionId={setChatSessionId}
        />
      )}

    </div>
  )
}
