import { workflowFamilies, workflowModeLabel } from '../form'
import type { WorkflowOption } from '../types'
import { NativeSelect } from './NativeSelect'
import { Input } from './ui/input'
import { Label } from './ui/label'

/**
 * ワークフローの「モデル → モード」2 段プルダウン（SPEC §3.1）。
 *
 * 1 段目でモデル（ファミリー）を、2 段目でそのモデルのモード（t2v / i2v /
 * 素材参照 …）を選ぶ。フォームが持つ状態は今までどおりワークフロー id 1 つ
 * だけで、モデルは選択中の id から引く: おかげで id を外から入れる経路（前回
 * 選択の復元・ライブラリからの連鎖）は何も変えずに両方のセレクトが揃う。
 *
 * モードが 1 つしかないモデルでもセレクトは出す（`disabled`）。消すと段組みが
 * 崩れるうえ、「このモデルは 1 モードだけ」という情報も見えなくなるため。
 */
export default function WorkflowPicker({
  workflows,
  value,
  onChange,
  modelLabel,
  modeLabel,
  fallbackLabel,
}: {
  /** 選べるワークフロー（モードで絞ったあとのもの）。空なら id を手入力させる。 */
  workflows: WorkflowOption[]
  /** 選択中のワークフロー id。 */
  value: string
  onChange: (workflowId: string) => void
  /** 1 段目のラベル（「動画モデル」など）。 */
  modelLabel: string
  /** 2 段目のラベル（「動画モード」など）。 */
  modeLabel: string
  /** 選択肢が届く前の手入力欄のラベル（「動画ワークフロー」など）。 */
  fallbackLabel: string
}) {
  // 選択肢が来ていない（ComfyUI に繋がらない）ときは今までどおり id の手入力。
  if (workflows.length === 0) {
    return (
      <Input
        aria-label={fallbackLabel}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    )
  }

  const groups = workflowFamilies(workflows)
  const current = workflows.find((item) => item.id === value) ?? null
  // 未知の id（既定値のまま・古い記憶）でも 1 段目が空にならないよう先頭へ寄せる。
  // 実際の id は各セクションの useEffect が選択肢の中の値へ直す。
  const group =
    groups.find((item) => item.family === current?.family) ?? groups[0]
  const modes = group.workflows
  const selected = current?.id ?? modes[0].id

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      <div>
        <Label className="mb-1">モデル</Label>
        <NativeSelect
          aria-label={modelLabel}
          value={group.family}
          onChange={(event) => {
            const picked = groups.find(
              (item) => item.family === event.target.value,
            )
            // モデルを変えたら、そのモデルの先頭モードへ（存在しない id の
            // まま送信されないようにする）。
            if (picked) onChange(picked.workflows[0].id)
          }}
        >
          {groups.map((item) => (
            <option key={item.family} value={item.family}>
              {item.label}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div>
        <Label className="mb-1">モード</Label>
        <NativeSelect
          aria-label={modeLabel}
          value={selected}
          disabled={modes.length < 2}
          onChange={(event) => onChange(event.target.value)}
        >
          {modes.map((item) => (
            <option key={item.id} value={item.id}>
              {workflowModeLabel(item)}
            </option>
          ))}
        </NativeSelect>
      </div>
    </div>
  )
}
