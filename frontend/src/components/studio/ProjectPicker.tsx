import { useRef, useState, type ReactNode } from 'react'
import { EyeOff, Loader2, Plus, RefreshCw } from 'lucide-react'

import type { StudioProjectCreate, StudioProjectSummary } from '../../types'
import { FieldError, Section } from '../ui'
import { Badge } from '../ui/badge'
import { NativeSelect } from '../NativeSelect'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { Switch } from '../ui/switch'
import { Textarea } from '../ui/textarea'
import { DEMO_PROJECTS, validateProjectForm } from './studio'

/** 一覧の 1 行に出す件数（0 は控えめ、1 件以上は強めに出して差を付ける）。 */
function Count({ value, label }: { value: number; label: string }) {
  return (
    <span
      className={`tnum text-[11px] ${
        value > 0 ? 'text-foreground/85' : 'text-muted-foreground'
      }`}
    >
      {label} {value}
    </span>
  )
}

/** 折りたたみの中の on/off 1 行（スイッチ + 説明）。 */
function ToggleRow({
  id,
  checked,
  onChange,
  children,
}: {
  id: string
  checked: boolean
  onChange: (checked: boolean) => void
  children: ReactNode
}) {
  return (
    <div className="flex items-center gap-2">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <Label htmlFor={id} className="cursor-pointer text-foreground/85">
        {children}
      </Label>
    </div>
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
  const nameRef = useRef<HTMLInputElement>(null)

  /** 空状態からの導線: 下の「新しいプロジェクト」まで運んで作品名に入る。 */
  const focusCreateForm = () => {
    // jsdom には scrollIntoView が無いので、あるときだけ呼ぶ。
    nameRef.current?.scrollIntoView?.({ block: 'center' })
    nameRef.current?.focus()
  }

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
    <div className="w-full space-y-3 p-4">
      {/* この画面そのものの名前（下の Section 見出しはフォームの見出しなので、
          ページの名前としては弱い）。 */}
      <div className="space-y-1">
        <h2 className="text-lg font-semibold text-foreground">スタジオ</h2>
        <p className="text-xs text-muted-foreground">
          作品（プロジェクト）を選んで開くか、新しく作ってください。脚本・World
          Bible・制作をひとつの作品としてまとめて扱います。
        </p>
      </div>

      <Section
        title={`プロジェクト（${projects.length}）`}
        right={
          <Button variant="outline" size="xs" onClick={onReload} disabled={loading}>
            {loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            {loading ? '読込中…' : '更新'}
          </Button>
        }
      >
        {projects.length === 0 ? (
          <div className="flex flex-col items-center gap-3 px-3 py-8 text-center">
            <p className="text-xs text-muted-foreground">
              まだプロジェクトがありません。最初の 1 本を作るところから始めます。
            </p>
            <Button onClick={focusCreateForm}>
              <Plus />
              新しいプロジェクト
            </Button>
          </div>
        ) : (
          <ul className="space-y-1">
            {projects.map((project) => (
              <li key={project.id}>
                <button
                  className="flex w-full items-center gap-2 rounded-md border border-border bg-surface-sunken px-3 py-2 text-left transition-colors hover:border-primary/50 hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                  onClick={() => onOpen(project.id)}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-foreground">
                      {project.name}
                    </span>
                    {project.synopsis && (
                      <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
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
                    <Badge variant="secondary" className="px-2 py-0 text-[11px]">
                      {project.code}
                    </Badge>
                  )}
                  <span className="tnum shrink-0 text-[11px] text-muted-foreground">
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
            <div className="space-y-1">
              <Label htmlFor="studio-new-name">作品名</Label>
              <Input
                id="studio-new-name"
                ref={nameRef}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <FieldError message={errors.name} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="studio-new-code">作品コード（任意）</Label>
              <Input
                id="studio-new-code"
                value={code}
                placeholder="EP01"
                onChange={(event) => setCode(event.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="studio-new-synopsis">あらすじ</Label>
            <Textarea
              id="studio-new-synopsis"
              className="h-20 resize-y"
              value={synopsis}
              onChange={(event) => setSynopsis(event.target.value)}
            />
          </div>
          {/* 初期設定は既定のままでよい人が多いので折りたたんでおく
              （どれも作品設定であとから変えられる）。 */}
          <details className="rounded-md border border-border bg-surface-sunken px-3 py-2">
            <summary className="cursor-pointer text-xs text-muted-foreground">
              初期設定（任意）
            </summary>
            <div className="mt-2 space-y-3">
              <div className="space-y-1">
                <Label htmlFor="studio-new-world-notes">世界観メモ</Label>
                <Textarea
                  id="studio-new-world-notes"
                  className="h-20 resize-y"
                  value={worldNotes}
                  onChange={(event) => setWorldNotes(event.target.value)}
                />
              </div>
              <ToggleRow
                id="studio-new-auto-translate"
                checked={autoTranslate}
                onChange={setAutoTranslate}
              >
                日本語のプロンプトを投入時に英訳する
              </ToggleRow>
              <ToggleRow
                id="studio-new-latent-continuity"
                checked={latentContinuity}
                onChange={setLatentContinuity}
              >
                ラテント連続性（Motion Context）で引き継ぐ
              </ToggleRow>
              <ToggleRow id="studio-new-nsfw" checked={nsfw} onChange={setNsfw}>
                <span className="flex items-center gap-1.5">
                  <EyeOff className="size-3.5" />
                  この作品のジョブをすべて NSFW 扱いにする
                </span>
              </ToggleRow>
            </div>
          </details>
          <Button onClick={create} disabled={busy}>
            作成
          </Button>
        </div>
      </Section>

      <Section title="デモから試す">
        <div className="space-y-2">
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[12rem] flex-1 space-y-1">
              <Label htmlFor="studio-demo-code">デモ作品</Label>
              <NativeSelect
                id="studio-demo-code"
                value={demoCode}
                onChange={(event) => setDemoCode(event.target.value)}
              >
                {DEMO_PROJECTS.map((demo) => (
                  <option key={demo.code} value={demo.code}>
                    {demo.name}（{demo.code}）
                  </option>
                ))}
              </NativeSelect>
            </div>
            <Button
              variant="outline"
              onClick={() => onCreateDemo(demoCode)}
              disabled={busy}
            >
              デモプロジェクトを作成
            </Button>
          </div>
        </div>
      </Section>
    </div>
  )
}
