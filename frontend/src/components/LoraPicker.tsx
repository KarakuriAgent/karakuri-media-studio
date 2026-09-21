import { useId, useState } from 'react'
import { ArrowDown, ArrowUp, Check } from 'lucide-react'

import { matchesLoraQuery, type SelectedLora } from '../form'
import type { Lora } from '../types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Modal } from './ui'

/**
 * LoRA の選択（チップ + 強度スライダー + トリガーワード）。
 *
 * 生成フォームの画像・動画の 2 段と、スタジオの作品設定・生成ダイアログで
 * 共有する。`onMove` を渡すと選択済みの行に上下の並べ替えボタンが付き
 * （チェーンは選んだ順に直列で挿さるので、順番にも意味がある）、
 * `showTrigger` を false にするとトリガーワードの編集欄を出さない
 * （スタジオでは選んだ LoRA のトリガーワードを自動で連結して使う）。
 */
export default function LoraPicker({
  loras,
  selected,
  triggerText,
  triggerDirty,
  emptyHint,
  onToggle,
  onStrength,
  onTrigger,
  onTriggerReset,
  onMove,
  showTrigger = true,
  searchId = 'lora-picker-search',
}: {
  loras: Lora[]
  selected: SelectedLora[]
  triggerText: string
  triggerDirty: boolean
  emptyHint: string
  onToggle: (lora: Lora) => void
  onStrength: (index: number, strength: number) => void
  onTrigger: (value: string) => void
  onTriggerReset: () => void
  /** 選択済みの `from` 番目を `to` 番目へ動かす（省略 = 並べ替えボタンを出さない）。 */
  onMove?: (from: number, to: number) => void
  /** トリガーワードの編集欄を出すか（既定 true）。 */
  showTrigger?: boolean
  /** 検索欄の id（同じ画面に 2 つ開きうるときに分ける）。 */
  searchId?: string
}) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const [query, setQuery] = useState('')
  // 画像用と動画用で 2 つ描かれるので、ラベルの結び付け先は id を分ける。
  const triggerId = useId()
  const visibleLoras = loras.filter((lora) => matchesLoraQuery(lora, query))

  return (
    <div>
      {loras.length === 0 ? (
        <p className="text-xs text-muted-foreground">{emptyHint}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              // 前回の検索語が残っていると絞り込まれた状態で開いてしまうため、
              // 開くたびに検索欄をリセットする。
              setQuery('')
              setPickerOpen(true)
            }}
          >
            LoRAを選ぶ
          </Button>
          <Badge variant="secondary" className="tnum">
            選択 {selected.length} / 候補 {loras.length}
          </Badge>
          {selected.length === 0 && (
            <span className="text-[11px] text-muted-foreground">未選択</span>
          )}
        </div>
      )}

      {selected.length > 0 && (
        <div className="mt-3 flex flex-col gap-2">
          {selected.map((lora, index) => (
            <div key={lora.id} className="flex items-center gap-2">
              <span className="w-24 shrink-0 truncate text-xs text-foreground/85">
                {lora.display_name}
              </span>
              <Slider
                className="flex-1"
                min={0}
                max={2}
                step={0.05}
                aria-label={`${lora.display_name} の強度`}
                value={[lora.strength]}
                onValueChange={([value]) => onStrength(index, value)}
              />
              <span className="tnum w-10 text-right text-xs text-muted-foreground">
                {lora.strength.toFixed(2)}
              </span>
              {onMove && (
                <span className="flex shrink-0 items-center">
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`${lora.display_name} を上へ`}
                    title="上へ（先に挿す）"
                    disabled={index === 0}
                    onClick={() => onMove(index, index - 1)}
                  >
                    <ArrowUp />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`${lora.display_name} を下へ`}
                    title="下へ（後に挿す）"
                    disabled={index === selected.length - 1}
                    onClick={() => onMove(index, index + 1)}
                  >
                    <ArrowDown />
                  </Button>
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {showTrigger && (
      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between">
          <Label htmlFor={triggerId}>トリガーワード（自動連結・編集可）</Label>
          {triggerDirty && (
            <Button variant="ghost" size="xs" onClick={onTriggerReset}>
              自動連結に戻す
            </Button>
          )}
        </div>
        <Input
          id={triggerId}
          value={triggerText}
          onChange={(event) => onTrigger(event.target.value)}
        />
      </div>
      )}

      {pickerOpen && (
        <Modal title="LoRAを選択" onClose={() => setPickerOpen(false)} wide closeOnBackdrop>
          <div className="sticky top-0 z-10 -mx-1 mb-3 border-b border-border bg-card/95 px-1 pb-3 backdrop-blur">
            <Label className="mb-1" htmlFor={searchId}>
              名前・ファイル名・トリガーで検索
            </Label>
            <div className="flex items-center gap-2">
              <Input
                id={searchId}
                placeholder="LoRAを検索"
                value={query}
                autoFocus
                onChange={(event) => setQuery(event.target.value)}
              />
              {query && (
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  onClick={() => setQuery('')}
                >
                  クリア
                </Button>
              )}
            </div>
            <div className="tnum mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
              <span>
                表示 {visibleLoras.length} / 全 {loras.length}
              </span>
              <span>選択中 {selected.length}件</span>
            </div>
          </div>

          {visibleLoras.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
              条件に一致するLoRAがありません
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {visibleLoras.map((lora) => {
                const active = selected.some((item) => item.id === lora.id)
                const sample = lora.sample_images[0]
                return (
                  <button
                    key={lora.id}
                    type="button"
                    aria-label={lora.display_name}
                    aria-pressed={active}
                    className={`flex min-h-20 items-center gap-3 rounded-lg border p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
                      active
                        ? 'border-primary bg-primary/15 text-foreground'
                        : 'border-border bg-surface-sunken text-foreground/85 hover:bg-secondary'
                    }`}
                    title={lora.lora_name}
                    onClick={() => onToggle(lora)}
                  >
                    {sample ? (
                      <img
                        src={sample}
                        alt=""
                        aria-hidden="true"
                        loading="lazy"
                        className="size-14 shrink-0 rounded-md border border-border object-cover"
                      />
                    ) : (
                      <span
                        aria-hidden="true"
                        className="flex size-14 shrink-0 items-center justify-center rounded-md border border-border bg-background text-lg font-semibold text-muted-foreground-subtle"
                      >
                        L
                      </span>
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium">
                        {lora.display_name}
                      </span>
                      <span className="mt-1 block truncate text-[11px] text-muted-foreground">
                        {lora.trigger_word || 'トリガーなし'}
                      </span>
                      <span className="tnum block text-[11px] text-muted-foreground-subtle">
                        強度 {lora.default_strength.toFixed(2)}
                      </span>
                    </span>
                    <span
                      aria-hidden="true"
                      className={`flex size-5 shrink-0 items-center justify-center rounded-full border ${
                        active
                          ? 'border-primary bg-primary text-primary-foreground'
                          : 'border-input text-transparent'
                      }`}
                    >
                      <Check className="size-3" />
                    </span>
                  </button>
                )
              })}
            </div>
          )}

          <div className="sticky bottom-0 -mx-1 mt-4 flex items-center justify-between border-t border-border bg-card/95 px-1 pt-3 backdrop-blur">
            <span className="text-xs text-muted-foreground">
              {selected.length}件を選択中
            </span>
            <Button size="sm" onClick={() => setPickerOpen(false)}>
              選択を完了
            </Button>
          </div>
        </Modal>
      )}
    </div>
  )
}
