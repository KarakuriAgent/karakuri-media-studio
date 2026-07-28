import { useEffect, useRef, useState } from 'react'
import { api, formatDetail, ApiError } from '../api'
import type { FormState } from '../form'
import type { ChatMessage, PromptResult, PromptTemplate } from '../types'
import { Banner, Modal } from './ui'

interface Props {
  form: FormState
  patch: (patch: Partial<FormState>) => void
  onClose: () => void
  onSessionId: (id: string | null) => void
}

export default function ChatModal({ form, patch, onClose, onSessionId }: Props) {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PromptResult | null>(null)
  const [template, setTemplate] = useState<PromptTemplate>(form.promptTemplate)
  const scroller = useRef<HTMLDivElement>(null)

  const startSession = async (promptTemplate: PromptTemplate) => {
    setBusy(true)
    setError(null)
    try {
      const session = await api.createChatSession({
        mode: form.mode,
        video_workflow: form.videoWorkflow,
        loras: form.loras.map(
          ({ lora_name, trigger_word, strength, display_name }) => ({
            lora_name,
            trigger_word,
            strength,
            display_name,
          }),
        ),
        trigger_text: form.triggerText,
        duration: form.duration,
        image_prompt_draft: form.imagePrompt,
        video_prompt_draft: form.videoPrompt,
        prompt_template: promptTemplate,
        start_image_path: form.mode === 'i2v' ? form.sourceImage || null : null,
      })
      setSessionId(session.id)
      onSessionId(session.id)
      // messages[0] is the system prompt: never shown (SPEC §8).
      setMessages(session.messages.filter((message) => message.role !== 'system'))
      setResult(null)
    } catch (caught) {
      setError(
        caught instanceof ApiError ? formatDetail(caught.detail) : String(caught),
      )
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void startSession(template)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight })
  }, [messages, busy])

  const send = async () => {
    const content = draft.trim()
    if (!content || !sessionId || busy) return
    setDraft('')
    setError(null)
    setBusy(true)
    const ts = new Date().toISOString()
    setMessages((previous) => [...previous, { role: 'user', content, ts }])
    try {
      const reply = await api.sendChatMessage(sessionId, content)
      setMessages((previous) => [
        ...previous,
        { role: 'assistant', content: reply.content, ts: new Date().toISOString() },
      ])
      if (reply.result) setResult(reply.result)
    } catch (caught) {
      // 502 = grok CLI failure: show its detail verbatim (SPEC §8).
      setError(
        caught instanceof ApiError ? formatDetail(caught.detail) : String(caught),
      )
    } finally {
      setBusy(false)
    }
  }

  const applyToForm = () => {
    if (!result) return
    const changes: Partial<FormState> = {}
    if (result.image_prompt != null) changes.imagePrompt = result.image_prompt
    if (result.video_prompt != null) changes.videoPrompt = result.video_prompt
    patch(changes)
    onClose()
  }

  return (
    <Modal title="Grok プロンプト作成" onClose={onClose} wide>
      <div className="flex h-[70vh] flex-col gap-3">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-400">プロンプトテンプレート</span>
          <div className="flex rounded-md border border-ink-600 bg-ink-800 p-0.5">
            {(['natural', 'tagged'] as PromptTemplate[]).map((value) => (
              <button
                key={value}
                className={`rounded px-2 py-1 ${
                  template === value
                    ? 'bg-accent-500 text-white'
                    : 'text-slate-400 hover:bg-ink-700'
                }`}
                disabled={busy}
                onClick={() => {
                  setTemplate(value)
                  patch({ promptTemplate: value })
                  // The template is baked into the system prompt: restart.
                  void startSession(value)
                }}
              >
                {value === 'natural' ? '自然文' : 'タグ形式'}
              </button>
            ))}
          </div>
          <span className="text-slate-500">
            切り替えると会話をやり直します
          </span>
        </div>

        {error && <Banner onClose={() => setError(null)}>{error}</Banner>}

        <div
          ref={scroller}
          className="flex-1 space-y-3 overflow-y-auto rounded-md border border-ink-600 bg-ink-900 p-3"
        >
          {messages.length === 0 && !busy && (
            <p className="text-xs text-slate-500">
              作りたいものをひとこと入力してください（例: 「かおりが楽しそうにダンスをしている」）。
              不足があれば Grok が質問で掘り下げます。
            </p>
          )}
          {messages.map((message, index) => (
            <div
              key={index}
              className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[80%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                  message.role === 'user'
                    ? 'bg-accent-500/20 text-slate-100'
                    : 'bg-ink-700 text-slate-200'
                }`}
              >
                {message.content}
              </div>
            </div>
          ))}
          {busy && <p className="text-xs text-slate-500">Grok が考えています…</p>}
        </div>

        {result && (
          <div className="card border-accent-600/60 p-3">
            <h4 className="mb-2 text-xs font-semibold text-accent-400">
              プロンプトプレビュー
            </h4>
            <div className="space-y-2 text-xs">
              {result.image_prompt != null && (
                <div>
                  <p className="text-slate-400">画像プロンプト</p>
                  <p className="whitespace-pre-wrap text-slate-200">
                    {result.image_prompt}
                  </p>
                </div>
              )}
              {result.video_prompt != null && (
                <div>
                  <p className="text-slate-400">動画プロンプト</p>
                  <p className="whitespace-pre-wrap text-slate-200">
                    {result.video_prompt}
                  </p>
                </div>
              )}
              {result.notes && (
                <p className="text-slate-500 whitespace-pre-wrap">{result.notes}</p>
              )}
            </div>
            <div className="mt-3 flex gap-2">
              <button className="btn-primary text-xs" onClick={applyToForm}>
                フォームに反映
              </button>
              <button className="btn-ghost text-xs" onClick={() => setResult(null)}>
                続けて調整
              </button>
            </div>
          </div>
        )}

        <div className="flex gap-2">
          <textarea
            className="field h-16 flex-1 resize-none"
            value={draft}
            placeholder="メッセージを入力（Ctrl+Enter で送信）"
            disabled={!sessionId || busy}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                event.preventDefault()
                void send()
              }
            }}
          />
          <button
            className="btn-primary"
            disabled={!sessionId || busy || !draft.trim()}
            onClick={() => void send()}
          >
            送信
          </button>
        </div>
      </div>
    </Modal>
  )
}
