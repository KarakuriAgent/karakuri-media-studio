import { useEffect, useState } from 'react'
import { api, wsUrl } from '../api'
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
  ModelFieldState,
  ModelsDirStatus,
  Options,
  Settings,
} from '../types'
import { Banner } from './ui'

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
          agent_max_plan_tasks: settings.agent_max_plan_tasks,
          agent_max_turns: settings.agent_max_turns,
          canvas_max_turns: settings.canvas_max_turns,
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

  /** モデル / LoRA タブの先頭に置く環境プルダウン（SPEC §5）。 */
  const envPicker = () => (
    <div className="card flex flex-wrap items-center gap-2 p-2">
      <label className="text-xs text-slate-400" htmlFor="settings-env">
        対象の接続先
      </label>
      <select
        id="settings-env"
        className="field max-w-[14rem]"
        value={envTarget ?? 'local'}
        disabled={envTarget == null || busy}
        onChange={(event) =>
          void changeEnvTarget(event.target.value as ComfyTarget)
        }
      >
        {COMFY_TARGETS.map((target) => (
          <option key={target} value={target}>
            {COMFY_TARGET_LABELS[target]}
            {target === settings?.comfy_target ? '（現在の接続先）' : ''}
          </option>
        ))}
      </select>
    </div>
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
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b border-ink-700 bg-ink-800/80 px-4 py-2.5">
        <button className="btn-ghost" onClick={onBack}>
          ← 戻る
        </button>
        <h2 className="text-sm font-semibold text-slate-100">設定</h2>
        <div className="ml-4 flex gap-1 rounded-lg border border-ink-600 bg-ink-900 p-1 text-xs">
          {TABS.map(([key, label]) => (
            <button
              key={key}
              className={`rounded px-3 py-1.5 ${
                tab === key
                  ? 'bg-accent-500 text-white'
                  : 'text-slate-400 hover:bg-ink-700'
              }`}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </div>
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
              {!settings && <p className="text-xs text-slate-500">読み込み中…</p>}
              {settings && (
                <>
                  {/* ComfyUI の接続先（SPEC §5）。3 プロファイル分の接続情報を
                      ここに置き、「接続先」がそのどれを使うかを決める。生成
                      フォーム上部のプルダウンは同じ値を書き換える。 */}
                  <div className="card flex flex-col gap-3 p-3">
                    <h4 className="text-xs font-semibold text-slate-300">
                      ComfyUI 接続先
                    </h4>
                    <div>
                      <label className="label">接続先</label>
                      <select
                        className="field"
                        value={settings.comfy_target}
                        onChange={(event) =>
                          update({
                            comfy_target: event.target.value as ComfyTarget,
                          })
                        }
                      >
                        {COMFY_TARGETS.map((target) => (
                          <option key={target} value={target}>
                            {COMFY_TARGET_LABELS[target]}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="rounded-lg border border-ink-600 p-2">
                      <h5 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                        ComfyCloud
                      </h5>
                      <div className="flex flex-col gap-2">
                        <div>
                          <label className="label">ComfyCloud APIキー</label>
                          <input
                            className="field"
                            type="password"
                            autoComplete="off"
                            value={settings.comfy_cloud_api_key}
                            onChange={(event) =>
                              update({ comfy_cloud_api_key: event.target.value })
                            }
                          />
                        </div>
                      </div>
                    </div>

                    {/* ComfyUI を RunPod の Pod で動かす構成（SPEC §5.1）。
                        自動起動を有効にすると、接続先が RunPod のときだけ、
                        ジョブ投入の直前に疎通を確かめて落ちていれば Pod を作って
                        待つ。Pod の停止はイメージ側の watchdog が行う。 */}
                    <div className="rounded-lg border border-ink-600 p-2">
                      <h5 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                        RunPod
                      </h5>
                      <div className="flex flex-col gap-2">
                        <div>
                          <label className="label">RunPod ComfyUI URL</label>
                          <input
                            className="field"
                            placeholder="https://<Cloudflare Tunnel のホスト名>"
                            value={settings.runpod_comfy_url}
                            onChange={(event) =>
                              update({ runpod_comfy_url: event.target.value })
                            }
                          />
                        </div>
                        <div>
                          <label className="label">
                            RunPod ComfyUI APIキー（任意）
                          </label>
                          <input
                            className="field"
                            type="password"
                            autoComplete="off"
                            value={settings.runpod_comfy_api_key}
                            onChange={(event) =>
                              update({ runpod_comfy_api_key: event.target.value })
                            }
                          />
                        </div>
                        <label className="flex items-center gap-2 text-xs font-semibold text-slate-300">
                          <input
                            type="checkbox"
                            checked={settings.runpod_enabled}
                            onChange={(event) =>
                              update({ runpod_enabled: event.target.checked })
                            }
                          />
                          RunPod の Pod を自動起動する
                        </label>
                        {settings.runpod_enabled && (
                          <>
                            <div>
                              <label className="label">RunPod APIキー</label>
                              <input
                                className="field"
                                type="password"
                                autoComplete="off"
                                value={settings.runpod_api_key}
                                onChange={(event) =>
                                  update({ runpod_api_key: event.target.value })
                                }
                              />
                            </div>
                            <div className="grid grid-cols-2 gap-2">
                              <div>
                                <label className="label">テンプレート ID</label>
                                <input
                                  className="field"
                                  value={settings.runpod_template_id}
                                  onChange={(event) =>
                                    update({
                                      runpod_template_id: event.target.value,
                                    })
                                  }
                                />
                              </div>
                              <div>
                                <label className="label">GPU 種別（gpuTypeId）</label>
                                <input
                                  className="field"
                                  value={settings.runpod_gpu_type}
                                  onChange={(event) =>
                                    update({ runpod_gpu_type: event.target.value })
                                  }
                                />
                              </div>
                            </div>
                            <div>
                              <label className="label">
                                Network Volume ID（任意）
                              </label>
                              <input
                                className="field"
                                value={settings.runpod_network_volume_id}
                                onChange={(event) =>
                                  update({
                                    runpod_network_volume_id: event.target.value,
                                  })
                                }
                              />
                            </div>
                          </>
                        )}
                      </div>
                    </div>

                    <div className="rounded-lg border border-ink-600 p-2">
                      <h5 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                        ローカル
                      </h5>
                      <div>
                        <label className="label">ローカル ComfyUI URL</label>
                        <input
                          className="field"
                          placeholder="http://127.0.0.1:8188"
                          value={settings.local_comfy_url}
                          onChange={(event) =>
                            update({ local_comfy_url: event.target.value })
                          }
                        />
                      </div>
                    </div>
                  </div>
                  {/* 不足モデルの自動ダウンロード（SPEC §3.3）。トークンは
                      ローカルにも RunPod の Pod にも要るので常に出す。保存先の
                      環境変数はローカルに落とすときだけ関係する。 */}
                  <div className="card flex flex-col gap-2 p-3">
                      <h4 className="text-xs font-semibold text-slate-300">
                        モデル自動ダウンロード
                      </h4>
                      <div>
                        <label className="label">
                          ローカルの保存先（環境変数 COMFY_MODELS_DIR）
                        </label>
                        <input
                          className="field"
                          value={dirStatus?.path ?? ''}
                          readOnly
                        />
                        <p
                          className={`mt-1 text-[11px] ${
                            dirStatusMessage(dirStatus).ok
                              ? 'text-emerald-400'
                              : 'text-amber-400'
                          }`}
                        >
                          {dirStatusMessage(dirStatus).text}
                        </p>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className="label">
                            Hugging Face トークン（gated モデル用・任意）
                          </label>
                          <input
                            className="field"
                            type="password"
                            autoComplete="off"
                            value={settings.hf_token}
                            onChange={(event) =>
                              update({ hf_token: event.target.value })
                            }
                          />
                        </div>
                        <div>
                          <label className="label">Civitai APIキー（任意）</label>
                          <input
                            className="field"
                            type="password"
                            autoComplete="off"
                            value={settings.civitai_api_key}
                            onChange={(event) =>
                              update({ civitai_api_key: event.target.value })
                            }
                          />
                        </div>
                      </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="label">grok コマンド</label>
                      <input
                        className="field"
                        value={settings.grok_command}
                        onChange={(event) =>
                          update({ grok_command: event.target.value })
                        }
                      />
                    </div>
                    <div>
                      <label className="label">grok モデル</label>
                      <input
                        className="field"
                        value={settings.grok_model}
                        onChange={(event) => update({ grok_model: event.target.value })}
                      />
                    </div>
                    <div>
                      <label className="label" htmlFor="grok-media-timeout">
                        Grok Imagine の制限時間（秒）
                      </label>
                      <input
                        id="grok-media-timeout"
                        className="field"
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
                    </div>
                    <div>
                      <label className="label" htmlFor="grok-workdir">
                        grok の作業ディレクトリ（空 = 既定）
                      </label>
                      <input
                        id="grok-workdir"
                        className="field"
                        value={settings.grok_workdir}
                        placeholder="/path/to/workdir"
                        onChange={(event) =>
                          update({ grok_workdir: event.target.value })
                        }
                      />
                      <p className="mt-1 text-[11px] text-slate-500">
                        チャット / エージェントが grok CLI を回すディレクトリです。
                      </p>
                    </div>
                    <div>
                      <label className="label" htmlFor="grok-media-workdir">
                        Grok Imagine の作業ディレクトリ（空 = 既定）
                      </label>
                      <input
                        id="grok-media-workdir"
                        className="field"
                        value={settings.grok_media_workdir}
                        placeholder="/path/to/workdir"
                        onChange={(event) =>
                          update({ grok_media_workdir: event.target.value })
                        }
                      />
                      <p className="mt-1 text-[11px] text-slate-500">
                        画像生成・編集（SPEC §5.2）で使う置き場所です。
                      </p>
                    </div>
                    <div>
                      <label className="label" htmlFor="agent-grok-args">
                        grok CLI の追加フラグ（空白区切り）
                      </label>
                      <input
                        id="agent-grok-args"
                        className="field"
                        value={grokArgsDraft}
                        placeholder="--permission-mode auto"
                        onChange={(event) => setGrokArgsDraft(event.target.value)}
                      />
                      <p className="mt-1 text-[11px] text-amber-400">
                        <strong>空にするとエージェントのツールが無効になります</strong>
                        （ファイルの読み書き・画像の確認・Web 検索ができなくなり、
                        システムプロンプトからもツールの節が落ちます）。
                      </p>
                    </div>
                    <div>
                      <p className="label">エージェントの実行方式</p>
                      <label
                        className="flex items-center gap-2 text-xs text-slate-300"
                        htmlFor="agent-use-acp"
                      >
                        <input
                          id="agent-use-acp"
                          type="checkbox"
                          className="accent-accent-500"
                          checked={settings.agent_use_acp}
                          onChange={(event) =>
                            update({ agent_use_acp: event.target.checked })
                          }
                        />
                        ACP でターンを回す
                      </label>
                      <p className="mt-1 text-[11px] text-slate-500">
                        オンだと実行中の活動（思考 / ツール実行）がチャットに出ます。
                        オフは従来のワンショット実行で、活動表示はありません。
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      className="btn-ghost self-start"
                      onClick={() => void checkGrok()}
                      disabled={grokChecking}
                    >
                      {grokChecking ? '確認中…' : 'grok CLI の接続確認'}
                    </button>
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
                  {/* エージェント / 相談の実行上限（AGENT-MODE §3.4）。
                      既定値は従来どおりで、0 を入れたときだけ無制限になる。 */}
                  <div className="card flex flex-col gap-2 p-3">
                    <h4 className="text-xs font-semibold text-slate-300">
                      実行上限（0 = 無制限）
                    </h4>
                    <p className="text-[11px] text-slate-500">
                      暴走防止の上限です。0 を入れるとその項目だけ無制限になります
                      （止めたいときは各画面の「停止」で止めてください）。
                    </p>
                    <div className="grid gap-2 sm:grid-cols-2">
                      <div>
                        <label className="label" htmlFor="agent-max-turns">
                          エージェントの連続ターン上限（0 = 無制限）
                        </label>
                        <input
                          id="agent-max-turns"
                          className="field"
                          type="number"
                          min="0"
                          step="1"
                          value={settings.agent_max_turns}
                          onChange={(event) =>
                            update({
                              agent_max_turns: Number(event.target.value) || 0,
                            })
                          }
                        />
                      </div>
                      <div>
                        <label className="label" htmlFor="canvas-max-turns">
                          キャンバスの連続ターン上限（0 = 無制限）
                        </label>
                        <input
                          id="canvas-max-turns"
                          className="field"
                          type="number"
                          min="0"
                          step="1"
                          value={settings.canvas_max_turns}
                          onChange={(event) =>
                            update({
                              canvas_max_turns: Number(event.target.value) || 0,
                            })
                          }
                        />
                      </div>
                      <div>
                        <label className="label" htmlFor="agent-max-plan-tasks">
                          1 プラン提案の新規ジョブ上限（自走時・0 = 無制限）
                        </label>
                        <input
                          id="agent-max-plan-tasks"
                          className="field"
                          type="number"
                          min="0"
                          step="1"
                          value={settings.agent_max_plan_tasks}
                          onChange={(event) =>
                            update({
                              agent_max_plan_tasks:
                                Number(event.target.value) || 0,
                            })
                          }
                        />
                      </div>
                      <div>
                        <label className="label" htmlFor="agent-grok-timeout">
                          grok の制限時間（秒・0 = タイムアウトなし）
                        </label>
                        <input
                          id="agent-grok-timeout"
                          className="field"
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
                      </div>
                    </div>
                  </div>
                  {/* 外部公開 API（docs/EXTERNAL-API.md）。キーを入れることが
                      有効化そのもので、空のあいだは /api/v1 が丸ごと 404。 */}
                  <div className="card flex flex-col gap-2 p-3">
                    <h4 className="text-xs font-semibold text-slate-300">
                      外部 API（/api/v1）
                    </h4>
                    <div className="grid gap-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
                      <div>
                        <label className="label" htmlFor="external-api-key">
                          API キー（空 = 外部 API は無効）
                        </label>
                        <div className="flex gap-2">
                          <input
                            id="external-api-key"
                            className="field min-w-0 flex-1"
                            type="password"
                            autoComplete="off"
                            value={settings.external_api_key}
                            onChange={(event) =>
                              update({ external_api_key: event.target.value })
                            }
                          />
                          <button
                            className="btn-ghost shrink-0"
                            onClick={() =>
                              update({ external_api_key: randomApiKey() })
                            }
                          >
                            生成
                          </button>
                        </div>
                      </div>
                      <div>
                        <label className="label" htmlFor="external-max-pending">
                          未完了 Take の上限（0 = 無制限）
                        </label>
                        <input
                          id="external-max-pending"
                          className="field"
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
                      </div>
                    </div>
                  </div>
                  <button
                    className="btn-primary self-start"
                    onClick={() => void saveSettings()}
                    disabled={busy}
                  >
                    保存
                  </button>
                </>
              )}
            </div>
          )}

          {tab === 'loras' && (
            <div className="flex flex-col gap-4">
              {envPicker()}
              <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(22rem,0.8fr)]">
                <div className="flex min-w-0 flex-col gap-2">
                  <div className="card p-3">
                    <div className="flex flex-col gap-2 sm:flex-row">
                      <div className="min-w-0 flex-1">
                        <label className="sr-only" htmlFor="lora-management-search">
                          LoRAを検索
                        </label>
                        <input
                          id="lora-management-search"
                          className="field"
                          placeholder="名前・ファイル名・トリガーで検索"
                          value={loraQuery}
                          onChange={(event) => setLoraQuery(event.target.value)}
                        />
                      </div>
                      <select
                        className="field sm:w-28"
                        aria-label="LoRAの対象"
                        value={loraTargetFilter}
                        onChange={(event) =>
                          setLoraTargetFilter(event.target.value as 'all' | LoraTarget)
                        }
                      >
                        <option value="all">すべて</option>
                        <option value="image">画像用</option>
                        <option value="video">動画用</option>
                      </select>
                      <select
                        className="field sm:w-32"
                        aria-label="LoRAのファミリー"
                        value={loraFamilyFilter}
                        disabled={loraTargetFilter === 'video'}
                        onChange={(event) => setLoraFamilyFilter(event.target.value)}
                      >
                        <option value="all">全ファミリー</option>
                        {IMAGE_FAMILIES.map((value) => (
                          <option key={value} value={value}>
                            {FAMILY_LABELS[value] ?? value}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-slate-500">
                      <span>
                        表示 {filteredLoras.length} / 全 {loras.length}
                      </span>
                      <button className="btn-ghost !py-1 text-[11px]" onClick={resetLoraForm}>
                        新規登録
                      </button>
                    </div>
                  </div>

                  <div
                    data-testid="lora-management-list"
                    className="card max-h-[32rem] divide-y divide-ink-600 overflow-y-auto overscroll-contain"
                  >
                    {loras.length === 0 && (
                      <p className="p-4 text-xs text-slate-500">登録がありません</p>
                    )}
                    {loras.length > 0 && filteredLoras.length === 0 && (
                      <p className="p-4 text-center text-xs text-slate-500">
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
                          className={editingId === lora.id ? 'bg-accent-500/5' : ''}
                        >
                          <div className="flex items-center gap-2 p-2 text-xs">
                            {sample ? (
                              <img
                                src={sample}
                                alt=""
                                aria-hidden="true"
                                loading="lazy"
                                className="h-10 w-10 shrink-0 rounded border border-ink-600 object-cover"
                              />
                            ) : (
                              <span
                                aria-hidden="true"
                                className="flex h-10 w-10 shrink-0 items-center justify-center rounded border border-ink-600 bg-ink-900 text-slate-600"
                              >
                                L
                              </span>
                            )}
                            <div className="min-w-0 flex-1">
                              <p className="truncate font-medium text-slate-200">
                                {lora.display_name}
                              </p>
                              <p className="truncate text-slate-500">
                                <span className="mr-1.5 rounded border border-ink-600 px-1 py-px text-[10px] text-slate-400">
                                  {loraBadge(lora)}
                                </span>
                                {lora.lora_name}
                                {savedUrl && (
                                  <span
                                    className="ml-1.5 text-accent-400"
                                    title={`取得元 URL: ${savedUrl}`}
                                  >
                                    URL ✓
                                  </span>
                                )}
                              </p>
                            </div>
                            <button
                              className="btn-ghost !px-2 !py-1 text-[11px]"
                              aria-expanded={expanded}
                              aria-controls={`lora-details-${lora.id}`}
                              onClick={() => setExpandedLoraId(expanded ? null : lora.id)}
                            >
                              {expanded ? '閉じる' : '詳細'}
                            </button>
                            <button
                              className="btn-ghost !px-2 !py-1 text-[11px]"
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
                              編集
                            </button>
                            <button
                              className="btn-danger !px-2 !py-1 text-[11px]"
                              onClick={() => void removeLora(lora)}
                              disabled={busy}
                            >
                              削除
                            </button>
                          </div>

                          {expanded && (
                            <div
                              id={`lora-details-${lora.id}`}
                              className="border-t border-ink-700 bg-ink-900/40 px-3 py-2 text-[11px] text-slate-500"
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
                                        className="h-14 w-14 rounded border border-ink-600 object-cover"
                                      />
                                    </a>
                                    <button
                                      type="button"
                                      title="サンプルを削除"
                                      className="absolute -right-1.5 -top-1.5 hidden h-4 w-4 items-center justify-center rounded-full border border-ink-600 bg-ink-900 text-[10px] leading-none text-slate-300 hover:text-red-400 group-hover:flex"
                                      onClick={() => void removeSample(lora, url)}
                                      disabled={busy}
                                    >
                                      ×
                                    </button>
                                  </div>
                                ))}
                                <label
                                  className="flex h-14 w-14 cursor-pointer items-center justify-center rounded border border-dashed border-ink-600 text-lg text-slate-500 hover:border-accent-500 hover:text-accent-500"
                                  title="サンプル画像を追加"
                                >
                                  ＋
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
                  </div>
                </div>

                <div className="card p-3 lg:sticky lg:top-4">
                <h4 className="mb-2 text-xs font-semibold text-slate-300">
                  {editingId == null ? 'LoRA を追加' : `LoRA を編集 (#${editingId})`}
                </h4>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="label" htmlFor="lora-display-name">
                      表示名
                    </label>
                    <input
                      id="lora-display-name"
                      className="field"
                      value={draft.display_name}
                      onChange={(event) =>
                        setDraft({ ...draft, display_name: event.target.value })
                      }
                    />
                  </div>
                  <div>
                    <label className="label">ファイル名 (lora_name)</label>
                    {/* 手入力が基本。入力し始めると一覧から補完候補が出る。 */}
                    <input
                      className="field"
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
                  </div>
                  <div>
                    <label className="label">対象ワークフロー</label>
                    <select
                      className="field"
                      value={draft.target}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          target: event.target.value as LoraTarget,
                        })
                      }
                    >
                      {(['video', 'image'] as LoraTarget[]).map((value) => (
                        <option key={value} value={value}>
                          {LORA_TARGET_LABELS[value]}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="label">モデルファミリー（画像用のみ）</label>
                    <select
                      className="field"
                      value={draft.family}
                      disabled={draft.target === 'video'}
                      onChange={(event) =>
                        setDraft({ ...draft, family: event.target.value })
                      }
                    >
                      {IMAGE_FAMILIES.map((value: ImageFamily) => (
                        <option key={value} value={value}>
                          {FAMILY_LABELS[value] ?? value}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="col-span-2">
                    <label className="label">トリガーワード</label>
                    <input
                      className="field"
                      value={draft.trigger_word}
                      onChange={(event) =>
                        setDraft({ ...draft, trigger_word: event.target.value })
                      }
                    />
                  </div>
                  <div>
                    <label className="label">既定強度</label>
                    <input
                      className="field"
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
                  </div>
                  <div>
                    <label className="label">既定リファレンス音声</label>
                    <select
                      className="field"
                      value={draft.default_audio ?? ''}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          default_audio: event.target.value || null,
                        })
                      }
                    >
                      <option value="">（なし）</option>
                      {draft.default_audio &&
                        !audioAssets.some(
                          (asset) => asset.url === draft.default_audio,
                        ) && (
                          <option value={draft.default_audio}>
                            {draft.default_audio}
                          </option>
                        )}
                      {audioAssets.map((asset) => (
                        <option key={asset.url} value={asset.url}>
                          {asset.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="label">並び順</label>
                    <input
                      className="field"
                      type="number"
                      value={draft.sort_order}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          sort_order: Number(event.target.value) || 0,
                        })
                      }
                    />
                  </div>
                  {/* 取得元 URL: モデルタブと同じ model_download_urls（キーは
                      ファイル名）に、LoRA の保存と同時に書き込む。 */}
                  <div className="col-span-2">
                    <label className="label">取得元 URL（任意）</label>
                    <input
                      className="field"
                      placeholder="ダウンロード URL（Hugging Face / Civitai など）"
                      value={draftUrl}
                      onChange={(event) => setDraftUrl(event.target.value)}
                    />
                  </div>
                </div>
                <div className="mt-3 flex gap-2">
                  <button
                    className="btn-primary text-xs"
                    onClick={() => void submitLora()}
                    disabled={busy || !draft.display_name || !draft.lora_name}
                  >
                    {editingId == null ? '追加' : '更新'}
                  </button>
                  {editingId != null && (
                    <button className="btn-ghost text-xs" onClick={resetLoraForm}>
                      キャンセル
                    </button>
                  )}
                </div>
              </div>
            </div>
            </div>
          )}

          {tab === 'models' && (
            <div className="flex flex-col gap-3">
              {envPicker()}
              {showDownload && (
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    className="btn-ghost text-xs"
                    onClick={() => void startAllDownloads()}
                    disabled={busy}
                    title="未検出かつ取得元 URL が登録済みのモデルをまとめて落とします"
                  >
                    全DL
                  </button>
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
                <p className="text-xs text-slate-500">読み込み中…</p>
              )}
              {MODEL_KINDS.map(([kind, kindLabel]) => {
                const families = modelFamilies.filter(
                  (family) => family.kind === kind,
                )
                if (families.length === 0) return null
                return (
                  <div key={kind} className="flex flex-col gap-2">
                    <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                      {kindLabel}
                    </h4>
                    {families.map((family) => {
                      // ワークフローが 2 本以上あるモデル（動画の MiniMax H3）だけ
                      // モデル名の見出しを挟み、その下にワークフローを並べる。
                      // 1 本だけのモデルは見出しを出さず従来どおりの並び。
                      const nested = family.groups.length > 1
                      return (
                        <div key={family.id} className="flex flex-col gap-2">
                          {nested && (
                            <h5 className="text-xs font-medium text-slate-300">
                              {family.label}
                            </h5>
                          )}
                          <div
                            className={`flex flex-col gap-2${
                              nested ? ' border-l border-ink-600 pl-2' : ''
                            }`}
                          >
                            {family.groups.map((group) => {
                              const open = openWorkflows[group.id] ?? false
                              return (
                                <div key={group.id} className="card overflow-hidden">
                                  <button
                                    type="button"
                                    className="flex w-full items-center gap-2 p-2 text-left text-xs hover:bg-ink-700"
                                    onClick={() =>
                                      setOpenWorkflows((previous) => ({
                                        ...previous,
                                        [group.id]: !open,
                                      }))
                                    }
                                  >
                                    <span className="w-3 text-slate-500">
                                      {open ? '▾' : '▸'}
                                    </span>
                                    <span className="text-slate-200">{group.label}</span>
                                    <span className="text-slate-600">
                                      {group.rows.length} 項目
                                    </span>
                                    {group.changed > 0 && (
                                      <span className="rounded border border-accent-500 px-1 py-px text-[10px] text-accent-400">
                                        未保存 {group.changed}
                                      </span>
                                    )}
                                    {group.custom > 0 && (
                                      <span className="rounded border border-ink-500 px-1 py-px text-[10px] text-slate-400">
                                        既定から変更 {group.custom}
                                      </span>
                                    )}
                                  </button>
                                  {open && (
                                    <div className="overflow-x-auto border-t border-ink-600">
                                      <table className="w-full text-xs">
                                        <thead className="text-left text-slate-500">
                                          <tr className="border-b border-ink-600">
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
                                        <tbody className="divide-y divide-ink-600">
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
                                                  changed ? 'bg-accent-500/10' : undefined
                                                }
                                              >
                                                <td className="p-2 align-top">
                                                  <p className="text-slate-200">
                                                    {row.title || row.key}
                                                  </p>
                                                  <p className="text-slate-600">
                                                    {row.node_id}.{row.field} /{' '}
                                                    {row.class_type}
                                                  </p>
                                                </td>
                                                <td className="max-w-[16rem] break-all p-2 align-top text-slate-500">
                                                  {row.default}
                                                </td>
                                                <td className="p-2 align-top">
                                                  <input
                                                    className="field"
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
                                                    <span
                                                      className="mt-1 inline-block rounded border border-amber-500 px-1 py-px text-[10px] text-amber-400"
                                                      title="ComfyUI のファイル一覧に見つかりません"
                                                    >
                                                      未検出
                                                    </span>
                                                  )}
                                                </td>
                                                <td className="min-w-[16rem] p-2 align-top">
                                                  {choices.length > 0 && (
                                                    <div className="mb-1 flex flex-wrap gap-1">
                                                      {choices.map((name) => (
                                                        <span
                                                          key={name}
                                                          className="chip border-ink-500 bg-ink-700 text-slate-300"
                                                        >
                                                          <span className="max-w-[12rem] truncate">
                                                            {name}
                                                          </span>
                                                          <button
                                                            className="text-slate-500 hover:text-slate-200"
                                                            title="候補から削除"
                                                            onClick={() =>
                                                              removeChoice(row.key, name)
                                                            }
                                                          >
                                                            ×
                                                          </button>
                                                        </span>
                                                      ))}
                                                    </div>
                                                  )}
                                                  <div className="flex gap-1">
                                                    <input
                                                      className="field"
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
                                                    <button
                                                      className="btn-ghost !py-1 text-xs"
                                                      disabled={
                                                        !(choiceInput[row.key] ?? '').trim()
                                                      }
                                                      onClick={() => addChoice(row.key)}
                                                    >
                                                      追加
                                                    </button>
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
                                                        className={`text-xs underline decoration-dotted underline-offset-2 hover:text-slate-200 disabled:opacity-40 ${
                                                          savedUrl
                                                            ? 'text-accent-400'
                                                            : 'text-slate-500'
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
                                                        {urlShown ? '▾' : '▸'} 取得元 URL
                                                        {savedUrl ? ' ✓' : ''}
                                                      </button>
                                                    )}
                                                    {(missing || urlShown) && (
                                                      <div
                                                        className={`flex gap-1 ${missing ? '' : 'mt-1'}`}
                                                      >
                                                        <input
                                                          className="field"
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
                                                          <button
                                                            className="btn-ghost !py-1 text-xs"
                                                            disabled={busy || url === savedUrl}
                                                            title="ダウンロードはせず、取得元 URL だけ設定に保存します（空欄で保存すると登録を解除）"
                                                            onClick={() =>
                                                              void saveDownloadUrl(value)
                                                            }
                                                          >
                                                            URL保存
                                                          </button>
                                                        )}
                                                        {showDownload && (
                                                          <button
                                                            className="btn-ghost !py-1 text-xs"
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
                                                            DL
                                                          </button>
                                                        )}
                                                      </div>
                                                    )}
                                                    {missing && showDownload && (
                                                      <p className="mt-1 text-[10px] text-slate-600">
                                                        保存先: {row.subfolder || 'models 直下'}
                                                      </p>
                                                    )}
                                                    {progress && (
                                                      <div className="mt-1">
                                                        {downloading && (
                                                          <div className="h-1 overflow-hidden rounded bg-ink-700">
                                                            <div
                                                              className="h-full bg-accent-500"
                                                              style={{
                                                                width: progress.total
                                                                  ? `${Math.min(100, (progress.received / progress.total) * 100)}%`
                                                                  : '100%',
                                                              }}
                                                            />
                                                          </div>
                                                        )}
                                                        <p
                                                          className={`text-[10px] ${
                                                            progress.status === 'error'
                                                              ? 'text-red-400'
                                                              : progress.status === 'done'
                                                                ? 'text-emerald-400'
                                                                : 'text-slate-500'
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
                                                  <button
                                                    className="btn-ghost !py-1 text-xs"
                                                    disabled={!custom}
                                                    onClick={() =>
                                                      setModelDraft((previous) => ({
                                                        ...previous,
                                                        [row.key]: row.default,
                                                      }))
                                                    }
                                                  >
                                                    既定に戻す
                                                  </button>
                                                </td>
                                              </tr>
                                            )
                                          })}
                                        </tbody>
                                      </table>
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )
              })}
              <button
                className="btn-primary self-start"
                onClick={() => void saveModels()}
                disabled={busy || models.length === 0 || !modelsDirty}
              >
                保存
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
