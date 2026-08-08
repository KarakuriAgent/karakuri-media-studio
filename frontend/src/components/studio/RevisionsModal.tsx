import type { StudioRevision } from '../../types'
import { Modal } from '../ui'
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
      <p className="mb-2 text-[11px] text-slate-500">
        脚本・話と場・World Bible の内容だけを書き戻します（生成した Take と
        実ファイルはそのまま残ります）。
      </p>
      {loading ? (
        <p className="px-3 py-6 text-center text-xs text-slate-600">読込中…</p>
      ) : revisions.length === 0 ? (
        <p className="px-3 py-6 text-center text-xs text-slate-600">
          まだ変更履歴がありません
        </p>
      ) : (
        <ul className="space-y-1">
          {revisions.map((revision) => (
            <li
              key={revision.seq}
              className="flex items-center gap-2 rounded-md border border-ink-600 bg-ink-800 px-3 py-2"
            >
              <span className="shrink-0 font-mono text-[11px] text-slate-600">
                #{revision.seq}
              </span>
              <span className="shrink-0 text-[11px] text-slate-500">
                {formatRevisionTime(revision.created_at)}
              </span>
              <span
                className={`chip !px-1.5 !py-0 shrink-0 text-[10px] ${
                  REVISION_ACTOR_CLASS[revision.actor]
                }`}
              >
                {REVISION_ACTOR_LABEL[revision.actor]}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs text-slate-200">
                {revision.action || '（説明なし）'}
              </span>
              <button
                className="btn-ghost !px-2 !py-0.5 shrink-0 text-[10px]"
                aria-label={`#${revision.seq} の時点に戻す`}
                onClick={() => onRestore(revision.seq)}
                disabled={busy}
              >
                この時点に戻す
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}
