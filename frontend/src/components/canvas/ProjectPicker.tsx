import { useState } from 'react'
import type { CanvasProject } from '../../types'

/**
 * キャンバス上部のプロジェクト操作バー（選択・新規作成・改名・削除）。
 */
export default function ProjectPicker({
  projects,
  activeId,
  loading,
  busy,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  onReload,
}: {
  projects: CanvasProject[]
  activeId: string | null
  loading: boolean
  busy: boolean
  onSelect: (id: string | null) => void
  onCreate: (title: string) => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  onReload: () => void
}) {
  const [creating, setCreating] = useState(false)
  const [title, setTitle] = useState('')
  const active = projects.find((item) => item.id === activeId) ?? null

  const create = () => {
    onCreate(title.trim())
    setTitle('')
    setCreating(false)
  }

  const rename = () => {
    if (!active) return
    const next = window.prompt('キャンバス名', active.title)
    if (next === null) return
    onRename(active.id, next.trim())
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        className="field !w-auto min-w-[10rem] max-w-[16rem]"
        aria-label="キャンバス"
        value={activeId ?? ''}
        disabled={busy}
        onChange={(event) => onSelect(event.target.value || null)}
      >
        <option value="">
          {loading ? '読み込み中…' : 'キャンバスを選択'}
        </option>
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.title || '（無題）'}
          </option>
        ))}
      </select>

      {creating ? (
        <>
          <input
            className="field !w-40"
            aria-label="新しいキャンバス名"
            autoFocus
            value={title}
            placeholder="PV 企画"
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') create()
              if (event.key === 'Escape') setCreating(false)
            }}
          />
          <button className="btn-primary text-xs" onClick={create} disabled={busy}>
            作成
          </button>
          <button className="btn-ghost text-xs" onClick={() => setCreating(false)}>
            キャンセル
          </button>
        </>
      ) : (
        <button
          className="btn-ghost text-xs"
          onClick={() => setCreating(true)}
          disabled={busy}
        >
          ＋ 新規キャンバス
        </button>
      )}

      {active && (
        <>
          <button className="btn-ghost text-xs" onClick={rename} disabled={busy}>
            改名
          </button>
          <button
            className="btn-ghost text-xs"
            onClick={() => onDelete(active.id)}
            disabled={busy}
          >
            🗑 削除
          </button>
        </>
      )}

      <button
        className="btn-ghost text-xs"
        onClick={onReload}
        disabled={loading}
        title="一覧を取り直す"
      >
        ⟳
      </button>
    </div>
  )
}
