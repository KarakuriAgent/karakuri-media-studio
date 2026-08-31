import { useState } from 'react'
import { ChevronRight, Loader2, Undo2 } from 'lucide-react'

import type {
  StudioRevision,
  StudioRevisionDiff,
  StudioRevisionEntityDiff,
  StudioRevisionRestore,
} from '../../types'
import { Modal } from '../ui'
import { Button } from '../ui/button'
import {
  REVISION_ACTOR_CLASS,
  REVISION_ACTOR_LABEL,
  REVISION_ENTITY_LABEL,
  REVISION_OP_CLASS,
  REVISION_OP_LABEL,
  revisionValueText,
} from './studio'

/** ISO 8601 を「2026-08-07 09:30」まで詰める（秒とタイムゾーンは落とす）。 */
export function formatRevisionTime(value: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(value)
  return match ? `${match[1]} ${match[2]}` : value
}

/** 1 行ぶんの差分の読み込み状態（行を開いたときに初めて取りに行く）。 */
type DiffState = {
  loading: boolean
  error: string | null
  diff: StudioRevisionDiff | null
}

/**
 * 「このカットの履歴」で開いたときの絞り込み。
 *
 * 絞り込みは**サーバー側**が `entity_kind` / `entity_id` で行う（説明文の名前
 * 一致だと、同じ名前のカットが混ざるし改名で履歴が消える）。`label` は見出しに
 * 出すためだけのもの。
 */
export type RevisionFilter = {
  kind: string
  id: string
  label: string
}

/**
 * 1 リビジョンぶんの差分の 1 行（エンティティ単位）。
 *
 * 戻し先は**このリビジョンの 1 つ前**（`seq - 1`）。差分は「`seq - 1` →
 * `seq`」の変化なので、`seq` へ戻すと変化後の値をもう一度書くだけになり、
 * 消えた行に至ってはそのリビジョンに存在しない（400 になる）。`restorable`
 * が false（`seq` が 1 = 前が無い）のときは戻すボタンを出さない。
 */
function EntityDiff({
  change,
  seq,
  busy,
  restorable,
  onRestorePart,
}: {
  change: StudioRevisionEntityDiff
  seq: number
  busy: boolean
  restorable: boolean
  /** 呼ぶと `seq - 1`（変更前）の状態へ戻る。 */
  onRestorePart: (target: StudioRevisionRestore) => void
}) {
  const label = REVISION_ENTITY_LABEL[change.entity] ?? change.entity
  return (
    <li className="rounded-md border border-border bg-surface px-2 py-1.5">
      <div className="flex items-center gap-2">
        <span
          className={`chip !px-1.5 !py-0 shrink-0 text-[11px] ${
            REVISION_OP_CLASS[change.op]
          }`}
        >
          {REVISION_OP_LABEL[change.op]}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs text-foreground/90">
          {label}『{change.name || change.id}』
        </span>
        {restorable && change.op !== 'create' && change.fields.length === 0 && (
          <Button
            variant="outline"
            size="xs"
            className="shrink-0"
            aria-label={`#${seq} の${label}『${
              change.name || change.id
            }』を変更前に戻す`}
            onClick={() =>
              onRestorePart({ entity: change.entity, id: change.id })
            }
            disabled={busy}
          >
            <Undo2 />
            この{label}を変更前に戻す
          </Button>
        )}
      </div>
      {change.fields.length > 0 && (
        <ul className="mt-1 space-y-1">
          {change.fields.map((field) => (
            <li
              key={field.field}
              className="flex items-start gap-2 rounded-md bg-surface-sunken px-2 py-1"
            >
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                {field.field}
              </span>
              <span className="min-w-0 flex-1 text-[11px] text-foreground/90">
                <span className="text-muted-foreground line-through">
                  {revisionValueText(field.before)}
                </span>
                {' → '}
                <span>{revisionValueText(field.after)}</span>
              </span>
              {restorable && (
                <Button
                  variant="outline"
                  size="xs"
                  className="shrink-0"
                  aria-label={`#${seq} の ${field.field} を変更前に戻す`}
                  onClick={() =>
                    onRestorePart({
                      entity: change.entity,
                      id: change.id,
                      fields: [field.field],
                    })
                  }
                  disabled={busy}
                >
                  <Undo2 />
                  この項目だけ変更前に戻す
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

/**
 * 変更履歴（リビジョン）の一覧モーダル。
 *
 * 新しい順に「いつ・誰が・何を」を並べ、行を開くと**そのリビジョンで何が
 * 変わったか**（エンティティ別・項目別の before → after）を出す。戻し方は 2 つ:
 * 行ごとの「この時点に戻す」（そのリビジョンの状態へプロジェクト丸ごと）と、
 * 差分の中の「変更前に戻す」（そのエンティティ / 項目だけを **1 つ前** の状態
 * へ）。前者は「そこまで巻き戻す」、後者は「この変更を取り消す」で戻し先が
 * 1 つずれることに注意。差分は開いた行のぶんだけ取りに行く。
 */
export default function RevisionsModal({
  revisions,
  loading,
  onRestore,
  onRestorePart,
  loadDiff,
  onClose,
  busy,
  filter = null,
  onShowAll,
}: {
  revisions: StudioRevision[]
  loading: boolean
  /** その時点へ書き戻す（確認は呼ぶ側で取る）。 */
  onRestore: (seq: number) => void
  /** そのリビジョンの 1 件（`fields` があればその項目だけ）を戻す。 */
  onRestorePart: (seq: number, target: StudioRevisionRestore) => void
  /** 行を開いたときに差分を取りに行く。 */
  loadDiff: (seq: number) => Promise<StudioRevisionDiff>
  onClose: () => void
  busy: boolean
  /** カットの履歴として開いたときの絞り込み（null = 作品まるごと）。 */
  filter?: RevisionFilter | null
  /** 絞り込みを外して作品全体の履歴を取り直す。 */
  onShowAll?: () => void
}) {
  const [open, setOpen] = useState<number[]>([])
  const [diffs, setDiffs] = useState<Record<number, DiffState>>({})

  // 取りに行くかどうかは**更新関数の外**で決める: state updater は StrictMode で
  // 二度呼ばれるので、その中で fetch すると二重に飛ぶ。
  const toggle = (seq: number) => {
    setOpen((previous) =>
      previous.includes(seq)
        ? previous.filter((value) => value !== seq)
        : [...previous, seq],
    )
    if (diffs[seq]) return
    setDiffs((previous) => ({
      ...previous,
      [seq]: { loading: true, error: null, diff: null },
    }))
    void (async () => {
      try {
        const diff = await loadDiff(seq)
        setDiffs((current) => ({
          ...current,
          [seq]: { loading: false, error: null, diff },
        }))
      } catch {
        setDiffs((current) => ({
          ...current,
          [seq]: { loading: false, error: '差分を読めませんでした', diff: null },
        }))
      }
    })()
  }

  return (
    <Modal
      title={filter ? `変更履歴 - ${filter.label || filter.id}` : '変更履歴'}
      onClose={onClose}
      wide
      closeOnBackdrop
    >
      {filter && (
        <div className="mb-2 flex items-center gap-2">
          <p className="min-w-0 flex-1 text-[11px] text-muted-foreground">
            『{filter.label || filter.id}』に触れた履歴だけを出しています
            （並べ替えのように複数へ跨る変更は出ません）。
          </p>
          {onShowAll && (
            <Button
              variant="outline"
              size="xs"
              className="shrink-0"
              onClick={onShowAll}
            >
              すべて表示
            </Button>
          )}
        </div>
      )}
      {loading ? (
        <p className="flex items-center justify-center gap-2 px-3 py-6 text-center text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          読込中…
        </p>
      ) : revisions.length === 0 ? (
        <p className="px-3 py-6 text-center text-xs text-muted-foreground">
          {filter ? 'このカットに触れた履歴はありません' : 'まだ変更履歴がありません'}
        </p>
      ) : (
        <ul className="space-y-1">
          {revisions.map((revision) => {
            const expanded = open.includes(revision.seq)
            const state = diffs[revision.seq]
            return (
              <li
                key={revision.seq}
                className="rounded-md border border-border bg-surface-sunken px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  <button
                    className="shrink-0 rounded-md p-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                    aria-expanded={expanded}
                    aria-label={`#${revision.seq} の差分`}
                    onClick={() => toggle(revision.seq)}
                  >
                    <ChevronRight
                      aria-hidden="true"
                      className={`size-3.5 text-muted-foreground ${
                        expanded ? 'rotate-90' : ''
                      }`}
                    />
                  </button>
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
                </div>

                {expanded && (
                  <div className="mt-1.5 pl-6">
                    {state?.loading && (
                      <p className="flex items-center gap-2 py-1 text-[11px] text-muted-foreground">
                        <Loader2 className="size-3 animate-spin" />
                        差分を読込中…
                      </p>
                    )}
                    {state?.error && (
                      <p className="py-1 text-[11px] text-destructive">
                        {state.error}
                      </p>
                    )}
                    {state?.diff && state.diff.changes.length === 0 && (
                      <p className="py-1 text-[11px] text-muted-foreground">
                        変わった項目はありません
                      </p>
                    )}
                    {state?.diff && state.diff.changes.length > 0 && (
                      <ul className="space-y-1">
                        {state.diff.changes.map((change) => (
                          <EntityDiff
                            key={`${change.entity}:${change.id}`}
                            change={change}
                            seq={revision.seq}
                            busy={busy}
                            // 差分は「1 つ前 → このリビジョン」なので、戻し先は
                            // 1 つ前。最初のリビジョンには前が無い。
                            restorable={revision.seq > 1}
                            onRestorePart={(target) =>
                              onRestorePart(revision.seq - 1, target)
                            }
                          />
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Modal>
  )
}
