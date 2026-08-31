/**
 * 生成フォームの下書き同期（`/api/ui/generate-form` と WS の `form` フレーム）。
 *
 * 外部エージェントが `PATCH /api/v1/ui/generate-form` でフォームを埋めると、その
 * 値が WS で飛んできて画面のフォームに入る。人がフォームを触れば 500ms 待って
 * サーバーへ書き戻る、という双方向の同期になっている。値のスキーマの正本は
 * この `FormState` で、サーバーは JSON の辞書として預かるだけ。
 *
 * 気をつけているのは 2 つだけ:
 *
 * - **自分が出した更新は無視する**。保存すると自分にも同じフレームが返ってくる
 *   ので、`revision` を覚えておいて読み飛ばす（無限ループにしない）。
 * - **入力中の項目は奪わない**。どこかの入力欄にカーソルがあるあいだは、まだ
 *   送っていない（＝いま打っている）項目だけ外からの値を当てない。
 */

import { useCallback, useEffect, useRef } from 'react'
import { ApiError, api, formatDetail } from './api'
import { initialForm, type FormState } from './form'
import type { UiFormProgress } from './types'

/** 打つたびに送らないための待ち時間。 */
export const FORM_SYNC_DEBOUNCE_MS = 500

/** 同期する値（`FormState` はすべて JSON にできるのでそのまま渡す）。 */
export function formValues(form: FormState): Record<string, unknown> {
  return { ...form } as unknown as Record<string, unknown>
}

/**
 * 受け取った値のうち、フォームの項目として使えるものだけを取り出す。
 *
 * 知らないキーと、型が違う値（数値の欄に文字列、など）は捨てる: 下書きは外から
 * 書ける場所なので、そのまま state に入れると画面が壊れうる。`skip` に入れた
 * キーは（入力中なので）触らない。
 */
export function formPatch(
  values: Record<string, unknown>,
  skip: ReadonlySet<string> = new Set(),
): Partial<FormState> {
  const patch: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(values)) {
    if (skip.has(key)) continue
    if (!(key in initialForm)) continue
    const reference = (initialForm as unknown as Record<string, unknown>)[key]
    if (Array.isArray(reference) !== Array.isArray(value)) continue
    if (Array.isArray(reference)) {
      patch[key] = value
      continue
    }
    if (reference === null || value === null) continue
    if (typeof reference !== typeof value) continue
    patch[key] = value
  }
  return patch as Partial<FormState>
}

/** 値が変わった項目（送り済みの内容との差分）。 */
function changedKeys(
  values: Record<string, unknown>,
  sent: Record<string, unknown>,
): Set<string> {
  const keys = new Set<string>()
  for (const key of Object.keys(values)) {
    if (JSON.stringify(values[key]) !== JSON.stringify(sent[key])) keys.add(key)
  }
  return keys
}

/** いまキーボードの入力先になっている欄があるか（既定の「編集中」の判定）。 */
export function isEditingField(): boolean {
  const active = typeof document === 'undefined' ? null : document.activeElement
  if (!active) return false
  if ((active as HTMLElement).isContentEditable) return true
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName)
}

export interface FormSyncOptions {
  form: FormState
  /** 外から来た値をフォームへ入れる（App の `patch` と同じもの）。 */
  patch: (changes: Partial<FormState>) => void
  /** WS で届いた最新の `form` フレーム（無ければ null）。 */
  event?: UiFormProgress | null
  /** 同期にまつわる一言（409 など）。 */
  onNotice?: (message: string) => void
  /** 入力中かどうかの判定（テストから差し替えられるようにしてある）。 */
  isEditing?: () => boolean
}

/**
 * フォームの状態をサーバーの下書きと同期させる。
 *
 * 返り値は無い（副作用だけ）。初期表示で 1 回 GET して復元し、以降はフォームが
 * 変わるたびに遅らせて PUT する。
 */
export function useGenerateFormSync({
  form,
  patch,
  event = null,
  onNotice,
  isEditing = isEditingField,
}: FormSyncOptions): void {
  //: サーバーが持っている最新の連番（PUT の `base_revision` に使う）
  const revision = useRef(0)
  //: 自分の保存で生まれた連番（同じフレームが返ってきたら読み飛ばす）
  const mine = useRef<Set<number>>(new Set())
  //: 最後にサーバーと揃った値（差分の基準 = 入力中かどうかの判定にも使う）
  const sent = useRef<Record<string, unknown>>(formValues(initialForm))
  //: 初回の GET が終わるまでは送らない（空のフォームで上書きしないため）
  const ready = useRef(false)
  //: 最新の form を effect の外から読むための控え
  const latest = useRef(form)
  latest.current = form
  //: 「入力中か」の判定も控えで持つ。呼び出し側が毎回作り直す関数を渡しても、
  //: 受け取りの effect が描画のたびに走らないようにするため。
  const editing = useRef(isEditing)
  editing.current = isEditing

  /**
   * サーバーの値をフォームへ流し込む（初回の復元と WS の受信で共通）。
   *
   * 守るのは「まだ送っていない項目」＝いま人が打っている項目だけ。守った項目は
   * **`sent` にサーバーの値を入れておく**: 自分の入力を「送信済み」にしてしまうと
   * 保存が動かなくなり、画面とサーバーの下書きが黙って食い違う（次の
   * `from_form` が古いプロンプトで走る）ため。守らなかった項目はそのまま当てる。
   */
  const applyRemote = useCallback(
    (values: Record<string, unknown>) => {
      const current = formValues(latest.current)
      const pending = editing.current()
        ? changedKeys(current, sent.current)
        : new Set<string>()
      // サーバーが持っている値として、守った項目のぶんも控えておく（次の保存の
      // 差分がここから取られる = 打ちかけの入力が改めて送られる）。
      sent.current = { ...sent.current, ...formPatch(values) }
      const changes = formPatch(values, pending)
      // 中身が同じなら state を作り直さない（描き直しの繰り返しを避ける）。
      if (changedKeys(changes as Record<string, unknown>, current).size === 0) {
        return
      }
      patch(changes)
    },
    [patch],
  )

  const notify = useCallback(
    (cause: unknown) => {
      if (!onNotice) return
      onNotice(
        cause instanceof ApiError ? formatDetail(cause.detail) : String(cause),
      )
    },
    [onNotice],
  )

  // ------------------------------------------------------------ 初期の復元
  useEffect(() => {
    let canceled = false
    void (async () => {
      try {
        const state = await api.getGenerateForm()
        if (canceled) return
        revision.current = state.revision
        // 読んでいるあいだに打たれた入力は奪わない（WS と同じ守り方）。
        if (state.revision > 0) applyRemote(state.values)
      } catch {
        /* 下書きが読めなくてもフォームは使える（同期しないだけ） */
      } finally {
        if (!canceled) ready.current = true
      }
    })()
    return () => {
      canceled = true
    }
  }, [applyRemote])

  // ------------------------------------------------------------ 変更の保存
  useEffect(() => {
    if (!ready.current) return
    const values = formValues(form)
    if (changedKeys(values, sent.current).size === 0) return
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const state = await api.putGenerateForm(values, revision.current)
          revision.current = state.revision
          mine.current.add(state.revision)
          sent.current = values
        } catch (cause) {
          // 409 = そのあいだに外から書かれた。取り込むのは WS のフレームの
          // 役目なので、ここでは知らせるだけ（送り直しもしない）。
          if (cause instanceof ApiError && cause.status === 409) {
            notify('生成フォームが外部から更新されました')
            return
          }
          notify(cause)
        }
      })()
    }, FORM_SYNC_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [form, notify])

  // ------------------------------------------------------ 外からの書き換え
  useEffect(() => {
    if (!event || event.type !== 'form') return
    if (mine.current.has(event.revision)) return
    revision.current = event.revision
    applyRemote(event.values)
  }, [event, applyRemote])
}
