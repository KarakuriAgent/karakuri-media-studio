import { useEffect, useState } from 'react'
import { EyeOff, History, Trash2 } from 'lucide-react'

import { api } from '../../api'
import type {
  ComfyTarget,
  StudioCapabilities,
  StudioProjectDetail,
  StudioProjectUpdate,
} from '../../types'
import { FieldError, Section } from '../ui'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import { Switch } from '../ui/switch'
import { Textarea } from '../ui/textarea'
import { projectSummary, validateProjectForm, type ProjectFormState } from './studio'

function Metric({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-sunken px-3 py-2 shadow-elevation-1">
      <div className="tnum text-lg font-semibold text-foreground">{value}</div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
    </div>
  )
}

/** 概要タブ: プロジェクトの基本情報の編集と、規模のサマリー。 */
export default function OverviewView({
  detail,
  onSave,
  onDelete,
  onOpenRevisions,
  busy,
  comfyTarget = null,
}: {
  detail: StudioProjectDetail
  onSave: (patch: StudioProjectUpdate) => void
  onDelete: () => void
  /** 変更履歴（リビジョン）のモーダルを開く。 */
  onOpenRevisions: () => void
  busy: boolean
  /** いまの ComfyUI 接続先（変わったらケーパビリティを聞き直す）。 */
  comfyTarget?: ComfyTarget | null
}) {
  const [form, setForm] = useState<ProjectFormState>({
    name: detail.name,
    code: detail.code,
    synopsis: detail.synopsis,
    world_notes: detail.world_notes,
    auto_translate: detail.auto_translate,
    latent_continuity: detail.latent_continuity,
    nsfw: detail.nsfw,
  })
  const [errors, setErrors] = useState<Record<string, string>>({})
  // 接続先にカスタムノードが入っているか（null = まだ聞いていない）。
  const [capabilities, setCapabilities] = useState<StudioCapabilities | null>(null)

  // ラテント連続性は接続先の ComfyUI 頼みなので、開いたときと**接続先を
  // 切り替えたとき**に聞き直す。聞けなかったときはトグルを塞がず、いま入って
  // いる値のままにしておく。
  useEffect(() => {
    let alive = true
    void api
      .getStudioCapabilities()
      .then((result) => {
        if (alive) setCapabilities(result)
      })
      .catch(() => {
        if (alive) setCapabilities(null)
      })
    return () => {
      alive = false
    }
  }, [comfyTarget])

  // プロジェクトを切り替えたら編集中の内容もそちらに揃える。
  useEffect(() => {
    setForm({
      name: detail.name,
      code: detail.code,
      synopsis: detail.synopsis,
      world_notes: detail.world_notes,
      auto_translate: detail.auto_translate,
      latent_continuity: detail.latent_continuity,
      nsfw: detail.nsfw,
    })
    setErrors({})
  }, [
    detail.id,
    detail.name,
    detail.code,
    detail.synopsis,
    detail.world_notes,
    detail.auto_translate,
    detail.latent_continuity,
    detail.nsfw,
  ])

  const summary = projectSummary(detail)
  // 接続先に入っていないと分かっていて、まだオンにしていないときだけ塞ぐ
  // （オン済みのプロジェクトはオフに戻せなければ困る）。
  const continuityDisabled =
    capabilities !== null && !capabilities.latent_continuity && !form.latent_continuity
  const patch = (changes: Partial<ProjectFormState>) =>
    setForm((previous) => ({ ...previous, ...changes }))

  const save = () => {
    const problems = validateProjectForm(form)
    setErrors(problems)
    if (Object.keys(problems).length > 0) return
    onSave(form)
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Metric value={summary.shots} label="カット" />
        <Metric value={`${Math.round(summary.totalSeconds)}s`} label="合計の尺" />
        <Metric value={summary.assets} label="World Bible の素材" />
        <Metric value={summary.takes} label="Take" />
        <Metric value={summary.selectedTakes} label="採用済み Take" />
      </div>

      <Section title="作品情報">
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="studio-project-name">作品名</Label>
              <Input
                id="studio-project-name"
                value={form.name}
                onChange={(event) => patch({ name: event.target.value })}
              />
              <FieldError message={errors.name} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="studio-project-code">作品コード（任意）</Label>
              <Input
                id="studio-project-code"
                value={form.code}
                placeholder="EP01"
                onChange={(event) => patch({ code: event.target.value })}
              />
            </div>
          </div>

          <div className="space-y-1">
            <Label htmlFor="studio-project-synopsis">あらすじ</Label>
            <Textarea
              id="studio-project-synopsis"
              className="h-24 resize-y"
              value={form.synopsis}
              onChange={(event) => patch({ synopsis: event.target.value })}
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="studio-project-notes">世界観メモ</Label>
            <Textarea
              id="studio-project-notes"
              className="h-32 resize-y"
              value={form.world_notes}
              placeholder="時代・土地・トーン・撮影の決まりごとなど"
              onChange={(event) => patch({ world_notes: event.target.value })}
            />
          </div>

          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Switch
                id="studio-project-auto-translate"
                checked={form.auto_translate}
                onCheckedChange={(checked) => patch({ auto_translate: checked })}
              />
              <Label
                htmlFor="studio-project-auto-translate"
                className="cursor-pointer text-foreground/85"
              >
                日本語プロンプトを自動で英訳して投入
              </Label>
            </div>
            <div>
              <div
                className={`flex items-center gap-2 ${
                  continuityDisabled ? 'opacity-60' : ''
                }`}
              >
                <Switch
                  id="studio-project-latent-continuity"
                  checked={form.latent_continuity}
                  disabled={continuityDisabled}
                  onCheckedChange={(checked) =>
                    patch({ latent_continuity: checked })
                  }
                />
                <Label
                  htmlFor="studio-project-latent-continuity"
                  className={`text-foreground/85 ${
                    continuityDisabled ? 'cursor-not-allowed' : 'cursor-pointer'
                  }`}
                >
                  ラテント連続性（連続カットを動きと音ごと引き継ぐ）
                </Label>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {continuityDisabled
                  ? '接続先（Comfy Cloud）では利用できません（MiniMaxH3MotionContext 系のカスタムノードが入っていません）。'
                  : 'カットの引き継ぎ（「直前カットから続ける」）が、ラストフレーム 1 枚ではなく直前カットの動画とラテントごとになります。参照素材の指定と直前カットの採用 Take が要ります。'}
              </p>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <Switch
                  id="studio-project-nsfw"
                  checked={form.nsfw}
                  onCheckedChange={(checked) => patch({ nsfw: checked })}
                />
                <Label
                  htmlFor="studio-project-nsfw"
                  className="flex cursor-pointer items-center gap-1.5 text-foreground/85"
                >
                  <EyeOff className="size-3.5" />
                  NSFW プロジェクト
                </Label>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {form.nsfw
                  ? 'このプロジェクトから投入するジョブはすべて NSFW 扱いになります。'
                  : 'このプロジェクトから投入するジョブは非 NSFW で固定されます（自動判定は走りません）。'}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button onClick={save} disabled={busy}>
              保存
            </Button>
            <Button variant="outline" onClick={onOpenRevisions} disabled={busy}>
              <History />
              変更履歴
            </Button>
            <Button
              variant="destructive"
              className="ml-auto"
              onClick={onDelete}
              disabled={busy}
            >
              <Trash2 />
              このプロジェクトを削除
            </Button>
          </div>
        </div>
      </Section>
    </div>
  )
}
