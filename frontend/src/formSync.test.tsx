import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useCallback, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from './api'
import { initialForm, type FormState } from './form'
import { FORM_SYNC_DEBOUNCE_MS, useGenerateFormSync } from './formSync'
import type { UiFormProgress } from './types'

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return {
    ...actual,
    api: { getGenerateForm: vi.fn(), putGenerateForm: vi.fn() },
  }
})

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

beforeEach(() => {
  mocked.getGenerateForm.mockResolvedValue({
    values: {},
    revision: 0,
    updated_by: '',
    updated_at: '',
  })
  mocked.putGenerateForm.mockImplementation(
    (values: Record<string, unknown>, base: number | null) => ({
      values,
      revision: (base ?? 0) + 1,
      updated_by: 'ui',
      updated_at: '',
    }),
  )
})

const notices: string[] = []

/** フックだけを載せた最小の画面（入力 1 つと、いまのフォームの中身）。 */
function Harness({
  event = null,
  isEditing = () => false,
}: {
  event?: UiFormProgress | null
  isEditing?: () => boolean
}) {
  const [form, setForm] = useState<FormState>(initialForm)
  const patch = useCallback(
    (changes: Partial<FormState>) => setForm((prev) => ({ ...prev, ...changes })),
    [],
  )
  useGenerateFormSync({
    form,
    patch,
    event,
    isEditing,
    onNotice: (message) => notices.push(message),
  })
  return (
    <div>
      <input
        aria-label="画像プロンプト"
        value={form.imagePrompt}
        onChange={(e) => patch({ imagePrompt: e.target.value })}
      />
      <span data-testid="mode">{form.mode}</span>
      <span data-testid="duration">{form.duration}</span>
    </div>
  )
}

function promptInput(): HTMLInputElement {
  return screen.getByLabelText('画像プロンプト') as HTMLInputElement
}

/** 初回の GET（と、その後の setState）を流し切る。 */
async function settle() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

function frame(values: Record<string, unknown>, revision = 5): UiFormProgress {
  return { type: 'form', revision, updated_by: 'external', values }
}

describe('useGenerateFormSync', () => {
  beforeEach(() => {
    notices.length = 0
  })

  it('初期表示で保存済みの下書きを復元する', async () => {
    mocked.getGenerateForm.mockResolvedValue({
      values: { mode: 'i2v', imagePrompt: 'ramen' },
      revision: 3,
      updated_by: 'external',
      updated_at: '',
    })
    render(<Harness />)
    await settle()

    expect(screen.getByTestId('mode').textContent).toBe('i2v')
    expect(promptInput().value).toBe('ramen')
    // 復元しただけでは書き戻さない
    expect(mocked.putGenerateForm).not.toHaveBeenCalled()
  })

  it('入力をデバウンスしてから 1 回だけ保存する', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    render(<Harness />)
    await settle()

    const input = screen.getByLabelText('画像プロンプト')
    fireEvent.change(input, { target: { value: 'ra' } })
    fireEvent.change(input, { target: { value: 'ramen' } })
    expect(mocked.putGenerateForm).not.toHaveBeenCalled()

    await act(async () => {
      vi.advanceTimersByTime(FORM_SYNC_DEBOUNCE_MS)
    })
    expect(mocked.putGenerateForm).toHaveBeenCalledTimes(1)
    const [values, base] = mocked.putGenerateForm.mock.calls[0]
    expect(values.imagePrompt).toBe('ramen')
    expect(base).toBe(0)
  })

  it('外から届いた値をフォームへ入れる', async () => {
    const { rerender } = render(<Harness />)
    await settle()

    rerender(<Harness event={frame({ mode: 'i2v', duration: 7 })} />)
    await settle()

    expect(screen.getByTestId('mode').textContent).toBe('i2v')
    expect(screen.getByTestId('duration').textContent).toBe('7')
    // 受け取った値は送り返さない
    expect(mocked.putGenerateForm).not.toHaveBeenCalled()
  })

  it('フォームの項目でない値・型の合わない値は捨てる', async () => {
    const { rerender } = render(<Harness />)
    await settle()

    rerender(
      <Harness event={frame({ duration: 'とても長い', somethingElse: 1 })} />,
    )
    await settle()

    expect(screen.getByTestId('duration').textContent).toBe('10')
  })

  it('自分の保存で返ってきたフレームは読み飛ばす', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const { rerender } = render(<Harness />)
    await settle()

    fireEvent.change(screen.getByLabelText('画像プロンプト'), {
      target: { value: 'ramen' },
    })
    await act(async () => {
      vi.advanceTimersByTime(FORM_SYNC_DEBOUNCE_MS)
    })
    expect(mocked.putGenerateForm).toHaveBeenCalledTimes(1)

    // サーバーは全ブラウザへ同じ内容を流す（自分にも返ってくる）
    rerender(
      <Harness event={{ ...frame({ mode: 'i2v' }, 1), updated_by: 'ui' }} />,
    )
    await settle()
    expect(screen.getByTestId('mode').textContent).toBe('full')
  })

  it('入力中の項目は外からの値で上書きしない', async () => {
    const { rerender } = render(<Harness event={null} isEditing={() => true} />)
    await settle()

    fireEvent.change(screen.getByLabelText('画像プロンプト'), {
      target: { value: '打ちかけ' },
    })
    rerender(
      <Harness
        event={frame({ imagePrompt: '外からの指示', duration: 7 })}
        isEditing={() => true}
      />,
    )
    await settle()

    // 打ちかけの欄は守り、触っていない欄は入る
    expect(promptInput().value).toBe('打ちかけ')
    expect(screen.getByTestId('duration').textContent).toBe('7')
  })

  it('守った入力は握りつぶさず、合成した値で保存し直す', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const { rerender } = render(<Harness event={null} isEditing={() => true} />)
    await settle()

    // まだ送っていない入力があるところへ、外からの更新が届く
    fireEvent.change(screen.getByLabelText('画像プロンプト'), {
      target: { value: '打ちかけ' },
    })
    rerender(
      <Harness
        event={frame({ imagePrompt: '外からの指示', duration: 7 })}
        isEditing={() => true}
      />,
    )
    await settle()

    await act(async () => {
      vi.advanceTimersByTime(FORM_SYNC_DEBOUNCE_MS)
    })
    // 保留していた保存は消えず、外から来た値と合わせて送られる
    expect(mocked.putGenerateForm).toHaveBeenCalledTimes(1)
    const [values, base] = mocked.putGenerateForm.mock.calls[0]
    expect(values.imagePrompt).toBe('打ちかけ')
    expect(values.duration).toBe(7)
    // 受け取ったフレームの revision を土台にして送る
    expect(base).toBe(5)
  })

  it('初期の復元も入力中の項目は奪わない', async () => {
    let resolveGet: (state: unknown) => void = () => {}
    mocked.getGenerateForm.mockReturnValue(
      new Promise((resolve) => {
        resolveGet = resolve
      }),
    )
    render(<Harness isEditing={() => true} />)

    // GET を待っているあいだに打ち始める
    fireEvent.change(screen.getByLabelText('画像プロンプト'), {
      target: { value: '打ちかけ' },
    })
    await act(async () => {
      resolveGet({
        values: { imagePrompt: '保存されていた値', duration: 7 },
        revision: 3,
        updated_by: 'external',
        updated_at: '',
      })
    })
    await settle()

    expect(promptInput().value).toBe('打ちかけ')
    expect(screen.getByTestId('duration').textContent).toBe('7')
  })

  it('409（あいだに外から書かれた）は知らせるだけ', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    mocked.putGenerateForm.mockRejectedValue(new ApiError(409, 'conflict'))
    render(<Harness />)
    await settle()

    fireEvent.change(screen.getByLabelText('画像プロンプト'), {
      target: { value: 'ramen' },
    })
    await act(async () => {
      vi.advanceTimersByTime(FORM_SYNC_DEBOUNCE_MS)
    })
    await settle()

    expect(mocked.putGenerateForm).toHaveBeenCalledTimes(1)
    expect(notices).toEqual(['生成フォームが外部から更新されました'])
  })
})
