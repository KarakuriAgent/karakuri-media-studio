import { useEffect, useState } from 'react'
import { api, wsUrl } from '../api'
import { DEFAULT_FAMILY, FAMILY_LABELS, IMAGE_FAMILIES } from '../form'
import type {
  Asset,
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
  video: '動画用（LTX 2.3）',
}

/** 一覧バッジ: 動画は LTX 固定、画像はモデルファミリーまで出す。 */
function loraBadge(lora: Lora): string {
  const target = lora.target ?? 'image'
  if (target === 'video') return LORA_TARGET_LABELS.video
  const family = lora.family ?? DEFAULT_FAMILY
  return `画像用 / ${FAMILY_LABELS[family] ?? family}`
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
 */
function modelFileMap(options: Options | null): Record<string, string[]> {
  const files: Record<string, string[]> = { ...(options?.model_files ?? {}) }
  const loraKey = 'LoraLoaderModelOnly.lora_name'
  const loraFiles = options?.lora_files ?? []
  if (!files[loraKey] && loraFiles.length > 0) files[loraKey] = loraFiles
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
 * ワークフローごとにまとめる（表示順は API の並び = workflows.SPECS の順）。
 * 折りたたみが既定なので、中身が既定値から変わっていることをバッジで見せる。
 */
function groupModels(
  rows: ModelFieldState[],
  draft: Record<string, string>,
  choices: Record<string, string[]>,
): ModelGroup[] {
  const groups = new Map<string, ModelGroup>()
  for (const row of rows) {
    const id = row.workflow_id || '(unknown)'
    let group = groups.get(id)
    if (!group) {
      group = {
        id,
        label: row.workflow_label || id,
        kind: row.kind ?? 'image',
        rows: [],
        changed: 0,
        custom: 0,
      }
      groups.set(id, group)
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
  return [...groups.values()]
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
  const [loras, setLoras] = useState<Lora[]>([])
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

  const fail = (caught: unknown) =>
    setError(caught instanceof Error ? caught.message : String(caught))

  const reloadLoras = async () => {
    try {
      setLoras(await api.listLoras())
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

  useEffect(() => {
    void (async () => {
      try {
        const loaded = await api.getSettings()
        setSettings(loaded)
        setUrlDraft({ ...loaded.model_download_urls })
      } catch (caught) {
        fail(caught)
      }
      try {
        applyModels(await api.listModels())
      } catch (caught) {
        fail(caught)
      }
      await reloadDirStatus()
      try {
        // 開き直したときに進行中のダウンロードを拾い直す（WS の取りこぼし対策）
        const running = await api.listModelDownloads()
        setDownloads(
          Object.fromEntries(running.map((item) => [item.filename, item])),
        )
      } catch (caught) {
        fail(caught)
      }
      await reloadLoras()
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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

  const saveSettings = async () => {
    if (!settings) return
    setBusy(true)
    setError(null)
    try {
      setSettings(
        await api.putSettings({
          comfy_url: settings.comfy_url,
          comfy_api_key: settings.comfy_api_key,
          grok_model: settings.grok_model,
          grok_command: settings.grok_command,
          hf_token: settings.hf_token,
          civitai_api_key: settings.civitai_api_key,
          runpod_enabled: settings.runpod_enabled,
          runpod_api_key: settings.runpod_api_key,
          runpod_template_id: settings.runpod_template_id,
          runpod_gpu_type: settings.runpod_gpu_type,
          runpod_network_volume_id: settings.runpod_network_volume_id,
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
      const started = await api.downloadModel(filename, url, row.subfolder)
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
   * 手元に在るモデルでも、RunPod の Pod 用マニフェスト
   * (deploy/runpod/gen_models_manifest.py) が `model_download_urls` を見るので
   * 事前に登録できるようにしてある。空欄で保存したらキーごと消す。
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

  const saveModels = async () => {
    setBusy(true)
    setError(null)
    try {
      applyModels(await api.putModels(modelDraft, choiceDraft))
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
      if (editingId == null) await api.createLora(draft)
      else await api.updateLora(editingId, draft)
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

  const modelsDirty = models.some(
    (row) =>
      (modelDraft[row.key] ?? '') !== row.value ||
      !sameChoices(choiceDraft[row.key] ?? [], row.choices ?? []),
  )
  // 保存は全件置換 PUT なので、折りたたんでいても modelDraft は全行を持ち続ける。
  const modelGroups = groupModels(models, modelDraft, choiceDraft)
  // models ディレクトリが未設定なら（Comfy Cloud 接続などでは普通のこと）
  // ダウンロード列そのものを出さない。設定済みなら、使えない状態でも出して
  // 理由を見せる（SPEC §3.3）。
  const showDownload = dirStatus?.configured === true

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
                  <div>
                    <label className="label">ComfyUI URL</label>
                    <input
                      className="field"
                      value={settings.comfy_url}
                      onChange={(event) => update({ comfy_url: event.target.value })}
                    />
                  </div>
                  <div>
                    <label className="label">ComfyUI APIキー（任意）</label>
                    <input
                      className="field"
                      value={settings.comfy_api_key}
                      onChange={(event) =>
                        update({ comfy_api_key: event.target.value })
                      }
                    />
                  </div>
                  {/* 不足モデルの自動ダウンロード（SPEC §3.3）。保存先は環境変数
                      COMFY_MODELS_DIR だけが決めるので、未設定ならブロックごと
                      出さない（Comfy Cloud 利用などでは正常な状態）。 */}
                  {showDownload && (
                    <div className="card flex flex-col gap-2 p-3">
                      <h4 className="text-xs font-semibold text-slate-300">
                        モデル自動ダウンロード
                      </h4>
                      <div>
                        <label className="label">
                          保存先（環境変数 COMFY_MODELS_DIR）
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
                        <p className="mt-1 text-[11px] text-slate-500">
                          パスは .env の COMFY_MODELS_DIR で決まります（変更したら再起動）。
                          「モデル」タブの [DL] はここへ直接ファイルを置きます（ComfyUI の
                          再起動は不要）。
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
                  )}
                  {/* ComfyUI を RunPod の Pod で動かす構成の自動起動（SPEC §5.1）。
                      ジョブ投入の直前に疎通を確かめ、落ちていれば Pod を作って待つ。
                      Pod の停止はイメージ側の watchdog が行う。 */}
                  <div className="card flex flex-col gap-2 p-3">
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
                    <p className="text-[11px] text-slate-500">
                      ComfyUI が落ちているとき、ジョブ実行の直前に RunPod で Pod を
                      立ち上げます（起動待ちは最大 15 分）。ComfyUI URL には Pod の
                      Cloudflare Tunnel のホスト名を入れてください。イメージと手順は
                      deploy/runpod/README.md を参照。
                    </p>
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
                                update({ runpod_template_id: event.target.value })
                              }
                            />
                          </div>
                          <div>
                            <label className="label">
                              GPU 種別（gpuTypeId）
                            </label>
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
              <div className="card divide-y divide-ink-600">
                {loras.length === 0 && (
                  <p className="p-3 text-xs text-slate-500">登録がありません</p>
                )}
                {loras.map((lora) => {
                  // 取得元 URL はモデルタブと同じ `model_download_urls`（キーは
                  // ファイル名 = lora_name）に入れる。Pod 用のモデル一覧
                  // (deploy/runpod/gen_models_manifest.py) がこれを見る。登録・編集は
                  // 下のフォームで行うので、一覧では印だけ出す。
                  const savedUrl =
                    settings?.model_download_urls?.[lora.lora_name] ?? ''
                  return (
                  <div key={lora.id} className="flex items-center gap-2 p-2 text-xs">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-slate-200">{lora.display_name}</p>
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
                      <p className="truncate text-slate-600">
                        trigger: {lora.trigger_word} / strength: {lora.default_strength}
                        {lora.default_audio ? ` / audio: ${lora.default_audio}` : ''}
                      </p>
                      {/* サンプル画像: エージェントモードで Grok が出力と見比べる基準 */}
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        {lora.sample_images.map((url) => (
                          <div key={url} className="group relative">
                            <a href={url} target="_blank" rel="noreferrer">
                              <img
                                src={url}
                                alt="サンプル"
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
                    <button
                      className="btn-ghost !py-1 text-xs"
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
                      className="btn-danger !py-1 text-xs"
                      onClick={() => void removeLora(lora)}
                      disabled={busy}
                    >
                      削除
                    </button>
                  </div>
                  )
                })}
              </div>

              <div className="card p-3">
                <h4 className="mb-2 text-xs font-semibold text-slate-300">
                  {editingId == null ? 'LoRA を追加' : `LoRA を編集 (#${editingId})`}
                </h4>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="label">表示名</label>
                    <input
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
                  <p className="col-span-2 -mt-1 text-[11px] text-slate-500">
                    画像用 LoRA は同じモデルファミリーの画像ワークフローでのみ選択できます。
                    動画用は LTX 2.3 の動画生成に挿入され、ファミリーは使いません。
                  </p>
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
                    <p className="mt-1 text-[11px] text-slate-500">
                      ここではダウンロードしません。RunPod の Pod へ持っていくモデル一覧
                      （deploy/runpod/gen_models_manifest.py）に使われます。空欄で保存すると
                      登録を解除します。
                    </p>
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
          )}

          {tab === 'models' && (
            <div className="flex flex-col gap-3">
              <p className="text-xs text-slate-500">
                workflow/ 配下の各ワークフローのモデルファイル名を上書きします。空欄・既定値と同じ値は保存されません。
              </p>
              <p className="text-xs text-slate-500">
                候補リストに別のファイル名を足すと、そのスロットは生成フォーム（とエージェント）で
                <strong className="text-slate-300">実行ごとに切り替えられる</strong>
                ようになります（既定値と合わせて 2 件以上必要）。
              </p>
              <p className="text-xs text-slate-500">
                ComfyUI のファイル一覧に無いファイル名には
                <span className="mx-1 rounded border border-amber-500 px-1 py-px text-[10px] text-amber-400">
                  未検出
                </span>
                が付きます。取得元 URL は行ごとに登録でき（ファイル名ごとに設定へ保存され、
                同じファイルを使う他のワークフローの行にも共有されます）、RunPod の Pod へ
                持っていくモデル一覧にも使われます。検出済みの行では [取得元 URL] を開くと
                入力でき、空欄で保存すると登録解除です。
              </p>
              <p className="text-xs text-slate-500">
                {showDownload ? (
                  <>
                    [DL] を押すと「接続 / Grok」タブで設定した models ディレクトリの
                    所定の場所へ実ファイルをダウンロードします。
                  </>
                ) : (
                  // 環境変数が無い（Comfy Cloud 利用など）のは正常な状態なので
                  // 警告は出さず、使いたい人向けの案内だけ添える。
                  <>
                    自動ダウンロードを使う場合は .env に COMFY_MODELS_DIR
                    （ComfyUI の models ディレクトリ）を設定して再起動してください
                    （未設定でも取得元 URL の登録はできます）。
                  </>
                )}
              </p>
              {/* 設定したのに使えない（見つからない / 書けない）ときだけ警告する */}
              {showDownload && !dirStatusMessage(dirStatus).ok && (
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
                const groups = modelGroups.filter((group) => group.kind === kind)
                if (groups.length === 0) return null
                return (
                  <div key={kind} className="flex flex-col gap-2">
                    <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                      {kindLabel}
                    </h4>
                    {groups.map((group) => {
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
                                    const missing = isMissing(row, modelFiles, value)
                                    const progress = downloads[value]
                                    const downloading =
                                      progress?.status === 'downloading'
                                    const dirReady = dirStatusMessage(dirStatus).ok
                                    const url = (urlDraft[value] ?? '').trim()
                                    // 検出済みの行でも取得元 URL は登録できる（Pod 用の
                                    // マニフェスト向け）。表がうるさくならないよう、
                                    // 既定では畳んでおく。
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
                                            {/* 未検出の行は URL 欄をそのまま出す。
                                                検出済みの行は [取得元 URL] で開いた
                                                ときだけ出す。[DL] は models
                                                ディレクトリが使えるときだけ描画し、
                                                それ以外では URL 登録だけにする。 */}
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
                                                    : '取得元 URL を登録する（RunPod の Pod へ持っていくモデル一覧に使われます）'
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
                                                {missing && dirReady ? (
                                                  <button
                                                    className="btn-ghost !py-1 text-xs"
                                                    disabled={
                                                      busy ||
                                                      downloading ||
                                                      !value ||
                                                      !url
                                                    }
                                                    title={`${row.subfolder || 'models 直下'} に保存します`}
                                                    onClick={() =>
                                                      void startDownload(row, value)
                                                    }
                                                  >
                                                    DL
                                                  </button>
                                                ) : (
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
                                              </div>
                                            )}
                                            {missing && dirReady && (
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
