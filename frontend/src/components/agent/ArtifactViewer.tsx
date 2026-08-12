import { useEffect, useState } from 'react'
import { Download, X } from 'lucide-react'

import type { AgentArtifact } from '../../types'
import { Modal } from '../ui'
import { Button } from '../ui/button'
import { ARTIFACT_LABEL, ArtifactIcon, formatTime } from './common'
import { downloadName } from './logic'

const MEDIA_KINDS: AgentArtifact['kind'][] = ['image', 'video', 'frame', 'audio']

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
        title={`${ARTIFACT_LABEL[artifact.kind]}: ${artifact.title || artifact.name}`}
        onClose={onClose}
        wide
      >
        <p className="tnum mb-2 text-[11px] text-muted-foreground">
          {formatTime(artifact.ts)}
        </p>
        <pre className="whitespace-pre-wrap break-words rounded-md border border-border bg-surface-sunken p-3 text-xs text-foreground/85">
          {text || '（本文がありません）'}
        </pre>
        {url && (
          <div className="mt-3">
            <Button variant="outline" size="sm" asChild>
              <a href={url} download={downloadName(artifact.title, url)}>
                <Download />
                ダウンロード
              </a>
            </Button>
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
          className="max-h-full max-w-full"
          onClick={(event) => event.stopPropagation()}
        />
      ) : artifact.kind === 'audio' ? (
        <audio
          src={url ?? ''}
          controls
          className="w-full max-w-xl"
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
        <span className="flex items-center gap-1.5 rounded-full border border-border bg-card/90 px-3 py-1 text-xs text-foreground/85 backdrop-blur">
          <ArtifactIcon kind={artifact.kind} />
          {artifact.title}
        </span>
        {url && (
          <Button variant="outline" size="sm" asChild>
            <a href={url} download={downloadName(artifact.title, url)}>
              <Download />
              ダウンロード
            </a>
          </Button>
        )}
      </div>

      <Button
        variant="outline"
        size="icon-sm"
        className="absolute right-4 top-4"
        onClick={onClose}
        title="閉じる (Esc)"
        aria-label="閉じる"
      >
        <X />
      </Button>
    </div>
  )
}
