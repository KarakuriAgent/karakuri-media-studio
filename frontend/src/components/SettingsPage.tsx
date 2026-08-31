import { useEffect, useState, type ReactNode } from 'react'
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  DownloadCloud,
  Image as ImageIcon,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  X,
} from 'lucide-react'

import { api, wsUrl } from '../api'
import {
  currentPushPermission,
  ensurePushSubscription,
  type PushPermission,
} from '../push'
import {
  COMFY_TARGETS,
  COMFY_TARGET_LABELS,
  DEFAULT_FAMILY,
  FAMILY_LABELS,
  IMAGE_FAMILIES,
  matchesLoraQuery,
} from '../form'
import type {
  Asset,
  ComfyTarget,
  HealthStatus,
  ImageFamily,
  Lora,
  LoraPayload,
  LoraTarget,
  ModelDownloadProgress,
  LlmCli,
  ModelFieldState,
  ModelsDirStatus,
  Options,
  Settings,
} from '../types'
import { Banner } from './ui'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { NativeSelect } from './NativeSelect'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select'
import { Separator } from './ui/separator'
import { Switch } from './ui/switch'
import { Tabs, TabsList, TabsTrigger } from './ui/tabs'

/**
 * 「（なし）」を表す番兵。Radix Select は空文字を選択肢の値にできないので、
 * 表示のあいだだけこの値に置き換える（保存する値は従来どおり null）。
 */
const NO_AUDIO = '__none__'

/** 設定の 1 ブロック（見出し + 説明 + 中身）。 */
function SettingsCard({
  title,
  description,
  children,
  className = '',
}: {
  title: string
  description?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">{children}</CardContent>
    </Card>
  )
}

/**
 * カードの中をさらに区切る小見出し付きブロック。
 *
 * 箱を入れ子にすると「線だらけ」になるので、境界は持たせず見出し + 罫線だけで
 * まとまりを示す（見出しのレベルはページの h2「設定」の直下なので h3）。
 */
function SubGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </h3>
        <Separator className="flex-1" />
      </div>
      <div className="flex flex-col gap-2">{children}</div>
    </section>
  )
}

/** ラベル + 入力 + 補足説明の縦積み。 */
function Field({
  label,
  htmlFor,
  hint,
  hintTone = 'muted',
  children,
  className = '',
}: {
  label: ReactNode
  htmlFor?: string
  hint?: ReactNode
  hintTone?: 'muted' | 'warn' | 'ok'
  children: ReactNode
  className?: string
}) {
  const tone = {
    muted: 'text-muted-foreground',
    warn: 'text-amber-400',
    ok: 'text-emerald-400',
  }[hintTone]
  return (
    <div className={className}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {/* 1 カラムのときに入力欄がカード幅いっぱい（900px 級）まで伸びると
          行が長すぎて読みにくいので、入力と補足の幅に上限を持たせる。 */}
      <div className="mt-1 max-w-xl">{children}</div>
      {hint && <p className={`mt-1 max-w-xl text-[11px] ${tone}`}>{hint}</p>}
    </div>
  )
}

/**
 * オン / オフ 1 項目（説明は左、スイッチは右端）。
 *
 * 1 項目ずつ枠で囲むとカードの中が線だらけになるので、境界は持たない行にする
 * （複数並べるときは親側の `divide-y divide-border/50` で区切る）。
 */
function ToggleRow({
  id,
  label,
  description,
  checked,
  onCheckedChange,
}: {
  id: string
  label: string
  description?: ReactNode
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  return (
    <div className="-mx-2 flex items-start justify-between gap-3 rounded-md px-2 py-2 transition-colors hover:bg-secondary/50">
      <div className="min-w-0">
        <Label htmlFor={id} className="cursor-pointer text-foreground/90">
          {label}
        </Label>
        {description && (
          <p className="mt-0.5 text-[11px] text-muted-foreground">{description}</p>
        )}
      </div>
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        className="mt-0.5 shrink-0"
      />
    </div>
  )
}

const EMPTY_LORA: LoraPayload = {
  display_name: '',
  lora_name: '',
  trigger_word: '',
  default_strength: 1,
  default_audio: null,
  sort_order: 0,
  target: 'image',
  family: DEFAULT_FAMILY,
}

const LORA_TARGET_LABELS: Record<LoraTarget, string> = {
  image: '画像用',
  video: '動画用',
}

/** 一覧バッジ: 動画はファミリー無し、画像はモデルファミリーまで出す。 */
function loraBadge(lora: Lora): string {
  const target = lora.target ?? 'image'
  if (target === 'video') return LORA_TARGET_LABELS.video
  const family = lora.family ?? DEFAULT_FAMILY
  return `画像用 / ${FAMILY_LABELS[family] ?? family}`
}

/**
 * 選べる CLI（バックエンドの `app/llm_cli.py` のアダプタと対応、SPEC §4.1）。
 *
 * `command` は既定のコマンド名で、入力欄の placeholder に出す（空欄 = 既定）。
 * Grok Imagine（画像生成）はこの選択と無関係に常に Grok CLI を使う。
 */
export const CLI_CHOICES: {
  id: LlmCli
  label: string
  command: string
  note: string
  /** モデル入力欄の placeholder（書き方に癖がある CLI だけ） */
  modelHint?: string
}[] = [
  {
    id: 'grok',
    label: 'Grok',
    command: 'grok',
    note: '契約は ACP の rules で渡ります。',
  },
  {
    id: 'claude',
    label: 'Claude Code',
    command: 'claude-agent-acp',
    note: '契約は作業ディレクトリの CLAUDE.md（プロンプトにも埋めます）。',
  },
  {
    id: 'codex',
    label: 'Codex',
    command: 'codex-acp',
    note: '契約は作業ディレクトリの AGENTS.md。ワンショットは codex exec。',
  },
  {
    id: 'cursor',
    label: 'Cursor',
    command: 'cursor-agent',
    note: '契約は作業ディレクトリの AGENTS.md。モデルは `grok-4.6[effort=xhigh,fast=false]` のように書くとワンショット・ACP の両方に効きます（素の `cursor-grok-4.6-xhigh` 形式はワンショットのみ）。',
    modelHint: 'grok-4.6[effort=xhigh,fast=false]',
  },
]

/**
 * `agent_grok_args`（grok CLI の追加フラグ）の入力欄 → 配列。
 *
 * 空白区切りの素朴な分解でよい（値は `--permission-mode auto` のようなフラグ列
 * で、空白を含む引数は想定していない）。空欄なら空配列 = ツール無効。
 */
export function splitGrokArgs(raw: string): string[] {
  return raw.split(/\s+/).filter(Boolean)
}

/** 外部 API の共有キーを 1 本作る（英数 32 文字。ブラウザ側で完結させる）。 */
function randomApiKey(length = 32): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
  const bytes = new Uint8Array(length)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join('')
}

const PUSH_LABELS: Record<PushPermission, string> = {
  granted: '許可',
  denied: '拒否',
  default: '未設定',
  unsupported: '非対応',
}

function PushSettingsCard() {
  const [permission, setPermission] = useState<PushPermission>(() =>
    currentPushPermission(),
  )
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const enable = async () => {
    setBusy(true)
    setMessage(null)
    try {
      if (currentPushPermission() === 'denied') {
        setPermission('denied')
        setMessage(
          'ブラウザのサイト設定でこのオリジンの通知を許可してから再読み込みしてください。拒否したあとは JavaScript からダイアログを出せません。',
        )
        return
      }
      const next = await ensurePushSubscription({ request: true })
      setPermission(next)
      if (next === 'granted') setMessage('この端末を購読しました')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsCard
      title="プッシュ通知"
      description="生成の完了を、この端末の通知として受け取ります。"
    >
      <p className="text-sm">
        いまの許可状態:{' '}
        <span className="font-medium">{PUSH_LABELS[permission]}</span>
      </p>
      {permission === 'denied' && (
        <p className="text-[11px] text-muted-foreground">
          ブラウザのサイト設定でこのオリジンの通知を許可してから再読み込みしてください。拒否したあとは
          JavaScript からダイアログを出せません。
        </p>
      )}
      {permission === 'unsupported' && (
        <p className="text-[11px] text-muted-foreground">
          この環境では通知を使えません（HTTPS または localhost
          と、対応ブラウザが必要です）。
        </p>
      )}
      {permission !== 'unsupported' && (
        <div>
          <Button type="button" disabled={busy} onClick={() => void enable()}>
            {permission === 'granted' ? '購読を作り直す' : '通知を許可する'}
          </Button>
        </div>
      )}
      {message && <p className="text-[11px] text-muted-foreground">{message}</p>}
    </SettingsCard>
  )
}

const TABS = [
  ['connection', '接続 / Grok'],
  ['loras', 'LoRA 管理'],
  ['models', 'モデル'],
] as const

type Tab = (typeof TABS)[number][0]

/** 「モデル」タブの大分類。 */
const MODEL_KINDS = [
  ['image', '画像'],
  ['video', '動画'],
  ['audio', '音声'],
] as const

/** `"<class_type>.<field>"` ごとの datalist の id（DOM で使える文字に落とす）。 */
function fileListId(name: string): string {
  return `model-files-${name.replace(/[^\w.-]/g, '_')}`
}

/**
 * ComfyUI から取れたモデルファイル一覧（`class_type.field` -> ファイル名）。
 *
 * LoRA だけは以前から `lora_files` で返しているので、`model_files` に無ければ
 * そちらで補う（どちらも無い = ComfyUI に繋がっていない場合は自由入力のまま）。
 * `MiniMaxH3TurboLoRA` は専用ローダーだが読むフォルダは `loras` なので同じ扱い
 * （カスタムノードを入れていない環境では `model_files` に出てこない）。
 */
const LORA_FILE_KEYS = [
  'LoraLoaderModelOnly.lora_name',
  'MiniMaxH3TurboLoRA.lora_name',
]

function modelFileMap(options: Options | null): Record<string, string[]> {
  const files: Record<string, string[]> = { ...(options?.model_files ?? {}) }
  const loraFiles = options?.lora_files ?? []
  if (loraFiles.length > 0) {
    for (const key of LORA_FILE_KEYS) {
      if (!files[key]) files[key] = loraFiles
    }
  }
  return files
}

/**
 * その値が ComfyUI に無い（= 不足している）か（SPEC §3.3）。
 *
 * 一覧そのものが取れていない（ComfyUI に繋がっていない・その class_type が
 * 入っていない）ときは判定できないので「不足していない」として扱う。
 */
function isMissing(
  row: ModelFieldState,
  files: Record<string, string[]>,
  value: string,
): boolean {
  const installed = files[`${row.class_type}.${row.field}`]
  if (!installed || installed.length === 0) return false
  return Boolean(value) && !installed.includes(value)
}

/**
 * models ディレクトリの状態を「使えるか」と表示文言にする（SPEC §3.3）。
 *
 * `configured` が false（環境変数 `COMFY_MODELS_DIR` 未設定）のときは呼び出し側が
 * ダウンロード関連の UI ごと出さないので、この文言は使われない。
 */
function dirStatusMessage(status: ModelsDirStatus | null): {
  ok: boolean
  text: string
} {
  if (!status) return { ok: false, text: '確認中…' }
  if (!status.configured) {
    return { ok: false, text: 'COMFY_MODELS_DIR が設定されていません' }
  }
  if (!status.exists) {
    return {
      ok: false,
      text: `パスが見つかりません: ${status.path}（Docker の場合は同じ絶対パスがコンテナにマウントされているか確認してください）`,
    }
  }
  if (!status.writable) {
    return { ok: false, text: `書き込み権限がありません: ${status.path}` }
  }
  return { ok: true, text: `このマシン / コンテナから書き込み可 ✓（${status.path}）` }
}

/** 進捗表示用のバイト数（GB / MB / KB）。 */
function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}

/** 折りたたみの 1 グループ = 1 ワークフロー。 */
interface ModelGroup {
  id: string
  label: string
  kind: string
  rows: ModelFieldState[]
  /** 未保存の編集がある行数（既定値・候補リストのどちらでも） */
  changed: number
  /** 既定値から変わっている行数（保存済みの上書きを含む） */
  custom: number
}

/**
 * モデル（ファミリー）の見出しと、その下にぶら下がるワークフローのグループ。
 *
 * 動画は 1 モデル（MiniMax H3）に t2v / i2v / … と複数のワークフローがあるので、
 * 「モデル名 → ワークフロー」の 2 階層で出す。ワークフローが 1 本しかない
 * ファミリー（ほとんどの画像・音声）は見出しを省いて従来どおり並べる。
 */
interface ModelFamily {
  id: string
  label: string
  kind: string
  groups: ModelGroup[]
}

/** 取得元 URL の対応表が同じ内容か（無駄な PUT を避けるため）。 */
function sameUrls(
  a: Record<string, string>,
  b: Record<string, string>,
): boolean {
  const keys = Object.keys(a)
  return keys.length === Object.keys(b).length && keys.every((k) => a[k] === b[k])
}

/** 候補リストが同じ内容か（順序も見る）。 */
function sameChoices(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((name, index) => name === b[index])
}

/**
 * ワークフローのグループ見出し（モデル名の見出しの下に出す分）。
 *
 * 親にモデル名が出ているので、ラベルからその重複を落とす
 * （「画像→動画・音声つき (MiniMax H3 i2v)」-> 「画像→動画・音声つき (i2v)」）。
 * 落とすと空になるラベル（ワークフローが 1 本だけのファミリー）はそのまま。
 */
function workflowLabel(row: ModelFieldState, familyLabel: string): string {
  const label = row.workflow_label || row.workflow_id || ''
  // family_label は「Grok Imagine（サブスク CLI）」のように注記つきのことが
  // あるので、注記を外した素の名前でも試す。
  const names = [familyLabel, familyLabel.split('（')[0]].filter(Boolean)
  for (const name of names) {
    if (!label.includes(name)) continue
    const stripped = label
      .split(name)
      .join('')
      .replace(/[(（]\s+/g, '(')
      .replace(/\s+[)）]/g, ')')
      .replace(/\s{2,}/g, ' ')
      .trim()
    if (stripped) return stripped
  }
  return label
}

/**
 * モデル（ファミリー）→ ワークフローの 2 階層にまとめる
 * （表示順はどちらも API の並び = workflows.SPECS の順）。
 *
 * グループ自体は従来どおり 1 ワークフロー = 1 折りたたみで、動画のように
 * 1 モデルへ複数のワークフローがぶら下がるときだけモデル名の見出しが挟まる。
 * 折りたたみが既定なので、中身が既定値から変わっていることをバッジで見せる。
 */
function groupModels(
  rows: ModelFieldState[],
  draft: Record<string, string>,
  choices: Record<string, string[]>,
): ModelFamily[] {
  const families = new Map<string, ModelFamily>()
  const groups = new Map<string, ModelGroup>()
  for (const row of rows) {
    const familyId = row.family || `workflow:${row.workflow_id || '(unknown)'}`
    let family = families.get(familyId)
    if (!family) {
      family = {
        id: familyId,
        label: row.family_label || row.family || row.workflow_label || familyId,
        kind: row.kind ?? 'image',
        groups: [],
      }
      families.set(familyId, family)
    }
    const id = row.workflow_id || '(unknown)'
    let group = groups.get(id)
    if (!group) {
      group = {
        id,
        label: workflowLabel(row, family.label),
        kind: row.kind ?? 'image',
        rows: [],
        changed: 0,
        custom: 0,
      }
      groups.set(id, group)
      family.groups.push(group)
    }
    group.rows.push(row)
    const value = draft[row.key] ?? ''
    if (
      value !== row.value ||
      !sameChoices(choices[row.key] ?? [], row.choices ?? [])
    ) {
      group.changed += 1
    }
    if (value !== row.default) group.custom += 1
  }
  // ワークフローが 1 本だけのファミリーはモデル名の見出しを出さないので、
  // 見出しに逃がした分（「Krea 2 turbo」の「Krea 2」）を書き戻す。
  for (const family of families.values()) {
    if (family.groups.length !== 1) continue
    const [group] = family.groups
    group.label = group.rows[0].workflow_label || group.id
  }
  return [...families.values()]
}

export default function SettingsPage({
  options,
  onBack,
  onChanged,
}: {
  options: Options | null
  onBack: () => void
  onChanged: () => void
}) {
  const [tab, setTab] = useState<Tab>('connection')
  const [settings, setSettings] = useState<Settings | null>(null)
  /**
   * `agent_grok_args` の編集中の文字列（実体は list なので空白区切りで持つ）。
   * 打っている途中の空白を保つため、表示はこちら・保存は分解した配列を使う。
   */
  const [grokArgsDraft, setGrokArgsDraft] = useState('')
  // モデル / LoRA タブの編集対象の環境（SPEC §5）。初期値は現在の接続先だが、
  // 繋いでいない環境の登録も整理できるよう独立して切り替えられる。
  const [envTarget, setEnvTarget] = useState<ComfyTarget | null>(null)
  const [loras, setLoras] = useState<Lora[]>([])
  const [loraQuery, setLoraQuery] = useState('')
  const [loraTargetFilter, setLoraTargetFilter] = useState<'all' | LoraTarget>('all')
  const [loraFamilyFilter, setLoraFamilyFilter] = useState('all')
  const [expandedLoraId, setExpandedLoraId] = useState<number | null>(null)
  const [models, setModels] = useState<ModelFieldState[]>([])
  const [modelDraft, setModelDraft] = useState<Record<string, string>>({})
  // スロットごとの候補リスト（編集中）と「候補に追加」入力欄の内容
  const [choiceDraft, setChoiceDraft] = useState<Record<string, string[]>>({})
  const [choiceInput, setChoiceInput] = useState<Record<string, string>>({})
  // ワークフローごとの折りたたみ状態（既定は閉じている）
  const [openWorkflows, setOpenWorkflows] = useState<Record<string, boolean>>({})
  // 不足モデルのダウンロード（SPEC §3.3）: models ディレクトリの状態、
  // ファイル名ごとの URL（行を跨いで共有）、ファイル名ごとの進捗
  const [dirStatus, setDirStatus] = useState<ModelsDirStatus | null>(null)
  const [urlDraft, setUrlDraft] = useState<Record<string, string>>({})
  // 検出済みの行で URL 入力欄を開いているファイル名（既定は閉じている）
  const [urlOpen, setUrlOpen] = useState<Record<string, boolean>>({})
  const [downloads, setDownloads] = useState<
    Record<string, ModelDownloadProgress>
  >({})
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  /** Grok Build CLI の疎通確認の結果（SPEC §5.2）。null = まだ押していない。 */
  const [grokCheck, setGrokCheck] = useState<HealthStatus | null>(null)
  const [grokChecking, setGrokChecking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<LoraPayload>(EMPTY_LORA)
  const [editingId, setEditingId] = useState<number | null>(null)
  // LoRA フォームの取得元 URL（LoRA 本体と同時に model_download_urls へ保存する）と、
  // 編集を始めたときのファイル名（ファイル名を変えたら旧キーを消すため）
  const [draftUrl, setDraftUrl] = useState('')
  const [editingLoraName, setEditingLoraName] = useState('')

  const loraFiles: string[] = options?.lora_files ?? []
  const modelFiles = modelFileMap(options)
  const audioAssets: Asset[] = options?.audio_assets ?? []
  const filteredLoras = loras.filter((lora) => {
    const target = lora.target ?? 'image'
    const family = lora.family ?? DEFAULT_FAMILY
    if (loraTargetFilter !== 'all' && target !== loraTargetFilter) return false
    // 動画用 LoRA にファミリーの区別はない。対象＝動画用のときに
    // ファミリーで絞ると必ず 0 件になってしまうので、ファミリー条件は無視する。
    if (
      loraTargetFilter !== 'video' &&
      loraFamilyFilter !== 'all' &&
      (target !== 'image' || family !== loraFamilyFilter)
    ) {
      return false
    }
    return matchesLoraQuery(lora, loraQuery)
  })

  const fail = (caught: unknown) =>
    setError(caught instanceof Error ? caught.message : String(caught))

  const reloadLoras = async (target = envTarget) => {
    try {
      setLoras(await api.listLoras(target ?? undefined))
    } catch (caught) {
      fail(caught)
    }
  }

  const applyModels = (rows: ModelFieldState[]) => {
    setModels(rows)
    setModelDraft(Object.fromEntries(rows.map((row) => [row.key, row.value])))
    setChoiceDraft(
      Object.fromEntries(rows.map((row) => [row.key, [...(row.choices ?? [])]])),
    )
    setChoiceInput({})
  }

  /** 候補リストにファイル名を足す（重複・空欄は無視）。 */
  const addChoice = (key: string) => {
    const name = (choiceInput[key] ?? '').trim()
    if (!name) return
    setChoiceDraft((previous) => {
      const current = previous[key] ?? []
      if (current.includes(name)) return previous
      return { ...previous, [key]: [...current, name] }
    })
    setChoiceInput((previous) => ({ ...previous, [key]: '' }))
  }

  const removeChoice = (key: string, name: string) =>
    setChoiceDraft((previous) => ({
      ...previous,
      [key]: (previous[key] ?? []).filter((item) => item !== name),
    }))

  const reloadDirStatus = async () => {
    try {
      setDirStatus(await api.modelsDirStatus())
    } catch (caught) {
      fail(caught)
    }
  }

  /** モデル一覧・LoRA 一覧・進行中のダウンロードを、その環境のものに揃える。 */
  const loadForTarget = async (target: ComfyTarget) => {
    try {
      applyModels(await api.listModels(target))
    } catch (caught) {
      fail(caught)
    }
    try {
      // 開き直したときに進行中のダウンロードを拾い直す（WS の取りこぼし対策。
      // RunPod では Pod 側で走っているものもここで入る）
      const running = await api.listModelDownloads(target)
      setDownloads(
        Object.fromEntries(running.map((item) => [item.filename, item])),
      )
    } catch (caught) {
      fail(caught)
    }
    await reloadLoras(target)
  }

  useEffect(() => {
    void (async () => {
      let target: ComfyTarget = 'local'
      try {
        const loaded = await api.getSettings()
        setSettings(loaded)
        setGrokArgsDraft(loaded.agent_grok_args.join(' '))
        setUrlDraft({ ...loaded.model_download_urls })
        target = loaded.comfy_target
      } catch (caught) {
        fail(caught)
      }
      setEnvTarget(target)
      await reloadDirStatus()
      await loadForTarget(target)
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** モデル / LoRA の編集対象の環境を切り替える（未保存の編集は捨てる）。 */
  const changeEnvTarget = async (target: ComfyTarget) => {
    setEnvTarget(target)
    resetLoraForm()
    setBusy(true)
    try {
      await loadForTarget(target)
    } finally {
      setBusy(false)
    }
  }

  // ダウンロードの進捗（WS /api/ws の `model_download`、SPEC §3.3）。
  // 完了したら ComfyUI のファイル一覧を取り直して「未検出」バッジを消す。
  useEffect(() => {
    const socket = new WebSocket(wsUrl())
    socket.onmessage = (event) => {
      try {
        const frame = JSON.parse(event.data as string) as ModelDownloadProgress
        if (frame?.type !== 'model_download') return
        setDownloads((previous) => ({ ...previous, [frame.filename]: frame }))
        if (frame.status === 'done') onChanged()
      } catch {
        /* 壊れたフレームは無視する */
      }
    }
    return () => socket.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** grok CLI を 1 ターン回して確かめる（生成はしないので枠の消費は最小）。 */
  const checkGrok = async () => {
    setGrokChecking(true)
    setGrokCheck(null)
    try {
      setGrokCheck(await api.checkGrok())
    } catch (caught) {
      setGrokCheck({
        status: 'error',
        detail: caught instanceof Error ? caught.message : String(caught),
      })
    } finally {
      setGrokChecking(false)
    }
  }

  const saveSettings = async () => {
    if (!settings) return
    setBusy(true)
    setError(null)
    try {
      setSettings(
        await api.putSettings({
          comfy_target: settings.comfy_target,
          local_comfy_url: settings.local_comfy_url,
          runpod_comfy_url: settings.runpod_comfy_url,
          runpod_comfy_api_key: settings.runpod_comfy_api_key,
          comfy_cloud_api_key: settings.comfy_cloud_api_key,
          agent_cli: settings.agent_cli,
          agent_cli_commands: settings.agent_cli_commands,
          agent_cli_models: settings.agent_cli_models,
          grok_model: settings.grok_model,
          grok_command: settings.grok_command,
          grok_workdir: settings.grok_workdir,
          grok_media_workdir: settings.grok_media_workdir,
          grok_media_timeout: settings.grok_media_timeout,
          // 空白区切りの入力欄をフラグの配列に戻す（空 = ツール無効）
          agent_grok_args: splitGrokArgs(grokArgsDraft),
          agent_use_acp: settings.agent_use_acp,
          hf_token: settings.hf_token,
          civitai_api_key: settings.civitai_api_key,
          runpod_enabled: settings.runpod_enabled,
          runpod_api_key: settings.runpod_api_key,
          runpod_template_id: settings.runpod_template_id,
          runpod_gpu_type: settings.runpod_gpu_type,
          runpod_network_volume_id: settings.runpod_network_volume_id,
          external_api_key: settings.external_api_key,
          external_max_pending_takes: settings.external_max_pending_takes,
          agent_grok_timeout: settings.agent_grok_timeout,
        }),
      )
      setNotice('設定を保存しました')
      // models ディレクトリを変えたかもしれないので状態を取り直す
      await reloadDirStatus()
      onChanged()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  /**
   * 1 ファイルのダウンロードを開始する（SPEC §3.3）。
   *
   * URL は次回のために設定 (`model_download_urls`) へ保存してから投げる。
   */
  const startDownload = async (row: ModelFieldState, filename: string) => {
    const url = (urlDraft[filename] ?? '').trim()
    if (!url) return
    setBusy(true)
    setError(null)
    try {
      const saved = await api.putSettings({
        model_download_urls: { ...urlDraft, [filename]: url },
      })
      setSettings(saved)
      const started = await api.downloadModel(
        filename,
        url,
        row.subfolder,
        envTarget ?? undefined,
      )
      setDownloads((previous) => ({ ...previous, [filename]: started }))
      setNotice(`${filename} のダウンロードを開始しました`)
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  /**
   * 取得元 URL だけを設定へ保存する（ダウンロードはしない、SPEC §3.3）。
   *
   * 手元に在るモデルでも、あとで別の環境（RunPod の Pod など）へ [DL] / [全DL]
   * で入れるときに要るので、事前に登録できるようにしてある。空欄で保存したら
   * キーごと消す。
   */
  const saveDownloadUrl = async (filename: string) => {
    if (!filename) return
    const url = (urlDraft[filename] ?? '').trim()
    const next = { ...(settings?.model_download_urls ?? {}) }
    if (url) next[filename] = url
    else delete next[filename]
    setBusy(true)
    setError(null)
    try {
      const saved = await api.putSettings({ model_download_urls: next })
      setSettings(saved)
      // 他の行の編集中の下書きは消さず、このファイルの分だけ揃える
      setUrlDraft((previous) => ({ ...previous, [filename]: url }))
      setNotice(
        url
          ? `${filename} の取得元 URL を保存しました`
          : `${filename} の取得元 URL を解除しました`,
      )
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  /**
   * 未検出かつ取得元 URL 登録済みのモデルをまとめて落とす（SPEC §3.3）。
   *
   * 何が足りないかは**サーバーが選んだ環境の ComfyUI に聞いて**決めるので、
   * その ComfyUI（RunPod なら Pod）が起動している必要がある。
   */
  const startAllDownloads = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.downloadAllModels(envTarget ?? undefined)
      setDownloads((previous) => ({
        ...previous,
        ...Object.fromEntries(result.started.map((item) => [item.filename, item])),
      }))
      const notes = [`${result.started.length} 件のダウンロードを開始しました`]
      if (result.missing_urls.length > 0) {
        notes.push(
          `取得元 URL が未登録: ${result.missing_urls.join(', ')}`,
        )
      }
      const failures = Object.entries(result.errors)
      if (failures.length > 0) {
        notes.push(
          ...failures.map(([name, message]) => `${name}: ${message}`),
        )
      }
      setNotice(notes.join(' / '))
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const saveModels = async () => {
    setBusy(true)
    setError(null)
    try {
      applyModels(
        await api.putModels(modelDraft, choiceDraft, envTarget ?? undefined),
      )
      setNotice('モデル名と候補リストを保存しました')
      onChanged()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  /** LoRA フォームを空に戻す（追加モード）。 */
  const resetLoraForm = () => {
    setDraft(EMPTY_LORA)
    setEditingId(null)
    setDraftUrl('')
    setEditingLoraName('')
  }

  const submitLora = async () => {
    setBusy(true)
    setError(null)
    try {
      // 新規登録は編集中の環境に紐づける（既存行の環境は変えない）
      if (editingId == null) {
        await api.createLora({ ...draft, comfy_target: envTarget })
      } else {
        await api.updateLora(editingId, draft)
      }
      // 取得元 URL は LoRA 本体と同時に保存する（キーはファイル名）。ファイル名を
      // 変えた場合は旧キーを消してから新しいキーに移す。
      const url = draftUrl.trim()
      const current = settings?.model_download_urls ?? {}
      const next = { ...current }
      if (editingLoraName && editingLoraName !== draft.lora_name) {
        delete next[editingLoraName]
      }
      if (url) next[draft.lora_name] = url
      else delete next[draft.lora_name]
      if (!sameUrls(next, current)) {
        const saved = await api.putSettings({ model_download_urls: next })
        setSettings(saved)
        // モデルタブの編集中の下書きは消さず、触ったキーだけ揃える
        setUrlDraft((previous) => ({
          ...previous,
          [draft.lora_name]: url,
          ...(editingLoraName && editingLoraName !== draft.lora_name
            ? { [editingLoraName]: '' }
            : {}),
        }))
      }
      resetLoraForm()
      await reloadLoras()
      onChanged()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const uploadSample = async (lora: Lora, file: File) => {
    setBusy(true)
    setError(null)
    try {
      await api.uploadLoraSample(lora.id, file)
      await reloadLoras()
      onChanged()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const removeSample = async (lora: Lora, url: string) => {
    const name = url.split('/').pop() ?? ''
    setBusy(true)
    setError(null)
    try {
      await api.deleteLoraSample(lora.id, name)
      await reloadLoras()
      onChanged()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const removeLora = async (lora: Lora) => {
    if (!window.confirm(`${lora.display_name} を削除しますか？`)) return
    setBusy(true)
    try {
      await api.deleteLora(lora.id)
      await reloadLoras()
      onChanged()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const update = (patch: Partial<Settings>) =>
    setSettings((previous) => (previous ? { ...previous, ...patch } : previous))

  // ------------------------------------------------ 選択中の CLI（SPEC §4.1）
  // コマンドとモデルの欄は選択中の CLI のものを出す。grok だけは従来からある
  // `grok_command` / `grok_model` を、ほかは `agent_cli_*` の辞書を書き換える。
  const activeCli: LlmCli = settings?.agent_cli ?? 'grok'
  const cliChoice = CLI_CHOICES.find((choice) => choice.id === activeCli) ?? CLI_CHOICES[0]
  const cliCommand =
    activeCli === 'grok'
      ? (settings?.grok_command ?? '')
      : (settings?.agent_cli_commands?.[activeCli] ?? '')
  const cliModel =
    activeCli === 'grok'
      ? (settings?.grok_model ?? '')
      : (settings?.agent_cli_models?.[activeCli] ?? '')

  const setCliCommand = (value: string) =>
    update(
      activeCli === 'grok'
        ? { grok_command: value }
        : {
            agent_cli_commands: {
              ...(settings?.agent_cli_commands ?? {}),
              [activeCli]: value,
            },
          },
    )

  const setCliModel = (value: string) =>
    update(
      activeCli === 'grok'
        ? { grok_model: value }
        : {
            agent_cli_models: {
              ...(settings?.agent_cli_models ?? {}),
              [activeCli]: value,
            },
          },
    )

  /** モデル / LoRA タブの先頭に置く環境プルダウン（SPEC §5）。 */
  const envPicker = () => (
    <Card className="flex flex-wrap items-center gap-2 p-2">
      <Label htmlFor="settings-env" className="text-muted-foreground">
        対象の接続先
      </Label>
      <Select
        value={envTarget ?? 'local'}
        disabled={envTarget == null || busy}
        onValueChange={(value) => void changeEnvTarget(value as ComfyTarget)}
      >
        <SelectTrigger id="settings-env" className="h-8 w-[16rem] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {COMFY_TARGETS.map((target) => (
            <SelectItem key={target} value={target}>
              {COMFY_TARGET_LABELS[target]}
              {target === settings?.comfy_target ? '（現在の接続先）' : ''}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Card>
  )

  // いま繋いでいる環境を編集しているか（未検出バッジの判定に使う）
  const connectedEnv = envTarget != null && envTarget === settings?.comfy_target

  const modelsDirty = models.some(
    (row) =>
      (modelDraft[row.key] ?? '') !== row.value ||
      !sameChoices(choiceDraft[row.key] ?? [], row.choices ?? []),
  )
  // 保存は全件置換 PUT なので、折りたたんでいても modelDraft は全行を持ち続ける。
  const modelFamilies = groupModels(models, modelDraft, choiceDraft)
  // ダウンロード UI を出すか（SPEC §3.3）。ローカル / RunPod では常に出し、
  // 落とせない事情（COMFY_MODELS_DIR 未設定・Pod 停止中）は押したときに
  // エラーで知らせる。ComfyCloud だけはファイルを置けないので出さない。
  const showDownload = envTarget !== 'comfy_cloud'

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <div className="flex items-center gap-3 border-b border-border bg-card/70 px-4 py-2.5 shadow-elevation-1">
        <Button variant="outline" size="sm" onClick={onBack}>
          <ArrowLeft />
          戻る
        </Button>
        <h2 className="text-sm font-semibold text-foreground">設定</h2>
        <Tabs
          className="ml-2"
          value={tab}
          onValueChange={(value) => setTab(value as Tab)}
        >
          <TabsList>
            {TABS.map(([key, label]) => (
              <TabsTrigger key={key} value={key}>
                {label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-5xl flex-col gap-3">
          {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
          {notice && (
            <Banner tone="info" onClose={() => setNotice(null)}>
              {notice}
            </Banner>
          )}

          {tab === 'connection' && (
            <div className="flex flex-col gap-3">
              <PushSettingsCard />
              {!settings && (
                <p className="text-xs text-muted-foreground">読み込み中…</p>
              )}
              {settings && (
                <>
                  {/* ComfyUI の接続先（SPEC §5）。3 プロファイル分の接続情報を
                      ここに置き、「接続先」がそのどれを使うかを決める。生成
                      フォーム上部のプルダウンは同じ値を書き換える。 */}
                  <SettingsCard
                    title="ComfyUI 接続先"
                    description="3 つのプロファイルの接続情報をまとめて持ち、「接続先」で実際に使うものを選びます。"
                  >
                    <Field label="接続先" htmlFor="settings-comfy-target">
                      <Select
                        value={settings.comfy_target}
                        onValueChange={(value) =>
                          update({ comfy_target: value as ComfyTarget })
                        }
                      >
                        <SelectTrigger id="settings-comfy-target">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {COMFY_TARGETS.map((target) => (
                            <SelectItem key={target} value={target}>
                              {COMFY_TARGET_LABELS[target]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </Field>

                    <SubGroup title="ComfyCloud">
                      <Field label="ComfyCloud APIキー" htmlFor="comfy-cloud-api-key">
                        <Input
                          id="comfy-cloud-api-key"
                          type="password"
                          autoComplete="off"
                          value={settings.comfy_cloud_api_key}
                          onChange={(event) =>
                            update({ comfy_cloud_api_key: event.target.value })
                          }
                        />
                      </Field>
                    </SubGroup>

                    {/* ComfyUI を RunPod の Pod で動かす構成（SPEC §5.1）。
                        自動起動を有効にすると、接続先が RunPod のときだけ、
                        ジョブ投入の直前に疎通を確かめて落ちていれば Pod を作って
                        待つ。Pod の停止はイメージ側の watchdog が行う。 */}
                    <SubGroup title="RunPod">
                      <Field label="RunPod ComfyUI URL" htmlFor="runpod-comfy-url">
                        <Input
                          id="runpod-comfy-url"
                          placeholder="https://<Cloudflare Tunnel のホスト名>"
                          value={settings.runpod_comfy_url}
                          onChange={(event) =>
                            update({ runpod_comfy_url: event.target.value })
                          }
                        />
                      </Field>
                      <Field
                        label="RunPod ComfyUI APIキー（任意）"
                        htmlFor="runpod-comfy-api-key"
                      >
                        <Input
                          id="runpod-comfy-api-key"
                          type="password"
                          autoComplete="off"
                          value={settings.runpod_comfy_api_key}
                          onChange={(event) =>
                            update({ runpod_comfy_api_key: event.target.value })
                          }
                        />
                      </Field>
                      <ToggleRow
                        id="runpod-enabled"
                        label="RunPod の Pod を自動起動する"
                        description="接続先が RunPod のとき、ジョブ投入の直前に Pod が落ちていれば起動して待ちます。"
                        checked={settings.runpod_enabled}
                        onCheckedChange={(checked) =>
                          update({ runpod_enabled: checked })
                        }
                      />
                      {settings.runpod_enabled && (
                        <>
                          <Field label="RunPod APIキー" htmlFor="runpod-api-key">
                            <Input
                              id="runpod-api-key"
                              type="password"
                              autoComplete="off"
                              value={settings.runpod_api_key}
                              onChange={(event) =>
                                update({ runpod_api_key: event.target.value })
                              }
                            />
                          </Field>
                          <div className="grid grid-cols-2 gap-2">
                            <Field label="テンプレート ID" htmlFor="runpod-template-id">
                              <Input
                                id="runpod-template-id"
                                value={settings.runpod_template_id}
                                onChange={(event) =>
                                  update({ runpod_template_id: event.target.value })
                                }
                              />
                            </Field>
                            <Field label="GPU 種別（gpuTypeId）" htmlFor="runpod-gpu-type">
                              <Input
                                id="runpod-gpu-type"
                                value={settings.runpod_gpu_type}
                                onChange={(event) =>
                                  update({ runpod_gpu_type: event.target.value })
                                }
                              />
                            </Field>
                          </div>
                          <Field
                            label="Network Volume ID（任意）"
                            htmlFor="runpod-network-volume-id"
                          >
                            <Input
                              id="runpod-network-volume-id"
                              value={settings.runpod_network_volume_id}
                              onChange={(event) =>
                                update({
                                  runpod_network_volume_id: event.target.value,
                                })
                              }
                            />
                          </Field>
                        </>
                      )}
                    </SubGroup>

                    <SubGroup title="ローカル">
                      <Field label="ローカル ComfyUI URL" htmlFor="local-comfy-url">
                        <Input
                          id="local-comfy-url"
                          placeholder="http://127.0.0.1:8188"
                          value={settings.local_comfy_url}
                          onChange={(event) =>
                            update({ local_comfy_url: event.target.value })
                          }
                        />
                      </Field>
                    </SubGroup>
                  </SettingsCard>
                  {/* 不足モデルの自動ダウンロード（SPEC §3.3）。トークンは
                      ローカルにも RunPod の Pod にも要るので常に出す。保存先の
                      環境変数はローカルに落とすときだけ関係する。 */}
                  <SettingsCard
                    title="モデル自動ダウンロード"
                    description="不足しているモデルを取得元 URL から落とすための共通設定です。"
                  >
                    <Field
                      label="ローカルの保存先（環境変数 COMFY_MODELS_DIR）"
                      htmlFor="models-dir"
                      hint={dirStatusMessage(dirStatus).text}
                      hintTone={dirStatusMessage(dirStatus).ok ? 'ok' : 'warn'}
                    >
                      <Input id="models-dir" value={dirStatus?.path ?? ''} readOnly />
                    </Field>
                    <div className="grid grid-cols-2 gap-2">
                      <Field
                        label="Hugging Face トークン（gated モデル用・任意）"
                        htmlFor="hf-token"
                      >
                        <Input
                          id="hf-token"
                          type="password"
                          autoComplete="off"
                          value={settings.hf_token}
                          onChange={(event) => update({ hf_token: event.target.value })}
                        />
                      </Field>
                      <Field label="Civitai APIキー（任意）" htmlFor="civitai-api-key">
                        <Input
                          id="civitai-api-key"
                          type="password"
                          autoComplete="off"
                          value={settings.civitai_api_key}
                          onChange={(event) =>
                            update({ civitai_api_key: event.target.value })
                          }
                        />
                      </Field>
                    </div>
                  </SettingsCard>

                  <SettingsCard
                    title="LLM CLI"
                    description="チャット・スタジオ会話・英訳が回す CLI の設定です。Grok Imagine（画像生成）は常に Grok CLI を使います。"
                  >
                    <div className="grid gap-2 sm:grid-cols-2">
                      <Field
                        label="使う CLI"
                        htmlFor="agent-cli"
                        hint={cliChoice.note}
                      >
                        <NativeSelect
                          id="agent-cli"
                          value={settings.agent_cli}
                          onChange={(event) =>
                            update({ agent_cli: event.target.value as LlmCli })
                          }
                        >
                          {CLI_CHOICES.map((choice) => (
                            <option key={choice.id} value={choice.id}>
                              {choice.label}
                            </option>
                          ))}
                        </NativeSelect>
                      </Field>
                      <Field
                        label={`${cliChoice.label} のコマンド（空 = 既定）`}
                        htmlFor="cli-command"
                        hint="引数まで書けます（例: npx @zed-industries/claude-code-acp）。"
                      >
                        <Input
                          id="cli-command"
                          value={cliCommand}
                          placeholder={cliChoice.command}
                          onChange={(event) => setCliCommand(event.target.value)}
                        />
                      </Field>
                      <Field
                        label={`${cliChoice.label} のモデル（空 = CLI の既定）`}
                        htmlFor="cli-model"
                      >
                        <Input
                          id="cli-model"
                          value={cliModel}
                          placeholder={cliChoice.modelHint}
                          onChange={(event) => setCliModel(event.target.value)}
                        />
                      </Field>
                      <Field
                        label="Grok Imagine の制限時間（秒）"
                        htmlFor="grok-media-timeout"
                      >
                        <Input
                          id="grok-media-timeout"
                          className="tnum"
                          type="number"
                          min="30"
                          step="30"
                          value={settings.grok_media_timeout}
                          onChange={(event) =>
                            update({
                              grok_media_timeout: Number(event.target.value) || 0,
                            })
                          }
                        />
                      </Field>
                      <Field
                        label="CLI の作業ディレクトリ（空 = 既定）"
                        htmlFor="grok-workdir"
                        hint="チャット・英訳が CLI を回すディレクトリの根です（セッションごとに下へ掘ります）。"
                      >
                        <Input
                          id="grok-workdir"
                          value={settings.grok_workdir}
                          placeholder="/path/to/workdir"
                          onChange={(event) =>
                            update({ grok_workdir: event.target.value })
                          }
                        />
                      </Field>
                      <Field
                        label="Grok Imagine の作業ディレクトリ（空 = 既定）"
                        htmlFor="grok-media-workdir"
                        hint="画像生成・編集（SPEC §5.2）で使う置き場所です。"
                      >
                        <Input
                          id="grok-media-workdir"
                          value={settings.grok_media_workdir}
                          placeholder="/path/to/workdir"
                          onChange={(event) =>
                            update({ grok_media_workdir: event.target.value })
                          }
                        />
                      </Field>
                      <Field
                        label="CLI の追加フラグ（空白区切り）"
                        htmlFor="agent-grok-args"
                        hintTone="warn"
                        hint={
                          <>
                            <strong>空にすると CLI のツールが無効になります</strong>
                            （ファイルの読み書き・画像の確認・Web 検索ができなくなり、
                            システムプロンプトからもツールの節が落ちます）。
                          </>
                        }
                      >
                        <Input
                          id="agent-grok-args"
                          value={grokArgsDraft}
                          placeholder="--permission-mode auto"
                          onChange={(event) => setGrokArgsDraft(event.target.value)}
                        />
                      </Field>
                    </div>
                    <ToggleRow
                      id="agent-use-acp"
                      label="ACP でターンを回す"
                      description="オンだと実行中の活動（思考 / ツール実行）がチャットに出ます。オフは従来のワンショット実行で、活動表示はありません。"
                      checked={settings.agent_use_acp}
                      onCheckedChange={(checked) => update({ agent_use_acp: checked })}
                    />
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void checkGrok()}
                        disabled={grokChecking}
                      >
                        {grokChecking ? '確認中…' : 'Grok CLI の接続確認（Grok Imagine）'}
                      </Button>
                      {grokCheck && (
                        <span
                          className={`text-xs ${
                            grokCheck.status === 'ok'
                              ? 'text-emerald-400'
                              : 'text-amber-400'
                          }`}
                        >
                          {grokCheck.detail || grokCheck.status}
                        </span>
                      )}
                    </div>
                  </SettingsCard>
                  {/* LLM CLI の実行上限。0 を入れたときだけ無制限になる。 */}
                  <SettingsCard
                    title="実行上限（0 = 無制限）"
                    description="暴走防止の上限です。0 を入れるとその項目だけ無制限になります（止めたいときは各画面の「停止」で止めてください）。"
                  >
                    <div className="grid gap-2 sm:grid-cols-2">
                      <Field
                        label="grok の制限時間（秒・0 = タイムアウトなし）"
                        htmlFor="agent-grok-timeout"
                      >
                        <Input
                          id="agent-grok-timeout"
                          className="tnum"
                          type="number"
                          min="0"
                          step="30"
                          value={settings.agent_grok_timeout}
                          onChange={(event) =>
                            update({
                              agent_grok_timeout: Number(event.target.value) || 0,
                            })
                          }
                        />
                      </Field>
                    </div>
                  </SettingsCard>
                  {/* 外部公開 API（docs/EXTERNAL-API.md）。キーを入れることが
                      有効化そのもので、空のあいだは /api/v1 が丸ごと 404。 */}
                  <SettingsCard
                    title="外部 API（/api/v1）"
                    description="キーを入れることが有効化そのものです。空のあいだは /api/v1 が丸ごと 404 になります。"
                  >
                    <div className="grid gap-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
                      <Field
                        label="API キー（空 = 外部 API は無効）"
                        htmlFor="external-api-key"
                      >
                        <div className="flex gap-2">
                          <Input
                            id="external-api-key"
                            className="min-w-0 flex-1"
                            type="password"
                            autoComplete="off"
                            value={settings.external_api_key}
                            onChange={(event) =>
                              update({ external_api_key: event.target.value })
                            }
                          />
                          <Button
                            variant="outline"
                            className="shrink-0"
                            onClick={() => update({ external_api_key: randomApiKey() })}
                          >
                            生成
                          </Button>
                        </div>
                      </Field>
                      <Field
                        label="未完了 Take の上限（0 = 無制限）"
                        htmlFor="external-max-pending"
                      >
                        <Input
                          id="external-max-pending"
                          className="tnum"
                          type="number"
                          min="0"
                          step="1"
                          value={settings.external_max_pending_takes}
                          onChange={(event) =>
                            update({
                              external_max_pending_takes:
                                Number(event.target.value) || 0,
                            })
                          }
                        />
                      </Field>
                    </div>
                  </SettingsCard>
                  <Button
                    className="self-start"
                    onClick={() => void saveSettings()}
                    disabled={busy}
                  >
                    保存
                  </Button>
                </>
              )}
            </div>
          )}

          {tab === 'loras' && (
            <div className="flex flex-col gap-4">
              {envPicker()}
              <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(22rem,0.8fr)]">
                <div className="flex min-w-0 flex-col gap-2">
                  <Card className="p-3">
                    <div className="flex flex-col gap-2 sm:flex-row">
                      <div className="min-w-0 flex-1">
                        <Label className="sr-only" htmlFor="lora-management-search">
                          LoRAを検索
                        </Label>
                        <Input
                          id="lora-management-search"
                          placeholder="名前・ファイル名・トリガーで検索"
                          value={loraQuery}
                          onChange={(event) => setLoraQuery(event.target.value)}
                        />
                      </div>
                      <Select
                        value={loraTargetFilter}
                        onValueChange={(value) =>
                          setLoraTargetFilter(value as 'all' | LoraTarget)
                        }
                      >
                        <SelectTrigger aria-label="LoRAの対象" className="sm:w-32">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">すべて</SelectItem>
                          <SelectItem value="image">画像用</SelectItem>
                          <SelectItem value="video">動画用</SelectItem>
                        </SelectContent>
                      </Select>
                      <Select
                        value={loraFamilyFilter}
                        disabled={loraTargetFilter === 'video'}
                        onValueChange={setLoraFamilyFilter}
                      >
                        <SelectTrigger aria-label="LoRAのファミリー" className="sm:w-36">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">全ファミリー</SelectItem>
                          {IMAGE_FAMILIES.map((value) => (
                            <SelectItem key={value} value={value}>
                              {FAMILY_LABELS[value] ?? value}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                      <span className="tnum">
                        表示 {filteredLoras.length} / 全 {loras.length}
                      </span>
                      <Button variant="outline" size="xs" onClick={resetLoraForm}>
                        <Plus />
                        新規登録
                      </Button>
                    </div>
                  </Card>

                  <Card
                    data-testid="lora-management-list"
                    className="max-h-[32rem] divide-y divide-border overflow-y-auto overscroll-contain"
                  >
                    {loras.length === 0 && (
                      <p className="p-4 text-xs text-muted-foreground">登録がありません</p>
                    )}
                    {loras.length > 0 && filteredLoras.length === 0 && (
                      <p className="p-4 text-center text-xs text-muted-foreground">
                        条件に一致するLoRAがありません
                      </p>
                    )}
                    {filteredLoras.map((lora) => {
                      // 取得元 URL はモデルタブと同じ `model_download_urls`（キーは
                      // ファイル名 = lora_name）に入れる。[DL] / [全DL] がこれを見る。
                      const savedUrl =
                        settings?.model_download_urls?.[lora.lora_name] ?? ''
                      const expanded = expandedLoraId === lora.id
                      const sample = lora.sample_images[0]
                      return (
                        <div
                          key={lora.id}
                          className={editingId === lora.id ? 'bg-primary/5' : ''}
                        >
                          <div className="flex items-center gap-2 p-2 text-xs">
                            {sample ? (
                              <img
                                src={sample}
                                alt=""
                                aria-hidden="true"
                                loading="lazy"
                                className="size-10 shrink-0 rounded-md border border-border object-cover"
                              />
                            ) : (
                              <span
                                aria-hidden="true"
                                className="flex size-10 shrink-0 items-center justify-center rounded-md border border-border bg-surface-sunken text-muted-foreground"
                              >
                                <ImageIcon className="size-4" />
                              </span>
                            )}
                            <div className="min-w-0 flex-1">
                              <p className="truncate font-medium text-foreground">
                                {lora.display_name}
                              </p>
                              <p className="flex items-center gap-1.5 truncate text-muted-foreground">
                                <Badge
                                  variant="outline"
                                  className="shrink-0 px-1.5 py-0 text-[11px] text-muted-foreground"
                                >
                                  {loraBadge(lora)}
                                </Badge>
                                <span className="truncate">{lora.lora_name}</span>
                                {savedUrl && (
                                  <span
                                    className="flex shrink-0 items-center gap-0.5 text-accent-400"
                                    title={`取得元 URL: ${savedUrl}`}
                                  >
                                    <Check className="size-3" />
                                    URL
                                  </span>
                                )}
                              </p>
                            </div>
                            <Button
                              variant="outline"
                              size="xs"
                              aria-expanded={expanded}
                              aria-controls={`lora-details-${lora.id}`}
                              onClick={() => setExpandedLoraId(expanded ? null : lora.id)}
                            >
                              {expanded ? '閉じる' : '詳細'}
                            </Button>
                            <Button
                              variant="outline"
                              size="xs"
                              onClick={() => {
                                setEditingId(lora.id)
                                setDraftUrl(savedUrl)
                                setEditingLoraName(lora.lora_name)
                                setDraft({
                                  display_name: lora.display_name,
                                  lora_name: lora.lora_name,
                                  trigger_word: lora.trigger_word,
                                  default_strength: lora.default_strength,
                                  default_audio: lora.default_audio,
                                  sort_order: lora.sort_order,
                                  target: lora.target ?? 'image',
                                  family: lora.family ?? DEFAULT_FAMILY,
                                })
                              }}
                            >
                              <Pencil />
                              編集
                            </Button>
                            <Button
                              variant="destructive"
                              size="xs"
                              onClick={() => void removeLora(lora)}
                              disabled={busy}
                            >
                              <Trash2 />
                              削除
                            </Button>
                          </div>

                          {expanded && (
                            <div
                              id={`lora-details-${lora.id}`}
                              className="border-t border-border bg-surface-sunken/50 px-3 py-2 text-[11px] text-muted-foreground"
                            >
                              <p className="break-all">
                                trigger: {lora.trigger_word || '（なし）'} / strength:{' '}
                                {lora.default_strength}
                                {lora.default_audio ? ` / audio: ${lora.default_audio}` : ''}
                              </p>
                              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                {lora.sample_images.map((url) => (
                                  <div key={url} className="group relative">
                                    <a href={url} target="_blank" rel="noreferrer">
                                      <img
                                        src={url}
                                        alt={`${lora.display_name} サンプル`}
                                        loading="lazy"
                                        className="size-14 rounded-md border border-border object-cover"
                                      />
                                    </a>
                                    <button
                                      type="button"
                                      title="サンプルを削除"
                                      className="absolute -right-1.5 -top-1.5 hidden size-4 items-center justify-center rounded-full border border-border bg-card text-foreground/85 hover:text-red-400 group-hover:flex"
                                      onClick={() => void removeSample(lora, url)}
                                      disabled={busy}
                                    >
                                      <X className="size-2.5" />
                                      <span className="sr-only">サンプルを削除</span>
                                    </button>
                                  </div>
                                ))}
                                <label
                                  className="flex size-14 cursor-pointer items-center justify-center rounded-md border border-dashed border-border text-muted-foreground hover:border-primary hover:text-primary"
                                  title="サンプル画像を追加"
                                >
                                  <Plus className="size-5" />
                                  <input
                                    type="file"
                                    accept=".png,.jpg,.jpeg,.webp,.bmp"
                                    className="hidden"
                                    disabled={busy}
                                    onChange={(event) => {
                                      const file = event.target.files?.[0]
                                      event.target.value = ''
                                      if (file) void uploadSample(lora, file)
                                    }}
                                  />
                                </label>
                              </div>
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </Card>
                </div>

                <Card className="p-3 lg:sticky lg:top-4">
                  <h3 className="mb-2 text-sm font-medium text-foreground">
                    {editingId == null ? 'LoRA を追加' : `LoRA を編集 (#${editingId})`}
                  </h3>
                  <p className="mb-3 text-xs text-muted-foreground">
                    ComfyUI に置いた LoRA ファイルを、生成フォームから選べるように登録します。
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="表示名" htmlFor="lora-display-name">
                      <Input
                        id="lora-display-name"
                        value={draft.display_name}
                        onChange={(event) =>
                          setDraft({ ...draft, display_name: event.target.value })
                        }
                      />
                    </Field>
                    <Field label="ファイル名 (lora_name)" htmlFor="lora-file-name">
                      {/* 手入力が基本。入力し始めると一覧から補完候補が出る。 */}
                      <Input
                        id="lora-file-name"
                        list="lora-file-candidates"
                        placeholder="例: my_lora.safetensors"
                        value={draft.lora_name}
                        onChange={(event) =>
                          setDraft({ ...draft, lora_name: event.target.value })
                        }
                      />
                      <datalist id="lora-file-candidates">
                        {loraFiles.map((file) => (
                          <option key={file} value={file} />
                        ))}
                      </datalist>
                    </Field>
                    <Field label="対象ワークフロー" htmlFor="lora-target">
                      <Select
                        value={draft.target}
                        onValueChange={(value) =>
                          setDraft({ ...draft, target: value as LoraTarget })
                        }
                      >
                        <SelectTrigger id="lora-target">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(['video', 'image'] as LoraTarget[]).map((value) => (
                            <SelectItem key={value} value={value}>
                              {LORA_TARGET_LABELS[value]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </Field>
                    <Field label="モデルファミリー（画像用のみ）" htmlFor="lora-family">
                      <Select
                        value={draft.family}
                        disabled={draft.target === 'video'}
                        onValueChange={(value) => setDraft({ ...draft, family: value })}
                      >
                        <SelectTrigger id="lora-family">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {IMAGE_FAMILIES.map((value: ImageFamily) => (
                            <SelectItem key={value} value={value}>
                              {FAMILY_LABELS[value] ?? value}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </Field>
                    <Field
                      className="col-span-2"
                      label="トリガーワード"
                      htmlFor="lora-trigger-word"
                    >
                      <Input
                        id="lora-trigger-word"
                        value={draft.trigger_word}
                        onChange={(event) =>
                          setDraft({ ...draft, trigger_word: event.target.value })
                        }
                      />
                    </Field>
                    <Field label="既定強度" htmlFor="lora-default-strength">
                      <Input
                        id="lora-default-strength"
                        className="tnum"
                        type="number"
                        step="0.05"
                        value={draft.default_strength}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            default_strength: Number(event.target.value) || 0,
                          })
                        }
                      />
                    </Field>
                    <Field label="既定リファレンス音声" htmlFor="lora-default-audio">
                      <Select
                        value={draft.default_audio ?? NO_AUDIO}
                        onValueChange={(value) =>
                          setDraft({
                            ...draft,
                            default_audio: value === NO_AUDIO ? null : value,
                          })
                        }
                      >
                        <SelectTrigger id="lora-default-audio">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={NO_AUDIO}>（なし）</SelectItem>
                          {draft.default_audio &&
                            !audioAssets.some(
                              (asset) => asset.url === draft.default_audio,
                            ) && (
                              <SelectItem value={draft.default_audio}>
                                {draft.default_audio}
                              </SelectItem>
                            )}
                          {audioAssets.map((asset) => (
                            <SelectItem key={asset.url} value={asset.url}>
                              {asset.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </Field>
                    <Field label="並び順" htmlFor="lora-sort-order">
                      <Input
                        id="lora-sort-order"
                        className="tnum"
                        type="number"
                        value={draft.sort_order}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            sort_order: Number(event.target.value) || 0,
                          })
                        }
                      />
                    </Field>
                    {/* 取得元 URL: モデルタブと同じ model_download_urls（キーは
                        ファイル名）に、LoRA の保存と同時に書き込む。 */}
                    <Field
                      className="col-span-2"
                      label="取得元 URL（任意）"
                      htmlFor="lora-download-url"
                    >
                      <Input
                        id="lora-download-url"
                        placeholder="ダウンロード URL（Hugging Face / Civitai など）"
                        value={draftUrl}
                        onChange={(event) => setDraftUrl(event.target.value)}
                      />
                    </Field>
                  </div>
                  <div className="mt-3 flex gap-2">
                    <Button
                      size="sm"
                      onClick={() => void submitLora()}
                      disabled={busy || !draft.display_name || !draft.lora_name}
                    >
                      {editingId == null ? '追加' : '更新'}
                    </Button>
                    {editingId != null && (
                      <Button variant="outline" size="sm" onClick={resetLoraForm}>
                        キャンセル
                      </Button>
                    )}
                  </div>
                </Card>
              </div>
            </div>
          )}

          {tab === 'models' && (
            <div className="flex flex-col gap-3">
              {envPicker()}
              {showDownload && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void startAllDownloads()}
                    disabled={busy}
                    title="未検出かつ取得元 URL が登録済みのモデルをまとめて落とします"
                  >
                    <DownloadCloud />
                    全DL
                  </Button>
                </div>
              )}
              {/* ローカルに落とすときだけ関係する保存先の警告 */}
              {showDownload && envTarget !== 'runpod' && !dirStatusMessage(dirStatus).ok && (
                <p className="text-xs text-amber-400">
                  {dirStatusMessage(dirStatus).text}
                </p>
              )}
              {Object.entries(modelFiles).map(([name, files]) => (
                <datalist key={name} id={fileListId(name)}>
                  {files.map((file) => (
                    <option key={file} value={file} />
                  ))}
                </datalist>
              ))}
              {models.length === 0 && (
                <p className="text-xs text-muted-foreground">読み込み中…</p>
              )}
              {MODEL_KINDS.map(([kind, kindLabel]) => {
                const families = modelFamilies.filter(
                  (family) => family.kind === kind,
                )
                if (families.length === 0) return null
                return (
                  <div key={kind} className="flex flex-col gap-2">
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      {kindLabel}
                    </h3>
                    {families.map((family) => {
                      // ワークフローが 2 本以上あるモデル（動画の MiniMax H3）だけ
                      // モデル名の見出しを挟み、その下にワークフローを並べる。
                      // 1 本だけのモデルは見出しを出さず従来どおりの並び。
                      const nested = family.groups.length > 1
                      return (
                        <div key={family.id} className="flex flex-col gap-2">
                          {nested && (
                            <h4 className="text-xs font-medium text-foreground/85">
                              {family.label}
                            </h4>
                          )}
                          <div
                            className={`flex flex-col gap-2${
                              nested ? ' border-l border-border pl-2' : ''
                            }`}
                          >
                            {family.groups.map((group) => {
                              const open = openWorkflows[group.id] ?? false
                              return (
                                <Card key={group.id} className="overflow-hidden">
                                  <button
                                    type="button"
                                    aria-expanded={open}
                                    className="flex w-full items-center gap-2 rounded-lg p-2 text-left text-xs transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                                    onClick={() =>
                                      setOpenWorkflows((previous) => ({
                                        ...previous,
                                        [group.id]: !open,
                                      }))
                                    }
                                  >
                                    {open ? (
                                      <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
                                    ) : (
                                      <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
                                    )}
                                    <span className="text-foreground">{group.label}</span>
                                    <span className="tnum text-muted-foreground">
                                      {group.rows.length} 項目
                                    </span>
                                    {group.changed > 0 && (
                                      <Badge
                                        variant="outline"
                                        className="border-primary/60 px-1.5 py-0 text-[11px] text-accent-400"
                                      >
                                        未保存 {group.changed}
                                      </Badge>
                                    )}
                                    {group.custom > 0 && (
                                      <Badge
                                        variant="outline"
                                        className="px-1.5 py-0 text-[11px] text-muted-foreground"
                                      >
                                        既定から変更 {group.custom}
                                      </Badge>
                                    )}
                                  </button>
                                  {open && (
                                    <div className="overflow-x-auto border-t border-border">
                                      <table className="w-full text-xs">
                                        <thead className="text-left text-muted-foreground">
                                          <tr className="border-b border-border">
                                            <th className="p-2 font-medium">ノード</th>
                                            <th className="p-2 font-medium">既定値</th>
                                            <th className="p-2 font-medium">使用する値</th>
                                            <th className="p-2 font-medium">
                                              候補リスト（実行時に選べる）
                                            </th>
                                            {/* 取得元 URL の登録は COMFY_MODELS_DIR と無関係に
                                                使える（Pod 用のモデル一覧に要るため）。実際の
                                                ダウンロードだけが dir の状態に縛られる。 */}
                                            <th className="p-2 font-medium">
                                              取得元 URL / ダウンロード
                                            </th>
                                            <th className="p-2" />
                                          </tr>
                                        </thead>
                                        <tbody className="divide-y divide-border">
                                          {group.rows.map((row) => {
                                            const value = modelDraft[row.key] ?? ''
                                            const choices = choiceDraft[row.key] ?? []
                                            const changed =
                                              value !== row.value ||
                                              !sameChoices(choices, row.choices ?? [])
                                            const custom = value !== row.default
                                            const listId = modelFiles[
                                              `${row.class_type}.${row.field}`
                                            ]
                                              ? fileListId(`${row.class_type}.${row.field}`)
                                              : undefined
                                            // 不足モデルのダウンロード（SPEC §3.3）。URL と
                                            // 進捗はファイル名で持つので、同じファイルを使う
                                            // 別のワークフローの行にも同じものが出る。
                                            // 「未検出」は options（= いま繋いでいる ComfyUI）の
                                            // ファイル一覧で決まるので、別の環境を編集して
                                            // いるあいだは判定しない（他所の在庫は分からない）。
                                            const missing =
                                              connectedEnv && isMissing(row, modelFiles, value)
                                            const progress = downloads[value]
                                            const downloading =
                                              progress?.status === 'downloading'
                                            const url = (urlDraft[value] ?? '').trim()
                                            // 検出済みの行でも取得元 URL は登録できる（別の
                                            // 環境へ [DL] するときに要る）。表がうるさく
                                            // ならないよう、既定では畳んでおく。
                                            const savedUrl =
                                              settings?.model_download_urls?.[value] ?? ''
                                            const urlShown = urlOpen[value] ?? false
                                            return (
                                              <tr
                                                key={row.key}
                                                className={
                                                  changed ? 'bg-primary/10' : undefined
                                                }
                                              >
                                                <td className="p-2 align-top">
                                                  <p className="text-foreground">
                                                    {row.title || row.key}
                                                  </p>
                                                  <p className="text-muted-foreground">
                                                    {row.node_id}.{row.field} /{' '}
                                                    {row.class_type}
                                                  </p>
                                                </td>
                                                <td className="max-w-[16rem] break-all p-2 align-top text-muted-foreground">
                                                  {row.default}
                                                </td>
                                                <td className="p-2 align-top">
                                                  <Input
                                                    className="h-8 text-xs"
                                                    value={value}
                                                    list={listId}
                                                    onChange={(event) =>
                                                      setModelDraft((previous) => ({
                                                        ...previous,
                                                        [row.key]: event.target.value,
                                                      }))
                                                    }
                                                  />
                                                  {missing && (
                                                    <Badge
                                                      variant="warning"
                                                      className="mt-1 px-1.5 py-0 text-[11px]"
                                                      title="ComfyUI のファイル一覧に見つかりません"
                                                    >
                                                      未検出
                                                    </Badge>
                                                  )}
                                                </td>
                                                <td className="min-w-[16rem] p-2 align-top">
                                                  {choices.length > 0 && (
                                                    <div className="mb-1 flex flex-wrap gap-1">
                                                      {choices.map((name) => (
                                                        <Badge
                                                          key={name}
                                                          variant="secondary"
                                                          className="max-w-full gap-1 px-2 py-0.5"
                                                        >
                                                          <span className="max-w-[12rem] truncate">
                                                            {name}
                                                          </span>
                                                          <button
                                                            className="shrink-0 text-muted-foreground hover:text-foreground"
                                                            title="候補から削除"
                                                            onClick={() =>
                                                              removeChoice(row.key, name)
                                                            }
                                                          >
                                                            <X className="size-3" />
                                                            <span className="sr-only">
                                                              候補から削除
                                                            </span>
                                                          </button>
                                                        </Badge>
                                                      ))}
                                                    </div>
                                                  )}
                                                  <div className="flex gap-1">
                                                    <Input
                                                      className="h-8 text-xs"
                                                      placeholder="候補に追加するファイル名"
                                                      list={listId}
                                                      value={choiceInput[row.key] ?? ''}
                                                      onChange={(event) =>
                                                        setChoiceInput((previous) => ({
                                                          ...previous,
                                                          [row.key]: event.target.value,
                                                        }))
                                                      }
                                                      onKeyDown={(event) => {
                                                        if (event.key !== 'Enter') return
                                                        event.preventDefault()
                                                        addChoice(row.key)
                                                      }}
                                                    />
                                                    <Button
                                                      variant="outline"
                                                      size="sm"
                                                      disabled={
                                                        !(choiceInput[row.key] ?? '').trim()
                                                      }
                                                      onClick={() => addChoice(row.key)}
                                                    >
                                                      追加
                                                    </Button>
                                                  </div>
                                                </td>
                                                <td className="min-w-[16rem] p-2 align-top">
                                                    {/* 未検出の行は URL 欄と [DL] をそのまま
                                                        出す。検出済みの行は [取得元 URL] で
                                                        開いたときだけ出し、[URL保存] だけに
                                                        する。落とせない事情（保存先が無い・
                                                        Pod が停止中）は押したときに理由が
                                                        返るので、ボタンは隠さない。 */}
                                                    {!missing && (
                                                      <button
                                                        type="button"
                                                        aria-expanded={urlShown}
                                                        className={`flex items-center gap-1 rounded-sm text-xs transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:opacity-40 ${
                                                          savedUrl
                                                            ? 'text-accent-400'
                                                            : 'text-muted-foreground'
                                                        }`}
                                                        disabled={!value}
                                                        title={
                                                          savedUrl
                                                            ? `取得元 URL: ${savedUrl}`
                                                            : '取得元 URL を登録する（[DL] / [全DL] のときに使われます）'
                                                        }
                                                        onClick={() =>
                                                          setUrlOpen((previous) => ({
                                                            ...previous,
                                                            [value]: !urlShown,
                                                          }))
                                                        }
                                                      >
                                                        {urlShown ? (
                                                          <ChevronDown className="size-3" />
                                                        ) : (
                                                          <ChevronRight className="size-3" />
                                                        )}
                                                        取得元 URL
                                                        {savedUrl && (
                                                          <>
                                                            <Check className="size-3" />
                                                            <span className="sr-only">
                                                              登録済み
                                                            </span>
                                                          </>
                                                        )}
                                                      </button>
                                                    )}
                                                    {(missing || urlShown) && (
                                                      <div
                                                        className={`flex gap-1 ${missing ? '' : 'mt-1'}`}
                                                      >
                                                        <Input
                                                          className="h-8 text-xs"
                                                          placeholder="ダウンロード URL（Hugging Face / Civitai など）"
                                                          value={urlDraft[value] ?? ''}
                                                          disabled={!value}
                                                          onChange={(event) =>
                                                            setUrlDraft((previous) => ({
                                                              ...previous,
                                                              [value]: event.target.value,
                                                            }))
                                                          }
                                                        />
                                                        {!missing && (
                                                          <Button
                                                            variant="outline"
                                                            size="sm"
                                                            disabled={busy || url === savedUrl}
                                                            title="ダウンロードはせず、取得元 URL だけ設定に保存します（空欄で保存すると登録を解除）"
                                                            onClick={() =>
                                                              void saveDownloadUrl(value)
                                                            }
                                                          >
                                                            URL保存
                                                          </Button>
                                                        )}
                                                        {showDownload && (
                                                          <Button
                                                            variant="outline"
                                                            size="sm"
                                                            disabled={
                                                              busy ||
                                                              downloading ||
                                                              !value ||
                                                              !url
                                                            }
                                                            title={`${COMFY_TARGET_LABELS[envTarget ?? 'local']}の ${row.subfolder || 'models 直下'} に保存します`}
                                                            onClick={() =>
                                                              void startDownload(row, value)
                                                            }
                                                          >
                                                            <Download />
                                                            DL
                                                          </Button>
                                                        )}
                                                      </div>
                                                    )}
                                                    {missing && showDownload && (
                                                      <p className="mt-1 text-[11px] text-muted-foreground">
                                                        保存先: {row.subfolder || 'models 直下'}
                                                      </p>
                                                    )}
                                                    {progress && (
                                                      <div className="mt-1">
                                                        {downloading && (
                                                          <div className="h-1 overflow-hidden rounded-full bg-secondary">
                                                            <div
                                                              className="h-full bg-primary transition-[width]"
                                                              style={{
                                                                width: progress.total
                                                                  ? `${Math.min(100, (progress.received / progress.total) * 100)}%`
                                                                  : '100%',
                                                              }}
                                                            />
                                                          </div>
                                                        )}
                                                        <p
                                                          className={`tnum text-[11px] ${
                                                            progress.status === 'error'
                                                              ? 'text-red-400'
                                                              : progress.status === 'done'
                                                                ? 'text-emerald-400'
                                                                : 'text-muted-foreground'
                                                          }`}
                                                        >
                                                          {progress.status === 'error'
                                                            ? `失敗: ${progress.error ?? ''}`
                                                            : progress.status === 'done'
                                                              ? `完了（${formatBytes(progress.received)}）`
                                                              : `${formatBytes(progress.received)}${
                                                                  progress.total
                                                                    ? ` / ${formatBytes(progress.total)}`
                                                                    : ''
                                                                } 取得中…`}
                                                        </p>
                                                      </div>
                                                    )}
                                                  </td>
                                                <td className="p-2 align-top">
                                                  <Button
                                                    variant="outline"
                                                    size="sm"
                                                    disabled={!custom}
                                                    onClick={() =>
                                                      setModelDraft((previous) => ({
                                                        ...previous,
                                                        [row.key]: row.default,
                                                      }))
                                                    }
                                                  >
                                                    <RotateCcw />
                                                    既定に戻す
                                                  </Button>
                                                </td>
                                              </tr>
                                            )
                                          })}
                                        </tbody>
                                      </table>
                                    </div>
                                  )}
                                </Card>
                              )
                            })}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )
              })}
              <Button
                className="self-start"
                onClick={() => void saveModels()}
                disabled={busy || models.length === 0 || !modelsDirty}
              >
                保存
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
