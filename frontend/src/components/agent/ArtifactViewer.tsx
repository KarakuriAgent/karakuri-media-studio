import { useEffect, useState } from 'react'
import type { AgentArtifact } from '../../types'
import { Modal } from '../ui'
import { ARTIFACT_ICON, formatTime } from './common'

const MEDIA_KINDS: AgentArtifact['kind'][] = ['image', 'video', 'frame']

function fileNameOf(url: string): string {
  const clean = url.split('?')[0]
  return clean.slice(clean.lastIndexOf('/') + 1) || 'download'
}

/** Text artifacts may live as a workdir file: fetch it on demand. */
function useArtifactText(artifact: AgentArtifact, url: string | null): string {
  const [text, setText] = useState(artifact.text ?? '')

  useEffect(() => {
    if (artifact.text || !url) return
    let alive = true
    void fetch(url)
      .then((response) => (response.ok ? response.text() : ''))
      .then((body) => {
        if (alive) setText(body)
      })
      .catch(() => {
        if (alive) setText('')
      })
    return () => {
      alive = false
    }
  }, [artifact.text, url])

  return artifact.text ?? text
}

export default function ArtifactViewer({
  artifact,
  url,
  onClose,
}: {
  artifact: AgentArtifact
  url: string | null
  onClose: () => void
}) {
  const media = MEDIA_KINDS.includes(artifact.kind) && url != null
  const text = useArtifactText(artifact, media ? null : url)

  useEffect(() => {
    if (!media) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [media, onClose])

  if (!media) {
    return (
      <Modal
        title={`${ARTIFACT_ICON[artifact.kind]} ${artifact.title || artifact.name}`}
        onClose={onClose}
        wide
      >
        <p className="mb-2 text-[11px] text-slate-500">{formatTime(artifact.ts)}</p>
        <pre className="whitespace-pre-wrap break-words rounded border border-ink-600 bg-ink-900 p-3 text-xs text-slate-300">
          {text || '（本文がありません）'}
        </pre>
        {url && (
          <div className="mt-3">
            <a className="btn-ghost !py-1 text-xs" href={url} download={fileNameOf(url)}>
              ダウンロード
            </a>
          </div>
        )}
      </Modal>
    )
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-6"
      onClick={onClose}
    >
      {artifact.kind === 'video' ? (
        <video
          src={url ?? ''}
          controls
          autoPlay
          className="max-h-full max-w-full"
          onClick={(event) => event.stopPropagation()}
        />
      ) : (
        <img
          src={url ?? ''}
          alt={artifact.title}
          className="max-h-full max-w-full object-contain"
          onClick={(event) => event.stopPropagation()}
        />
      )}

      <div
        className="absolute inset-x-4 bottom-4 flex items-center justify-center gap-3"
        onClick={(event) => event.stopPropagation()}
      >
        <span className="rounded-full border border-ink-600 bg-ink-800/90 px-3 py-1 text-xs text-slate-300 backdrop-blur">
          {ARTIFACT_ICON[artifact.kind]} {artifact.title}
        </span>
        {url && (
          <a className="btn-ghost !py-1 text-xs" href={url} download={fileNameOf(url)}>
            ダウンロード
          </a>
        )}
      </div>

      <button
        className="btn-ghost absolute right-4 top-4 !px-2 !py-1"
        onClick={onClose}
        title="閉じる (Esc)"
      >
        ✕
      </button>
    </div>
  )
}
