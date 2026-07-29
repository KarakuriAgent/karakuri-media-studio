import { useEffect, useState } from 'react'
import { api } from '../api'
import type {
  Asset,
  Lora,
  LoraPayload,
  LoraTarget,
  ModelFieldState,
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
}

const LORA_TARGET_LABELS: Record<LoraTarget, string> = {
  image: '画像用（Krea 2）',
  video: '動画用（LTX 2.3）',
}

const TABS = [
  ['connection', '接続 / Grok'],
  ['loras', 'LoRA 管理'],
  ['models', 'モデル'],
] as const

type Tab = (typeof TABS)[number][0]

const LORA_DATALIST_ID = 'model-lora-files'

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
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<LoraPayload>(EMPTY_LORA)
  const [editingId, setEditingId] = useState<number | null>(null)

  const loraFiles: string[] = options?.lora_files ?? []
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
  }

  useEffect(() => {
    void (async () => {
      try {
        setSettings(await api.getSettings())
      } catch (caught) {
        fail(caught)
      }
      try {
        applyModels(await api.listModels())
      } catch (caught) {
        fail(caught)
      }
      await reloadLoras()
    })()
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
        }),
      )
      setNotice('設定を保存しました')
      onChanged()
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
      applyModels(await api.putModels(modelDraft))
      setNotice('モデル名を保存しました')
      onChanged()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }

  const submitLora = async () => {
    setBusy(true)
    setError(null)
    try {
      if (editingId == null) await api.createLora(draft)
      else await api.updateLora(editingId, draft)
      setDraft(EMPTY_LORA)
      setEditingId(null)
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

  const modelsDirty = models.some((row) => (modelDraft[row.key] ?? '') !== row.value)

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
                {loras.map((lora) => (
                  <div key={lora.id} className="flex items-center gap-2 p-2 text-xs">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-slate-200">{lora.display_name}</p>
                      <p className="truncate text-slate-500">
                        <span className="mr-1.5 rounded border border-ink-600 px-1 py-px text-[10px] text-slate-400">
                          {LORA_TARGET_LABELS[lora.target ?? 'image']}
                        </span>
                        {lora.lora_name}
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
                        setDraft({
                          display_name: lora.display_name,
                          lora_name: lora.lora_name,
                          trigger_word: lora.trigger_word,
                          default_strength: lora.default_strength,
                          default_audio: lora.default_audio,
                          sort_order: lora.sort_order,
                          target: lora.target ?? 'image',
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
                ))}
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
                  <div className="col-span-2">
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
                      {(['image', 'video'] as LoraTarget[]).map((value) => (
                        <option key={value} value={value}>
                          {LORA_TARGET_LABELS[value]}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-[11px] text-slate-500">
                      画像用は Krea 2 の画像生成に、動画用は LTX 2.3 の動画生成に挿入されます。
                    </p>
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
                    <button
                      className="btn-ghost text-xs"
                      onClick={() => {
                        setEditingId(null)
                        setDraft(EMPTY_LORA)
                      }}
                    >
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
              {loraFiles.length > 0 && (
                <datalist id={LORA_DATALIST_ID}>
                  {loraFiles.map((file) => (
                    <option key={file} value={file} />
                  ))}
                </datalist>
              )}
              {models.length === 0 && (
                <p className="text-xs text-slate-500">読み込み中…</p>
              )}
              {models.length > 0 && (
                <div className="card overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead className="text-left text-slate-500">
                      <tr className="border-b border-ink-600">
                        <th className="p-2 font-medium">ワークフロー</th>
                        <th className="p-2 font-medium">ノード</th>
                        <th className="p-2 font-medium">既定値</th>
                        <th className="p-2 font-medium">使用する値</th>
                        <th className="p-2" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-ink-600">
                      {models.map((row) => {
                        const value = modelDraft[row.key] ?? ''
                        const changed = value !== row.value
                        const custom = value !== row.default
                        return (
                          <tr
                            key={row.key}
                            className={changed ? 'bg-accent-500/10' : undefined}
                          >
                            <td className="p-2 align-top text-slate-400">
                              {row.workflow_label || row.workflow_id}
                            </td>
                            <td className="p-2 align-top">
                              <p className="text-slate-200">{row.title || row.key}</p>
                              <p className="text-slate-600">
                                {row.node_id}.{row.field} / {row.class_type}
                              </p>
                            </td>
                            <td className="max-w-[16rem] break-all p-2 align-top text-slate-500">
                              {row.default}
                            </td>
                            <td className="p-2 align-top">
                              <input
                                className="field"
                                value={value}
                                list={
                                  row.class_type === 'LoraLoaderModelOnly' &&
                                  loraFiles.length > 0
                                    ? LORA_DATALIST_ID
                                    : undefined
                                }
                                onChange={(event) =>
                                  setModelDraft((previous) => ({
                                    ...previous,
                                    [row.key]: event.target.value,
                                  }))
                                }
                              />
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
