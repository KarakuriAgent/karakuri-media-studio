import { Loader2, Undo2 } from 'lucide-react'

import type { StudioRevision } from '../../types'
import { Modal } from '../ui'
import { Button } from '../ui/button'
import { REVISION_ACTOR_CLASS, REVISION_ACTOR_LABEL } from './studio'

/** ISO 8601 を「2026-08-07 09:30」まで詰める（秒とタイムゾーンは落とす）。 */
export function formatRevisionTime(value: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(value)
  return match ? `${match[1]} ${match[2]}` : value
}

/**
 * 変更履歴（リビジョン）の一覧モーダル。
 *
 * 新しい順に「いつ・誰が・何を」を並べ、各行から**その時点へ書き戻せる**
 * （Take と実ファイルは残る、というサーバー側の振る舞いに合わせて注記を出す）。
 */
export default function RevisionsModal({
  revisions,
  loading,
  onRestore,
  onClose,
  busy,
}: {
  revisions: StudioRevision[]
  loading: boolean
  /** その時点へ書き戻す（確認は呼ぶ側で取る）。 */
  onRestore: (seq: number) => void
  onClose: () => void
  busy: boolean
}) {
  return (
    <Modal title="変更履歴" onClose={onClose} wide closeOnBackdrop>
      {loading ? (
        <p className="flex items-center justify-center gap-2 px-3 py-6 text-center text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          読込中…
        </p>
      ) : revisions.length === 0 ? (
        <p className="px-3 py-6 text-center text-xs text-muted-foreground">
          まだ変更履歴がありません
        </p>
      ) : (
        <ul className="space-y-1">
          {revisions.map((revision) => (
            <li
              key={revision.seq}
              className="flex items-center gap-2 rounded-md border border-border bg-surface-sunken px-3 py-2"
            >
              <span className="tnum shrink-0 font-mono text-[11px] text-muted-foreground">
                #{revision.seq}
              </span>
              <span className="tnum shrink-0 text-[11px] text-muted-foreground">
                {formatRevisionTime(revision.created_at)}
              </span>
              <span
                className={`chip !px-1.5 !py-0 shrink-0 text-[11px] ${
                  REVISION_ACTOR_CLASS[revision.actor]
                }`}
              >
                {REVISION_ACTOR_LABEL[revision.actor]}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs text-foreground/90">
                {revision.action || '（説明なし）'}
              </span>
              <Button
                variant="outline"
                size="xs"
                className="shrink-0"
                aria-label={`#${revision.seq} の時点に戻す`}
                onClick={() => onRestore(revision.seq)}
                disabled={busy}
              >
                <Undo2 />
                この時点に戻す
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}
