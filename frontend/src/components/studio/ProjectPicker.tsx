import { useState } from 'react'
import type { StudioProjectCreate, StudioProjectSummary } from '../../types'
import { FieldError, Section } from '../ui'
import { DEMO_PROJECTS, validateProjectForm } from './studio'

/** 一覧の 1 行に出す件数（0 は薄く出す）。 */
function Count({ value, label }: { value: number; label: string }) {
  return (
    <span className={`text-[10px] ${value > 0 ? 'text-slate-400' : 'text-slate-600'}`}>
      {label} {value}
    </span>
  )
}

/** プロジェクト未選択のときの画面: 一覧から開くか、新しく作る。 */
export default function ProjectPicker({
  projects,
  loading,
  onOpen,
  onCreate,
  onCreateDemo,
  onReload,
  busy,
}: {
  projects: StudioProjectSummary[]
  loading: boolean
  onOpen: (id: string) => void
  onCreate: (payload: StudioProjectCreate) => void
  /** デモ作品を 1 本まるごと作る（同じ作品コードが既にあれば 409）。 */
  onCreateDemo: (code: string) => void
  onReload: () => void
  busy: boolean
}) {
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [synopsis, setSynopsis] = useState('')
  // 作品の初期設定（あとから作品設定でも変えられるので、ここでは折りたたむ）。
  // 既定値はバックエンドの StudioProjectCreate と揃えてある。
  const [worldNotes, setWorldNotes] = useState('')
  const [autoTranslate, setAutoTranslate] = useState(true)
  const [latentContinuity, setLatentContinuity] = useState(false)
  const [nsfw, setNsfw] = useState(false)
  const [demoCode, setDemoCode] = useState(DEMO_PROJECTS[0].code)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const create = () => {
    const problems = validateProjectForm({ name })
    setErrors(problems)
    if (Object.keys(problems).length > 0) return
    onCreate({
      name: name.trim(),
      code: code.trim(),
      synopsis,
      world_notes: worldNotes,
      auto_translate: autoTranslate,
      latent_continuity: latentContinuity,
      nsfw,
    })
    setName('')
    setCode('')
    setSynopsis('')
    setWorldNotes('')
    setAutoTranslate(true)
    setLatentContinuity(false)
    setNsfw(false)
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-3 p-4">
      <Section
        title={`プロジェクト（${projects.length}）`}
        right={
          <button
            className="btn-ghost !py-1 text-xs"
            onClick={onReload}
            disabled={loading}
          >
            {loading ? '読込中…' : '更新'}
          </button>
        }
      >
        {projects.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-slate-600">
            まだプロジェクトがありません。下から作ってください。
          </p>
        ) : (
          <ul className="space-y-1">
            {projects.map((project) => (
              <li key={project.id}>
                <button
                  className="flex w-full items-center gap-2 rounded-md border border-ink-600 bg-ink-800 px-3 py-2 text-left transition-colors hover:border-ink-500"
                  onClick={() => onOpen(project.id)}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-slate-100">
                      {project.name}
                    </span>
                    {project.synopsis && (
                      <span className="mt-0.5 block truncate text-[11px] text-slate-500">
                        {project.synopsis}
                      </span>
                    )}
                    <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5">
                      <Count value={project.shot_count ?? 0} label="カット" />
                      <Count value={project.asset_count ?? 0} label="素材" />
                      <Count
                        value={project.selected_take_count ?? 0}
                        label="採用済み"
                      />
                    </span>
                  </span>
                  {project.code && (
                    <span className="chip !px-2 !py-0 border-ink-500 bg-ink-700 text-[10px] text-slate-400">
                      {project.code}
                    </span>
                  )}
                  <span className="shrink-0 text-[10px] text-slate-600">
                    {project.updated_at.slice(0, 10)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="新しいプロジェクト">
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="label" htmlFor="studio-new-name">
                作品名
              </label>
              <input
                id="studio-new-name"
                className="field"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <FieldError message={errors.name} />
            </div>
            <div>
              <label className="label" htmlFor="studio-new-code">
                作品コード（任意）
              </label>
              <input
                id="studio-new-code"
                className="field"
                value={code}
                placeholder="EP01"
                onChange={(event) => setCode(event.target.value)}
              />
            </div>
          </div>
          <div>
            <label className="label" htmlFor="studio-new-synopsis">
              あらすじ
            </label>
            <textarea
              id="studio-new-synopsis"
              className="field h-20 resize-y"
              value={synopsis}
              onChange={(event) => setSynopsis(event.target.value)}
            />
          </div>
          {/* 初期設定は既定のままでよい人が多いので折りたたんでおく
              （どれも作品設定であとから変えられる）。 */}
          <details className="rounded-md border border-ink-600 bg-ink-900/40 px-3 py-2">
            <summary className="cursor-pointer text-xs text-slate-400">
              初期設定（任意）
            </summary>
            <div className="mt-2 space-y-3">
              <div>
                <label className="label" htmlFor="studio-new-world-notes">
                  世界観メモ
                </label>
                <textarea
                  id="studio-new-world-notes"
                  className="field h-20 resize-y"
                  value={worldNotes}
                  onChange={(event) => setWorldNotes(event.target.value)}
                />
              </div>
              <label className="flex items-center gap-2 text-xs text-slate-300">
                <input
                  type="checkbox"
                  className="accent-accent-500"
                  checked={autoTranslate}
                  onChange={(event) => setAutoTranslate(event.target.checked)}
                />
                日本語のプロンプトを投入時に英訳する
              </label>
              <label className="flex items-center gap-2 text-xs text-slate-300">
                <input
                  type="checkbox"
                  className="accent-accent-500"
                  checked={latentContinuity}
                  onChange={(event) => setLatentContinuity(event.target.checked)}
                />
                ラテント連続性（Motion Context）で引き継ぐ
              </label>
              <label className="flex items-center gap-2 text-xs text-slate-300">
                <input
                  type="checkbox"
                  className="accent-accent-500"
                  checked={nsfw}
                  onChange={(event) => setNsfw(event.target.checked)}
                />
                🫣 この作品のジョブをすべて NSFW 扱いにする
              </label>
            </div>
          </details>
          <button className="btn-primary" onClick={create} disabled={busy}>
            作成
          </button>
        </div>
      </Section>

      <Section title="デモから試す">
        <div className="space-y-2">
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[12rem] flex-1">
              <label className="label" htmlFor="studio-demo-code">
                デモ作品
              </label>
              <select
                id="studio-demo-code"
                className="field"
                value={demoCode}
                onChange={(event) => setDemoCode(event.target.value)}
              >
                {DEMO_PROJECTS.map((demo) => (
                  <option key={demo.code} value={demo.code}>
                    {demo.name}（{demo.code}）
                  </option>
                ))}
              </select>
            </div>
            <button
              className="btn-ghost"
              onClick={() => onCreateDemo(demoCode)}
              disabled={busy}
            >
              デモプロジェクトを作成
            </button>
          </div>
        </div>
      </Section>
    </div>
  )
}
