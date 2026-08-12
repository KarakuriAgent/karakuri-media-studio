import { COMFY_TARGETS, COMFY_TARGET_LABELS } from '../form'
import type { ComfyTarget } from '../types'

/**
 * ComfyUI の接続先セレクタ（SPEC §5）。
 *
 * 生成タブ・スタジオ・エージェントで**同じもの**を出す。選択はサーバー側の
 * 設定（`GET/PUT /api/settings` の `comfy_target`）に保存されるグローバル設定
 * なので、どの画面で変えても全体に効く。詳しい接続情報は設定ページ。
 */
export default function TargetSelector({
  target,
  onChange,
  className = '',
  id = 'comfy-target',
}: {
  /** いまの接続先（null = 設定をまだ読めていない＝操作させない）。 */
  target: ComfyTarget | null
  onChange: (target: ComfyTarget) => void
  className?: string
  /** 同じ画面に 2 つ出すときの id の衝突よけ。 */
  id?: string
}) {
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <label className="shrink-0 text-xs text-slate-400" htmlFor={id}>
        接続先
      </label>
      <select
        id={id}
        className="field"
        value={target ?? 'local'}
        disabled={target == null}
        onChange={(event) => onChange(event.target.value as ComfyTarget)}
      >
        {COMFY_TARGETS.map((value) => (
          <option key={value} value={value}>
            {COMFY_TARGET_LABELS[value]}
          </option>
        ))}
      </select>
    </div>
  )
}
