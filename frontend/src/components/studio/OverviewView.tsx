import { useEffect, useState } from 'react'
import { api } from '../../api'
import type { StudioCapabilities, StudioProjectDetail, StudioProjectUpdate } from '../../types'
import { FieldError, Section } from '../ui'
import { projectSummary, validateProjectForm, type ProjectFormState } from './studio'

function Metric({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="rounded-md border border-ink-600 bg-ink-900/60 px-3 py-2">
      <div className="text-lg font-semibold text-slate-100">{value}</div>
      <div className="text-[11px] text-slate-500">{label}</div>
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
}: {
  detail: StudioProjectDetail
  onSave: (patch: StudioProjectUpdate) => void
  onDelete: () => void
  /** 変更履歴（リビジョン）のモーダルを開く。 */
  onOpenRevisions: () => void
  busy: boolean
}) {
  const [form, setForm] = useState<ProjectFormState>({
    name: detail.name,
    code: detail.code,
    synopsis: detail.synopsis,
    world_notes: detail.world_notes,
    auto_translate: detail.auto_translate,
    latent_continuity: detail.latent_continuity,
  })
  const [errors, setErrors] = useState<Record<string, string>>({})
  // 接続先にカスタムノードが入っているか（null = まだ聞いていない）。
  const [capabilities, setCapabilities] = useState<StudioCapabilities | null>(null)

  // ラテント連続性は接続先の ComfyUI 頼みなので、開いたときに 1 度だけ聞く。
  // 聞けなかったときはトグルを塞がず、いま入っている値のままにしておく。
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
  }, [])

  // プロジェクトを切り替えたら編集中の内容もそちらに揃える。
  useEffect(() => {
    setForm({
      name: detail.name,
      code: detail.code,
      synopsis: detail.synopsis,
      world_notes: detail.world_notes,
      auto_translate: detail.auto_translate,
      latent_continuity: detail.latent_continuity,
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
            <div>
              <label className="label" htmlFor="studio-project-name">
                作品名
              </label>
              <input
                id="studio-project-name"
                className="field"
                value={form.name}
                onChange={(event) => patch({ name: event.target.value })}
              />
              <FieldError message={errors.name} />
            </div>
            <div>
              <label className="label" htmlFor="studio-project-code">
                作品コード（任意）
              </label>
              <input
                id="studio-project-code"
                className="field"
                value={form.code}
                placeholder="EP01"
                onChange={(event) => patch({ code: event.target.value })}
              />
            </div>
          </div>

          <div>
            <label className="label" htmlFor="studio-project-synopsis">
              あらすじ
            </label>
            <textarea
              id="studio-project-synopsis"
              className="field h-24 resize-y"
              value={form.synopsis}
              onChange={(event) => patch({ synopsis: event.target.value })}
            />
          </div>

          <div>
            <label className="label" htmlFor="studio-project-notes">
              世界観メモ
            </label>
            <textarea
              id="studio-project-notes"
              className="field h-32 resize-y"
              value={form.world_notes}
              placeholder="時代・土地・トーン・撮影の決まりごとなど"
              onChange={(event) => patch({ world_notes: event.target.value })}
            />
          </div>

          <div className="space-y-2">
            <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-300">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 accent-accent-500"
                checked={form.auto_translate}
                onChange={(event) => patch({ auto_translate: event.target.checked })}
              />
              日本語プロンプトを自動で英訳して投入
            </label>
            <div>
              <label
                className={`flex items-center gap-2 text-xs text-slate-300 ${
                  continuityDisabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
                }`}
              >
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-accent-500"
                  checked={form.latent_continuity}
                  disabled={continuityDisabled}
                  onChange={(event) =>
                    patch({ latent_continuity: event.target.checked })
                  }
                />
                ラテント連続性（連続カットを動きと音ごと引き継ぐ）
              </label>
              <p className="mt-1 text-[11px] text-slate-500">
                {continuityDisabled
                  ? '接続先（Comfy Cloud）では利用できません（MiniMaxH3MotionContext 系のカスタムノードが入っていません）。'
                  : 'カットの引き継ぎ（「直前カットから続ける」）が、ラストフレーム 1 枚ではなく直前カットの動画とラテントごとになります。参照素材の指定と直前カットの採用 Take が要ります。'}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button className="btn-primary" onClick={save} disabled={busy}>
              保存
            </button>
            <button className="btn-ghost" onClick={onOpenRevisions} disabled={busy}>
              変更履歴
            </button>
            <button className="btn-danger ml-auto" onClick={onDelete} disabled={busy}>
              このプロジェクトを削除
            </button>
          </div>
        </div>
      </Section>
    </div>
  )
}
