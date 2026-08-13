import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'

import type {
  StudioEpisode,
  StudioScene,
  StudioShot,
  StudioShotStatus,
  StudioShotUpdate,
} from '../../types'
import { FieldError, Section } from '../ui'
import { NativeSelect } from '../NativeSelect'
import { Button } from '../ui/button'
import { Checkbox } from '../ui/checkbox'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { Textarea } from '../ui/textarea'
import PromptPreview from './PromptPreview'
import {
  SHOT_STATUS_CLASS,
  SHOT_STATUS_LABEL,
  WORKFLOW_OVERRIDES,
  WORKFLOW_OVERRIDE_LABEL,
  sceneOptions,
  shotFormFromShot,
  shotUpdateFromForm,
  validateShotForm,
  type ShotFormState,
} from './studio'

const STATUSES: StudioShotStatus[] = ['draft', 'ready', 'done']

/** 脚本を読むための 1 カット分の表示（台詞は blockquote 風に落とす）。 */
function ShotScript({
  shot,
  index,
  active,
  onSelect,
}: {
  shot: StudioShot
  index: number
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      className={`w-full rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
        active
          ? 'border-primary bg-primary/10'
          : 'border-border bg-surface-sunken hover:border-primary/50'
      }`}
      onClick={onSelect}
      aria-current={active ? 'true' : undefined}
    >
      <div className="flex items-center gap-2">
        <span className="tnum text-[11px] font-semibold tracking-wider text-muted-foreground">
          #{String(index + 1).padStart(2, '0')}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm text-foreground">
          {shot.title || `カット ${index + 1}`}
        </span>
        <span className={`chip !px-1.5 !py-0 text-[11px] ${SHOT_STATUS_CLASS[shot.status]}`}>
          {SHOT_STATUS_LABEL[shot.status]}
        </span>
        <span className="tnum shrink-0 text-[11px] text-muted-foreground">
          {shot.duration_seconds}s
        </span>
      </div>

      {shot.purpose && (
        <p className="mt-1 text-[11px] italic text-muted-foreground">{shot.purpose}</p>
      )}
      {shot.action && (
        <p className="mt-1 whitespace-pre-wrap text-xs text-foreground/85">{shot.action}</p>
      )}
      {shot.dialogue && (
        <blockquote className="mt-1.5 border-l-2 border-primary/60 pl-2 text-xs text-foreground">
          <span className="whitespace-pre-wrap">{shot.dialogue}</span>
        </blockquote>
      )}
      {shot.camera && (
        <p className="mt-1.5 text-[11px] uppercase tracking-wide text-sky-400">
          CAM: {shot.camera}
        </p>
      )}
    </button>
  )
}

function Field({
  id,
  label,
  value,
  onChange,
  rows,
  placeholder,
  error,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  rows?: number
  placeholder?: string
  error?: string
}) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      {rows ? (
        <Textarea
          id={id}
          className="resize-y"
          rows={rows}
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <Input
          id={id}
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      <FieldError message={error} />
    </div>
  )
}

/**
 * 脚本タブ: 左に読み物としての脚本、右に選んだカットの編集フォーム。
 *
 * 保存は明示ボタンだけ（打つそばから PATCH を投げない）。
 */
export default function ScriptView({
  shots,
  episodes,
  scenes,
  aspectRatios,
  selectedShot,
  onSelectShot,
  onSave,
  onDelete,
  busy,
}: {
  shots: StudioShot[]
  episodes: StudioEpisode[]
  scenes: StudioScene[]
  /** 生成フォームと同じアスペクト比の候補（空なら自由入力に落ちる）。 */
  aspectRatios: string[]
  selectedShot: StudioShot | null
  onSelectShot: (id: string) => void
  onSave: (id: string, patch: StudioShotUpdate) => void
  onDelete: (id: string) => void
  busy: boolean
}) {
  const [form, setForm] = useState<ShotFormState | null>(
    selectedShot ? shotFormFromShot(selectedShot) : null,
  )
  const [errors, setErrors] = useState<Record<string, string>>({})

  // 選択が変わった / サーバー側の値が変わったら編集中の内容を入れ直す。
  // 依存を id と updated_at にしてあるのは、Take の進捗でプロジェクトを取り直す
  // たびに（値は同じなのに）入力途中が消えるのを避けるため。
  const shotId = selectedShot?.id ?? null
  const shotUpdatedAt = selectedShot?.updated_at ?? null
  useEffect(() => {
    setForm(selectedShot ? shotFormFromShot(selectedShot) : null)
    setErrors({})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shotId, shotUpdatedAt])

  const patch = (changes: Partial<ShotFormState>) =>
    setForm((previous) => (previous ? { ...previous, ...changes } : previous))

  const sceneChoices = sceneOptions({ episodes, scenes })

  const save = () => {
    if (!selectedShot || !form) return
    const problems = validateShotForm(form)
    setErrors(problems)
    if (Object.keys(problems).length > 0) return
    onSave(selectedShot.id, shotUpdateFromForm(form))
  }

  return (
    <div className="grid min-h-0 gap-3 lg:grid-cols-2">
      <div className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          脚本
        </h3>
        {shots.length === 0 && (
          <p className="rounded-md border border-border bg-surface-sunken px-3 py-6 text-center text-xs text-muted-foreground">
            左のレールの「カットを追加」でカットを作ってください
          </p>
        )}
        {shots.map((shot, index) => (
          <ShotScript
            key={shot.id}
            shot={shot}
            index={index}
            active={shot.id === selectedShot?.id}
            onSelect={() => onSelectShot(shot.id)}
          />
        ))}
      </div>

      <div>
        {!selectedShot || !form ? (
          <p className="rounded-md border border-border bg-surface-sunken px-3 py-6 text-center text-xs text-muted-foreground">
            カットを選ぶとここで編集できます
          </p>
        ) : (
          <Section title="カットの編集">
            <div className="space-y-3">
              <Field
                id="studio-shot-title"
                label="タイトル"
                value={form.title}
                onChange={(value) => patch({ title: value })}
              />
              <Field
                id="studio-shot-purpose"
                label="物語上の目的"
                value={form.purpose}
                rows={2}
                placeholder="このカットで何が進むのか"
                onChange={(value) => patch({ purpose: value })}
              />
              <Field
                id="studio-shot-action"
                label="アクション"
                value={form.action}
                rows={3}
                onChange={(value) => patch({ action: value })}
              />
              <Field
                id="studio-shot-dialogue"
                label="台詞"
                value={form.dialogue}
                rows={3}
                placeholder="投入時に MiniMax H3 の <d>[Language] …</d> へ入ります"
                onChange={(value) => patch({ dialogue: value })}
              />

              <div className="grid gap-3 sm:grid-cols-2">
                <Field
                  id="studio-shot-soundscape"
                  label="効果音・環境音"
                  value={form.soundscape}
                  onChange={(value) => patch({ soundscape: value })}
                />
                <Field
                  id="studio-shot-bgm"
                  label="BGM"
                  value={form.bgm}
                  onChange={(value) => patch({ bgm: value })}
                />
                <Field
                  id="studio-shot-camera"
                  label="カメラ"
                  value={form.camera}
                  placeholder="slow dolly in, eye level"
                  onChange={(value) => patch({ camera: value })}
                />
                <div className="space-y-1">
                  <Label htmlFor="studio-shot-duration">尺（秒）</Label>
                  <Input
                    id="studio-shot-duration"
                    className="tnum"
                    type="number"
                    min={1}
                    max={15}
                    step={0.5}
                    value={form.duration_seconds}
                    onChange={(event) =>
                      patch({ duration_seconds: event.target.value })
                    }
                  />
                  <FieldError message={errors.duration_seconds} />
                </div>
              </div>

              <Field
                id="studio-shot-prompt"
                label="生成プロンプト（@素材名 で World Bible を参照）"
                value={form.prompt}
                rows={5}
                onChange={(value) => patch({ prompt: value })}
                error={errors.prompt}
              />

              <div className="flex flex-wrap items-center gap-3">
                <div className="space-y-1">
                  <Label htmlFor="studio-shot-status">状態</Label>
                  <NativeSelect
                    id="studio-shot-status"
                    value={form.status}
                    onChange={(event) =>
                      patch({ status: event.target.value as StudioShotStatus })
                    }
                  >
                    {STATUSES.map((status) => (
                      <option key={status} value={status}>
                        {SHOT_STATUS_LABEL[status]}
                      </option>
                    ))}
                  </NativeSelect>
                </div>
                <div className="mt-4 flex items-center gap-2">
                  <Checkbox
                    id="studio-shot-carry-over"
                    checked={form.carry_over_end_frame}
                    onCheckedChange={(checked) =>
                      patch({ carry_over_end_frame: checked === true })
                    }
                  />
                  <Label
                    htmlFor="studio-shot-carry-over"
                    className="cursor-pointer text-foreground/85"
                  >
                    直前カットのラストフレームを開始フレームに使う
                  </Label>
                </div>
              </div>

              <div className="space-y-3 rounded-md border border-border bg-surface-sunken p-2">
                <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  生成設定
                </h4>
                <div className="space-y-1">
                  <Label htmlFor="studio-shot-scene">所属する場</Label>
                  <NativeSelect
                    id="studio-shot-scene"
                    value={form.scene_id}
                    onChange={(event) => patch({ scene_id: event.target.value })}
                  >
                    <option value="">未分類</option>
                    {sceneChoices.map((choice) => (
                      <option key={choice.id} value={choice.id}>
                        {choice.label}
                      </option>
                    ))}
                  </NativeSelect>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label htmlFor="studio-shot-aspect">アスペクト比</Label>
                    {aspectRatios.length > 0 ? (
                      <NativeSelect
                        id="studio-shot-aspect"
                        value={form.aspect_ratio}
                        onChange={(event) =>
                          patch({ aspect_ratio: event.target.value })
                        }
                      >
                        <option value="">プロジェクト設定に従う</option>
                        {!aspectRatios.includes(form.aspect_ratio) &&
                          form.aspect_ratio && (
                            <option value={form.aspect_ratio}>
                              {form.aspect_ratio}
                            </option>
                          )}
                        {aspectRatios.map((ratio) => (
                          <option key={ratio} value={ratio}>
                            {ratio}
                          </option>
                        ))}
                      </NativeSelect>
                    ) : (
                      <Input
                        id="studio-shot-aspect"
                        value={form.aspect_ratio}
                        placeholder="16:9 (Widescreen)（空欄でプロジェクト設定）"
                        onChange={(event) =>
                          patch({ aspect_ratio: event.target.value })
                        }
                      />
                    )}
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="studio-shot-megapixels">
                      解像度（メガピクセル）
                    </Label>
                    <Input
                      id="studio-shot-megapixels"
                      className="tnum"
                      type="number"
                      step="0.05"
                      min="0.1"
                      value={form.megapixels}
                      placeholder="空欄でプロジェクト設定"
                      onChange={(event) => patch({ megapixels: event.target.value })}
                    />
                    <FieldError message={errors.megapixels} />
                    <p className="text-[11px] text-muted-foreground">
                      この Shot だけの指定。空欄ならプロジェクトの「画質」
                      （ヘッダーのアスペクト比・メガピクセル）に従います。
                    </p>
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="studio-shot-seed">シード</Label>
                    <Input
                      id="studio-shot-seed"
                      className="tnum"
                      type="number"
                      step="1"
                      value={form.seed}
                      placeholder="空欄で毎回ランダム"
                      onChange={(event) => patch({ seed: event.target.value })}
                    />
                    <FieldError message={errors.seed} />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="studio-shot-workflow">
                      ワークフローの強制指定
                    </Label>
                    <NativeSelect
                      id="studio-shot-workflow"
                      value={form.workflow_override}
                      onChange={(event) =>
                        patch({ workflow_override: event.target.value })
                      }
                    >
                      <option value="">自動（素材と引き継ぎから決める）</option>
                      {WORKFLOW_OVERRIDES.map((value) => (
                        <option key={value} value={value}>
                          {WORKFLOW_OVERRIDE_LABEL[value]}
                        </option>
                      ))}
                    </NativeSelect>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Button onClick={save} disabled={busy}>
                  保存
                </Button>
                <Button
                  variant="destructive"
                  className="ml-auto"
                  onClick={() => onDelete(selectedShot.id)}
                  disabled={busy}
                >
                  <Trash2 />
                  このカットを削除
                </Button>
              </div>

              <PromptPreview shot={selectedShot} />
            </div>
          </Section>
        )}
      </div>
    </div>
  )
}
