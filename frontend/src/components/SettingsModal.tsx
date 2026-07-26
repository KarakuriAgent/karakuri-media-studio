import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Asset, Lora, LoraPayload, Options, Settings } from '../types'
import { Banner, Modal } from './ui'

const EMPTY_LORA: LoraPayload = {
  display_name: '',
  lora_name: '',
  trigger_word: '',
  default_strength: 1,
  default_audio: null,
  sort_order: 0,
}

export default function SettingsModal({
  options,
  onClose,
  onChanged,
}: {
  options: Options | null
  onClose: () => void
  onChanged: () => void
}) {
  const [tab, setTab] = useState<'connection' | 'loras'>('connection')
  const [settings, setSettings] = useState<Settings | null>(null)
  const [loras, setLoras] = useState<Lora[]>([])
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

  useEffect(() => {
    void (async () => {
      try {
        setSettings(await api.getSettings())
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

  return (
    <Modal title="設定" onClose={onClose} wide>
      <div className="mb-3 flex gap-1 rounded-lg border border-ink-600 bg-ink-900 p-1 text-xs">
        {(
          [
            ['connection', '接続設定'],
            ['loras', 'LoRA 管理'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            className={`flex-1 rounded px-2 py-1.5 ${
              tab === key ? 'bg-accent-500 text-white' : 'text-slate-400 hover:bg-ink-700'
            }`}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
      {notice && (
        <Banner tone="info" onClose={() => setNotice(null)}>
          {notice}
        </Banner>
      )}

      {tab === 'connection' && (
        <div className="mt-3 flex flex-col gap-3">
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
                  onChange={(event) => update({ comfy_api_key: event.target.value })}
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="label">grok コマンド</label>
                  <input
                    className="field"
                    value={settings.grok_command}
                    onChange={(event) => update({ grok_command: event.target.value })}
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
        <div className="mt-3 flex flex-col gap-4">
          <div className="card divide-y divide-ink-600">
            {loras.length === 0 && (
              <p className="p-3 text-xs text-slate-500">登録がありません</p>
            )}
            {loras.map((lora) => (
              <div key={lora.id} className="flex items-center gap-2 p-2 text-xs">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-slate-200">{lora.display_name}</p>
                  <p className="truncate text-slate-500">{lora.lora_name}</p>
                  <p className="truncate text-slate-600">
                    trigger: {lora.trigger_word} / strength: {lora.default_strength}
                    {lora.default_audio ? ` / audio: ${lora.default_audio}` : ''}
                  </p>
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
                {loraFiles.length > 0 ? (
                  <select
                    className="field"
                    value={draft.lora_name}
                    onChange={(event) =>
                      setDraft({ ...draft, lora_name: event.target.value })
                    }
                  >
                    <option value="">（選択）</option>
                    {draft.lora_name && !loraFiles.includes(draft.lora_name) && (
                      <option value={draft.lora_name}>{draft.lora_name}</option>
                    )}
                    {loraFiles.map((file) => (
                      <option key={file} value={file}>
                        {file}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className="field"
                    placeholder="ComfyUI 未接続のため手入力"
                    value={draft.lora_name}
                    onChange={(event) =>
                      setDraft({ ...draft, lora_name: event.target.value })
                    }
                  />
                )}
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
                    setDraft({ ...draft, default_audio: event.target.value || null })
                  }
                >
                  <option value="">（なし）</option>
                  {draft.default_audio &&
                    !audioAssets.some((asset) => asset.url === draft.default_audio) && (
                      <option value={draft.default_audio}>{draft.default_audio}</option>
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
                    setDraft({ ...draft, sort_order: Number(event.target.value) || 0 })
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
    </Modal>
  )
}
