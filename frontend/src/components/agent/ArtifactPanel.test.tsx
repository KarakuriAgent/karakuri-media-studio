import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { AgentArtifact } from '../../types'
import ArtifactPanel from './ArtifactPanel'

afterEach(cleanup)

function artifact(
  kind: AgentArtifact['kind'],
  title: string,
  extra: Partial<AgentArtifact> = {},
): AgentArtifact {
  return {
    kind,
    title,
    ts: '2026-07-27T00:00:00',
    name: '',
    url: null,
    job_id: null,
    text: null,
    ...extra,
  }
}

function show(artifacts: AgentArtifact[]) {
  return render(
    <ArtifactPanel
      sessionId="session-1"
      artifacts={artifacts}
      pending={[]}
      collapsed={false}
      onToggle={() => {}}
      onExpand={() => {}}
    />,
  )
}

const FRAMES = [
  artifact('frame', '① 明るいスタジオ フレーム検分 1', {
    job_id: 'job-1',
    name: 'inspect_job-1/frame_001.png',
  }),
  artifact('frame', '① 明るいスタジオ フレーム検分 2', {
    job_id: 'job-1',
    name: 'inspect_job-1/frame_002.png',
  }),
]

describe('ArtifactPanel のリンクカード', () => {
  it('カードにサムネイル画像を出さない（開くまで中身が見えない）', () => {
    const { container } = show([
      artifact('image', '夕暮れ屋上ダンス', { url: '/outputs/a.png' }),
      ...FRAMES,
    ])
    expect(container.querySelectorAll('img')).toHaveLength(0)
  })

  it('タイトル・種別チップ・時刻を出す', () => {
    show([artifact('video', '夕暮れ屋上ダンス・引きカメラ', { url: '/outputs/a.mp4' })])
    expect(screen.queryByText('夕暮れ屋上ダンス・引きカメラ')).not.toBeNull()
    expect(screen.queryByText('動画')).not.toBeNull()
    expect(screen.queryByText('07-27 00:00')).not.toBeNull()
  })

  it('フレームは 1 枚のまとめカードになる', () => {
    show(FRAMES)
    expect(screen.getAllByRole('button', { name: /フレーム検分/ })).toHaveLength(1)
    expect(screen.queryByText('① 明るいスタジオ フレーム検分 (2枚)')).not.toBeNull()
    expect(screen.queryByText('フレーム 2')).not.toBeNull()
  })

  it('まとめカードを開くとグリッドに全フレームが並ぶ', () => {
    const { container } = show(FRAMES)
    fireEvent.click(screen.getByRole('button', { name: /フレーム検分/ }))
    expect(container.querySelectorAll('img')).toHaveLength(2)
  })

  it('新着でビューアを勝手に開かない', () => {
    const { container, rerender } = show([])
    rerender(
      <ArtifactPanel
        sessionId="session-1"
        artifacts={[artifact('image', '新しい画像', { url: '/outputs/a.png' })]}
        pending={[]}
        collapsed={false}
        onToggle={() => {}}
        onExpand={() => {}}
      />,
    )
    expect(container.querySelectorAll('img')).toHaveLength(0)
  })
})
