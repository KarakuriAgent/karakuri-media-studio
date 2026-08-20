import { useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'

import type { TimelineSyncPreview, TimelineSyncRequest } from '../../types'
import { Modal } from '../ui'
import { Button } from '../ui/button'
import { Checkbox } from '../ui/checkbox'
import { formatSeconds } from './timeline'

/**
 * 脚本との差分を項目ごとに選んで反映するダイアログ。
 *
 * 3 つの差分（増えたカット / 採用が変わったカット / 消えたカット）を並べ、
 * チェックしたものだけ `POST /sync` へ送る。既定は**全部オン**（バナーを出す
 * のは「反映したい」と思ったときなので、いちいち全部押させない）。
 */
export default function SyncDialog({
  preview,
  busy,
  onApply,
  onClose,
}: {
  preview: TimelineSyncPreview
  busy: boolean
  onApply: (request: TimelineSyncRequest) => void
  onClose: () => void
}) {
  const [added, setAdded] = useState<string[]>(() =>
    preview.added.map((item) => item.shot_id),
  )
  const [retaken, setRetaken] = useState<string[]>(() =>
    preview.retaken.map((item) => item.clip_id),
  )
  const [removed, setRemoved] = useState<string[]>(() =>
    preview.removed.map((item) => item.clip_id),
  )

  const chosen = added.length + retaken.length + removed.length
  const toggle = (
    values: string[],
    setValues: (next: string[]) => void,
    id: string,
  ) =>
    setValues(
      values.includes(id)
        ? values.filter((value) => value !== id)
        : [...values, id],
    )

  const total = useMemo(
    () =>
      preview.added.length + preview.retaken.length + preview.removed.length,
    [preview],
  )

  return (
    <Modal title="脚本の変更を反映" onClose={onClose}>
      <div className="flex flex-col gap-4 text-xs">
        <p className="text-muted-foreground">
          このタイムラインを作ったあとに脚本が {total} 件動いています。
          反映するものを選んでください（反映しなかったものは次も出ます）。
        </p>

        {preview.added.length > 0 && (
          <Group title={`増えたカット（${preview.added.length}）`}>
            {preview.added.map((item) => (
              <Row
                key={item.shot_id}
                checked={added.includes(item.shot_id)}
                onChange={() => toggle(added, setAdded, item.shot_id)}
                label={item.label || item.shot_id}
                note={`V1 の末尾へ ${formatSeconds(item.duration_ms)} で追加`}
              />
            ))}
          </Group>
        )}

        {preview.retaken.length > 0 && (
          <Group title={`採用テイクが変わったカット（${preview.retaken.length}）`}>
            {preview.retaken.map((item) => (
              <Row
                key={item.clip_id}
                checked={retaken.includes(item.clip_id)}
                onChange={() => toggle(retaken, setRetaken, item.clip_id)}
                label={item.label || item.clip_id}
                note={
                  item.duration_ms == null
                    ? '新しいテイクへ差し替え'
                    : `新しいテイク（${formatSeconds(item.duration_ms)}）へ差し替え。` +
                      'はみ出す切り出しは丸められます'
                }
              />
            ))}
          </Group>
        )}

        {preview.removed.length > 0 && (
          <Group title={`消えたカット（${preview.removed.length}）`}>
            {preview.removed.map((item) => (
              <Row
                key={item.clip_id}
                checked={removed.includes(item.clip_id)}
                onChange={() => toggle(removed, setRemoved, item.clip_id)}
                label={item.label || item.clip_id}
                note={`${item.reason}。クリップを消して後ろを詰めます`}
              />
            ))}
          </Group>
        )}

        <div className="flex items-center justify-end gap-2 border-t border-border pt-3">
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>
            やめる
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={busy || chosen === 0}
            onClick={() =>
              onApply({
                add_shot_ids: added,
                retake_clip_ids: retaken,
                remove_clip_ids: removed,
              })
            }
          >
            {busy && (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            )}
            {chosen} 件を反映
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h4>
      {children}
    </section>
  )
}

function Row({
  checked,
  onChange,
  label,
  note,
}: {
  checked: boolean
  onChange: () => void
  label: string
  note: string
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded border border-border bg-secondary/40 px-2 py-1.5">
      <Checkbox
        checked={checked}
        onCheckedChange={onChange}
        className="mt-0.5"
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{label}</span>
        <span className="block text-[10px] text-muted-foreground">{note}</span>
      </span>
    </label>
  )
}
