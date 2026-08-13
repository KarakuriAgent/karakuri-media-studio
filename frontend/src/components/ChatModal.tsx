import { useEffect, useRef, useState } from 'react'
import { Loader2, Send } from 'lucide-react'
import { api, formatDetail, ApiError } from '../api'
import { audioSupports, hiddenFields, type FormState } from '../form'
import type { ChatMessage, Options, PromptResult } from '../types'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Banner, Modal } from './ui'

interface Props {
  form: FormState
  patch: (patch: Partial<FormState>) => void
  /** 音声モードで、選択中のワークフローが読むフィールドを知るために使う。 */
  options: Options | null
  onClose: () => void
  onSessionId: (id: string | null) => void
}

export default function ChatModal({
  form,
  patch,
  options,
  onClose,
  onSessionId,
}: Props) {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<PromptResult | null>(null)
  const scroller = useRef<HTMLDivElement>(null)

  // 音声モードは会話の中身も反映先も別物（シーンもカメラも LoRA も無い）。
  const audio = form.mode === 'audio'
  const audioWorkflow =
    options?.audio_workflows.find((item) => item.id === form.audioWorkflow) ?? null
  const videoWorkflow =
    options?.video_workflows?.find((item) => item.id === form.videoWorkflow) ?? null
  const imageWorkflow =
    options?.image_workflows?.find((item) => item.id === form.imageWorkflow) ?? null
  // フォームに**出ていない**欄の値は送らない: 使われないまま残っている値
  // （モードを切り替える前の開始フレームなど）を渡すと、Grok がそれを前提に
  // 書いてしまう。判定はフォーム本体と同じ :func:`hiddenFields`。
  const hidden = hiddenFields(form.mode, videoWorkflow, imageWorkflow)

  const startSession = async () => {
    setBusy(true)
    setError(null)
    try {
      const session = await api.createChatSession({
        mode: form.mode,
        video_workflow: form.videoWorkflow,
        // decides which model family's IMAGE PROMPT SPEC the system prompt uses
        image_workflow: form.imageWorkflow,
        // …and which AUDIO PROMPT SPEC, in mode 'audio'
        audio_workflow: form.audioWorkflow,
        // LoRA はそのステージが走り、かつ LoRA チェーンを持つワークフローの
        // ときだけ効く（挿せないワークフローに渡すとトリガーワードだけが
        // プロンプトに紛れ込む）。
        loras: hidden.loras
          ? []
          : form.loras.map(
              ({ lora_name, trigger_word, strength, display_name }) => ({
                lora_name,
                trigger_word,
                strength,
                display_name,
              }),
            ),
        trigger_text: hidden.trigger ? '' : form.triggerText,
        video_loras: hidden.videoLoras
          ? []
          : form.videoLoras.map(
              ({ lora_name, trigger_word, strength, display_name }) => ({
                lora_name,
                trigger_word,
                strength,
                display_name,
              }),
            ),
        video_trigger_text: hidden.videoTrigger ? '' : form.videoTriggerText,
        duration: audio ? form.audioDuration : form.duration,
        image_prompt_draft: form.imagePrompt,
        video_prompt_draft: form.videoPrompt,
        audio_prompt_draft: form.audioPrompt,
        lyrics_draft: form.lyrics,
        prompt_template: form.promptTemplate,
        // 入力画像は「欄が出ているかどうか」で決める: i2v の開始フレームだけ
        // でなく、編集系の画像ワークフロー（qwen-image-edit など）の編集元も
        // 見せたい。
        start_image_path: hidden.startImage ? null : form.sourceImage || null,
        end_image_path: hidden.endImage ? null : form.endImage || null,
        reference_images: hidden.references ? [] : form.referenceImages,
        reference_videos: hidden.references ? [] : form.referenceVideos,
        reference_audios: hidden.references ? [] : form.referenceAudios,
        aspect_ratio: hidden.resolution ? null : form.aspectRatio,
        megapixels: hidden.resolution ? null : form.megapixels,
        negative_prompt: hidden.negative ? null : form.negativePrompt,
        // 音声モードのフォームの現在値（選択中のモデルが読む項目だけ）
        audio_category:
          audio && audioSupports(audioWorkflow, 'audio_category')
            ? form.audioCategory
            : null,
        bpm: audio && audioSupports(audioWorkflow, 'bpm') ? form.bpm : null,
        keyscale:
          audio && audioSupports(audioWorkflow, 'keyscale') ? form.keyscale : null,
        language:
          audio && audioSupports(audioWorkflow, 'language') ? form.language : null,
        negative_tags_draft:
          audio && audioSupports(audioWorkflow, 'negative_tags')
            ? form.negativeTags
            : null,
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
    void startSession()
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
    if (result.audio_prompt != null) changes.audioPrompt = result.audio_prompt
    // モデル固有の提案は、選択中のワークフローが実際に読む項目だけ反映する
    // （Stable Audio に歌詞やキーを書き込んでも使われないので入れない）。
    if (result.lyrics != null && audioSupports(audioWorkflow, 'lyrics')) {
      changes.lyrics = result.lyrics
    }
    if (result.bpm != null && audioSupports(audioWorkflow, 'bpm')) {
      changes.bpm = result.bpm
    }
    if (result.keyscale != null && audioSupports(audioWorkflow, 'keyscale')) {
      changes.keyscale = result.keyscale
    }
    if (result.language != null && audioSupports(audioWorkflow, 'language')) {
      changes.language = result.language
    }
    if (
      result.negative_tags != null &&
      audioSupports(audioWorkflow, 'negative_tags')
    ) {
      changes.negativeTags = result.negative_tags
    }
    patch(changes)
    onClose()
  }

  return (
    <Modal title="Grok プロンプト作成" onClose={onClose} wide>
      <div className="flex h-[70vh] flex-col gap-3">
        {!audio && form.mode !== 'image_only' && (
          <p className="text-xs text-muted-foreground">
            MiniMax H3 は公式リライト形式（フィールド見出し / [Shot N]）で書き直します。
          </p>
        )}

        {error && <Banner onClose={() => setError(null)}>{error}</Banner>}

        <div
          ref={scroller}
          className="flex-1 space-y-3 overflow-y-auto rounded-lg border border-border bg-surface-sunken p-3"
        >
          {messages.length === 0 && !busy && (
            <p className="text-xs text-muted-foreground">
              {audio
                ? '作りたい音をひとこと入力してください（例: 「夜の街を歩くときの lo-fi な曲」）。不足があれば Grok が質問で掘り下げます。'
                : '作りたいものをひとこと入力してください（例: 「かおりが楽しそうにダンスをしている」）。不足があれば Grok が質問で掘り下げます。'}
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
                    ? 'bg-primary/20 text-foreground'
                    : 'bg-card text-foreground/90'
                }`}
              >
                {message.content}
              </div>
            </div>
          ))}
          {busy && (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              Grok が考えています…
            </p>
          )}
        </div>

        {result && (
          <div className="rounded-lg border border-primary/60 bg-card p-3 shadow-elevation-1">
            <h4 className="mb-2 text-xs font-semibold text-primary">
              プロンプトプレビュー
            </h4>
            <div className="space-y-2 text-xs">
              {result.image_prompt != null && (
                <div>
                  <p className="text-muted-foreground">画像プロンプト</p>
                  <p className="whitespace-pre-wrap text-foreground/90">
                    {result.image_prompt}
                  </p>
                </div>
              )}
              {result.video_prompt != null && (
                <div>
                  <p className="text-muted-foreground">動画プロンプト</p>
                  <p className="whitespace-pre-wrap text-foreground/90">
                    {result.video_prompt}
                  </p>
                </div>
              )}
              {result.audio_prompt != null && (
                <div>
                  <p className="text-muted-foreground">音声プロンプト</p>
                  <p className="whitespace-pre-wrap text-foreground/90">
                    {result.audio_prompt}
                  </p>
                </div>
              )}
              {result.lyrics != null && audioSupports(audioWorkflow, 'lyrics') && (
                <div>
                  <p className="text-muted-foreground">歌詞</p>
                  <p className="whitespace-pre-wrap font-mono text-foreground/90">
                    {result.lyrics}
                  </p>
                </div>
              )}
              {result.negative_tags != null &&
                audioSupports(audioWorkflow, 'negative_tags') && (
                  <div>
                    <p className="text-muted-foreground">除外タグ</p>
                    <p className="whitespace-pre-wrap text-foreground/90">
                      {result.negative_tags}
                    </p>
                  </div>
                )}
              {audio && (result.bpm != null || result.keyscale || result.language) && (
                <p className="text-muted-foreground">
                  {[
                    result.bpm != null && audioSupports(audioWorkflow, 'bpm')
                      ? `BPM ${result.bpm}`
                      : null,
                    result.keyscale && audioSupports(audioWorkflow, 'keyscale')
                      ? result.keyscale
                      : null,
                    result.language && audioSupports(audioWorkflow, 'language')
                      ? `言語 ${result.language}`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(' / ')}
                </p>
              )}
              {result.notes && (
                <p className="whitespace-pre-wrap text-muted-foreground">{result.notes}</p>
              )}
            </div>
            <div className="mt-3 flex gap-2">
              <Button size="sm" onClick={applyToForm}>
                フォームに反映
              </Button>
              <Button variant="outline" size="sm" onClick={() => setResult(null)}>
                続けて調整
              </Button>
            </div>
          </div>
        )}

        <div className="flex gap-2">
          <Textarea
            className="h-16 flex-1 resize-none"
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
          <Button
            className="h-16"
            disabled={!sessionId || busy || !draft.trim()}
            onClick={() => void send()}
          >
            <Send />
            送信
          </Button>
        </div>
      </div>
    </Modal>
  )
}
