import { Button } from '../ui/button'
import LoraPicker from '../LoraPicker'
import type { Lora, LoraRef } from '../../types'
import {
  joinLoraTriggers,
  loraRefFromEntry,
  refsFromSelected,
  selectedFromRefs,
} from './studio'

/**
 * スタジオの動画 LoRA 欄（作品設定と生成ダイアログで共有）。
 *
 * 生成フォームと同じ `LoraPicker`（選択・強度・並べ替え）を、`LoraRef` の配列を
 * 値に持つ controlled な部品として包む。トリガーワードは選んだ LoRA のものを
 * 自動で連結して使う（編集欄は出さず、何が付くかだけを見せる）。
 */
export default function VideoLoraEditor({
  registered,
  value,
  onChange,
  searchId,
}: {
  /** いまの接続先に登録されている動画用 LoRA（`target = 'video'`）。 */
  registered: Lora[]
  value: LoraRef[]
  onChange: (next: LoraRef[]) => void
  /** ピッカーの検索欄の id（同じ画面に 2 つ開きうるので分ける）。 */
  searchId?: string
}) {
  const selected = selectedFromRefs(value, registered)
  const triggers = joinLoraTriggers(value)
  // いまの接続先の登録に見当たらない LoRA（別環境で登録した・消した）。
  // ピッカーの一覧に出ないので、ここで外せるようにしておく。
  const missing = value.filter(
    (ref) => !registered.some((lora) => lora.lora_name === ref.lora_name),
  )

  const toggle = (lora: Lora) => {
    const already = value.some((ref) => ref.lora_name === lora.lora_name)
    onChange(
      already
        ? value.filter((ref) => ref.lora_name !== lora.lora_name)
        : [...value, loraRefFromEntry(lora)],
    )
  }

  return (
    <div className="space-y-2">
      <LoraPicker
        loras={registered}
        selected={selected}
        triggerText={triggers}
        triggerDirty={false}
        showTrigger={false}
        searchId={searchId}
        emptyHint="動画用の登録済み LoRA がありません（設定 → LoRA 管理で追加）"
        onToggle={toggle}
        onStrength={(index, strength) => {
          const next = refsFromSelected(selected)
          next[index] = { ...next[index], strength }
          onChange(next)
        }}
        onMove={(from, to) => {
          const next = refsFromSelected(selected)
          const [moved] = next.splice(from, 1)
          next.splice(to, 0, moved)
          onChange(next)
        }}
        onTrigger={() => {}}
        onTriggerReset={() => {}}
      />
      {missing.length > 0 && (
        <ul className="space-y-1 text-[11px] text-amber-300">
          {missing.map((ref) => (
            <li key={ref.lora_name} className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate">
                いまの接続先の LoRA 登録に見当たりません: {ref.lora_name}
              </span>
              <Button
                variant="ghost"
                size="xs"
                onClick={() =>
                  onChange(value.filter((item) => item.lora_name !== ref.lora_name))
                }
              >
                外す
              </Button>
            </li>
          ))}
        </ul>
      )}
      <p className="text-[11px] text-muted-foreground">
        {value.length === 0
          ? '動画 LoRA なし'
          : triggers
            ? `トリガーワード（プロンプトの先頭に付く）: ${triggers}`
            : 'トリガーワードなし'}
      </p>
    </div>
  )
}
