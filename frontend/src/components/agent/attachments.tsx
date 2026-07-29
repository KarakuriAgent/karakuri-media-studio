/**
 * 添付ファイルの共通部品（チャット欄と新規セッションフォームで共用）。
 *
 * 許可拡張子はバックエンドの `ATTACHMENT_EXT`（backend/app/routers/agent.py）と
 * 同じ集合にしておき、アップロード前にフロントでも弾く。
 */

export const ATTACHMENT_EXT = [
  // image
  '.png', '.jpg', '.jpeg', '.webp', '.bmp',
  // audio
  '.mp3', '.wav', '.flac', '.m4a', '.ogg', '.opus',
  // video
  '.mp4', '.webm', '.mkv', '.mov',
  // document
  '.txt', '.md', '.json', '.csv', '.pdf',
]

/** `<input type="file">` の accept 属性値。 */
export const ATTACHMENT_ACCEPT = ATTACHMENT_EXT.join(',')

export function isAllowedAttachment(name: string): boolean {
  const lower = name.toLowerCase()
  return ATTACHMENT_EXT.some((ext) => lower.endsWith(ext))
}

/** 許可外を弾いたときのエラー文（複数まとめて 1 行にする）。 */
export function rejectedMessage(names: string[]): string {
  return `この形式は添付できません: ${names.join(', ')}`
}

export function AttachmentChip({
  label,
  title,
  onRemove,
}: {
  label: string
  title?: string
  onRemove: () => void
}) {
  return (
    <span className="flex items-center gap-1 rounded border border-ink-600 bg-ink-800 px-1.5 py-1 text-[11px] text-slate-300">
      <span className="max-w-[12rem] truncate" title={title ?? label}>
        📎 {label}
      </span>
      <button
        className="text-slate-500 hover:text-slate-200"
        title="添付を外す"
        aria-label={`${label} を外す`}
        onClick={onRemove}
      >
        ✕
      </button>
    </span>
  )
}
