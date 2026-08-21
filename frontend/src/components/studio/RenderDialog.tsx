import { useState } from 'react'
import { Loader2 } from 'lucide-react'

import type { StudioRenderRequest, StudioShot } from '../../types'
import { FieldError, Modal } from '../ui'
import { NativeSelect } from '../NativeSelect'
import { Button } from '../ui/button'
import { Checkbox } from '../ui/checkbox'
import { Input } from '../ui/input'
import { Label } from '../ui/label'
import {
  MAX_STEPS,
  SHOT_DURATION_MAX,
  SHOT_DURATION_MIN,
  renderFormFromShot,
  renderRequestFromForm,
  validateRenderForm,
  type RenderDefaults,
  type RenderFormState,
} from './studio'

/**
 * 「生成」ボタンのダイアログ: そのテイク 1 回ぶんの設定。
 *
 * 初期値はいまの解決結果（解像度はカット → プロジェクト、尺はカット、
 * ステップ数とラテントアップスケールはプロジェクト、シードはカットの設定）なので、**何も触らずに
 * 「この設定で生成」を押せば今までどおりの投入**になる。ここで変えた値は
 * その 1 回にだけ効き、カットもプロジェクトも書き換えない。
 */
export default function RenderDialog({
  shot,
  project,
  aspectRatios = [],
  busy,
  onRender,
  onClose,
}: {
  shot: StudioShot
  /** プロジェクト側の既定（解像度・ステップ数・ラテントアップスケール）。 */
  project: RenderDefaults
  /** 生成フォームと同じアスペクト比の候補。 */
  aspectRatios?: string[]
  busy: boolean
  onRender: (body: StudioRenderRequest) => void
  onClose: () => void
}) {
  const [form, setForm] = useState<RenderFormState>(() =>
    renderFormFromShot(shot, project),
  )
  const [showErrors, setShowErrors] = useState(false)
  const patch = (changes: Partial<RenderFormState>) => {
    setForm((current) => ({ ...current, ...changes }))
  }

  const errors = validateRenderForm(form)
  const submit = () => {
    if (Object.keys(errors).length > 0) {
      setShowErrors(true)
      return
    }
    onRender(renderRequestFromForm(form, project))
  }
  const errorOf = (name: string) => (showErrors ? errors[name] : undefined)

  // 保存済みの候補に無い比（作品側で自由入力したもの）も選択肢に残す。
  const ratios = form.aspect_ratio && !aspectRatios.includes(form.aspect_ratio)
    ? [form.aspect_ratio, ...aspectRatios]
    : aspectRatios

  return (
    <Modal title={`${shot.title || 'カット'} を生成`} onClose={onClose}>
      <div className="space-y-3">
        <p className="text-xs text-muted-foreground">
          この 1 回の生成にだけ効く設定です（カットとプロジェクトの設定は変わり
          ません）。触らずにそのまま生成すれば、いつもどおりの設定で焼きます。
        </p>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="studio-render-aspect-ratio">アスペクト比</Label>
            <NativeSelect
              id="studio-render-aspect-ratio"
              value={form.aspect_ratio}
              onChange={(event) => patch({ aspect_ratio: event.target.value })}
            >
              <option value="">既定のまま</option>
              {ratios.map((ratio) => (
                <option key={ratio} value={ratio}>
                  {ratio}
                </option>
              ))}
            </NativeSelect>
          </div>

          <div className="space-y-1">
            <Label htmlFor="studio-render-megapixels">解像度（メガピクセル）</Label>
            <Input
              id="studio-render-megapixels"
              className="tnum"
              type="number"
              step={0.05}
              min={0.1}
              placeholder="既定のまま"
              value={form.megapixels}
              onChange={(event) => patch({ megapixels: event.target.value })}
            />
            <FieldError message={errorOf('megapixels')} />
          </div>

          <div className="space-y-1">
            <Label htmlFor="studio-render-duration">尺（秒）</Label>
            <Input
              id="studio-render-duration"
              className="tnum"
              type="number"
              min={SHOT_DURATION_MIN}
              max={SHOT_DURATION_MAX}
              step={0.5}
              value={form.duration}
              onChange={(event) => patch({ duration: event.target.value })}
            />
            <FieldError message={errorOf('duration')} />
          </div>

          <div className="space-y-1">
            <Label htmlFor="studio-render-steps">ステップ数</Label>
            <Input
              id="studio-render-steps"
              className="tnum"
              type="number"
              min={0}
              max={MAX_STEPS}
              step={1}
              placeholder="おまかせ"
              value={form.steps}
              onChange={(event) => patch({ steps: event.target.value })}
            />
            <p className="text-[11px] text-muted-foreground">
              空欄 = おまかせ（テンプレートの既定のまま）
            </p>
            <FieldError message={errorOf('steps')} />
          </div>
        </div>

        <div className="space-y-2 rounded-md border border-border bg-surface-sunken p-2">
          <div className="flex items-center gap-2">
            <Checkbox
              id="studio-render-latent-upscale"
              checked={form.latent_upscale}
              onCheckedChange={(checked) =>
                patch({ latent_upscale: checked === true })
              }
            />
            <Label
              htmlFor="studio-render-latent-upscale"
              className="cursor-pointer text-foreground/85"
            >
              ラテントアップスケール（オフ = 指定解像度で 1 パス）
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="studio-render-fixed-seed"
              checked={form.fixed_seed}
              onCheckedChange={(checked) =>
                patch({ fixed_seed: checked === true })
              }
            />
            <Label
              htmlFor="studio-render-fixed-seed"
              className="cursor-pointer text-foreground/85"
            >
              シードを固定する（オフ = ランダム）
            </Label>
          </div>
          {form.fixed_seed && (
            <div className="pt-1">
              <Input
                id="studio-render-seed"
                aria-label="シード"
                className="tnum"
                type="number"
                step={1}
                value={form.seed}
                onChange={(event) => patch({ seed: event.target.value })}
              />
              <FieldError message={errorOf('seed')} />
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>
            やめる
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy && <Loader2 className="animate-spin" />}
            この設定で生成
          </Button>
        </div>
      </div>
    </Modal>
  )
}
