import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api, formatDetail } from '../../api'
import type {
  CanvasBoard as BoardData,
  CanvasCard,
  CanvasCardCreate,
  CanvasCardKind,
  CanvasProgress,
  CanvasViewport,
  JobProgress,
  Options,
  StudioAssetFileRole,
  StudioAssetUpdate,
  StudioProjectDetail,
  StudioSceneUpdate,
  StudioShotUpdate,
} from '../../types'
import { Banner } from '../ui'
import { ResizeHandle, useIsWide, useResizablePanel } from '../ui/resizable-panel'
import AddCardModal from './AddCardModal'
import Board from './CanvasBoard'
import CanvasChat from './CanvasChat'
import CanvasTabs from './CanvasTabs'
import CardEditor from './CardEditor'
import {
  CARD_H,
  CARD_W,
  appendMessage,
  arrangeCards,
  canvasTabs,
  cardsInTab,
  dropCard,
  entityEpisodes,
  freeSpot,
  isStandalone,
  jobSignature,
  upsertCard,
  type Point,
} from './logic'

/**
 * 取り直しをまとめる間合い（ms）。生成の進捗は矢継ぎ早に届くので、最後の
 * フレームから少し待ってから 1 回だけ取り直す。
 */
const RELOAD_DEBOUNCE_MS = 600

/** 前に開いていたタブの置き場（作品ごと。タブを閉じたら忘れてよい）。 */
const TAB_KEY = 'canvas-tab'

function rememberedTab(projectId: string): string | null {
  try {
    return window.sessionStorage.getItem(`${TAB_KEY}:${projectId}`)
  } catch {
    return null /* sessionStorage が使えない環境では毎回 作品共通 から */
  }
}

function rememberTab(projectId: string, episodeId: string | null): void {
  try {
    const key = `${TAB_KEY}:${projectId}`
    if (episodeId) window.sessionStorage.setItem(key, episodeId)
    else window.sessionStorage.removeItem(key)
  } catch {
    /* 覚えられなくても表示には困らない */
  }
}

const CHAT_WIDTH_KEY = 'canvasChatWidth'
const CHAT_WIDTH = { initial: 320, min: 260, max: 560 }

/** md 以上か（未満ではチャットをボードとの切り替えにする）。 */
function useIsDesktop(): boolean {
  const [desktop, setDesktop] = useState(true)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(min-width: 768px)')
    const apply = () => setDesktop(query.matches)
    apply()
    query.addEventListener('change', apply)
    return () => query.removeEventListener('change', apply)
  }, [])
  return desktop
}

/**
 * キャンバス表示（ドラマスタジオの別ビュー）。
 *
 * カードの置き場所と会話はキャンバスの API（`/api/canvas/…`）から、カードの
 * 中身は開いているプロジェクトの詳細（`detail`）から引く。盤面はスタジオの
 * 鏡で、カードの無いエンティティにはサーバーが読み出しのたびにカードを作る
 * ので、ここから「並べる」操作は投げない。中身を直す操作は
 * すべてスタジオの API に投げ、終わったら `onReloadStudio` で親に取り直して
 * もらう——スタジオ表示に切り替えたときに食い違わないようにするため。
 */
export default function CanvasView({
  detail,
  event = null,
  progress = {},
  onReloadStudio,
}: {
  detail: StudioProjectDetail
  /** キャンバスのエージェント実行の最新フレーム（WS `type: "canvas"`）。 */
  event?: CanvasProgress | null
  /** App が WS から集めているジョブ進捗（生成の開始・完了で盤面を取り直す）。 */
  progress?: Record<string, JobProgress>
  /** スタジオ側を書き換えたあとの取り直し（親が持つ詳細を更新する）。 */
  onReloadStudio: () => Promise<void>
}) {
  const projectId = detail.id
  /** 開いているタブ（null = 作品共通）。入ったときは前回のタブを開く。 */
  const [tab, setTab] = useState<string | null>(() => rememberedTab(projectId))
  const [board, setBoard] = useState<BoardData | null>(null)
  const [options, setOptions] = useState<Options | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  /** 「＋」で選んだ種別と置き場所（モーダルを開いているあいだだけ持つ）。 */
  const [adding, setAdding] = useState<{ kind: CanvasCardKind; point: Point } | null>(
    null,
  )
  // 狭幅 (<md): ボードとチャットをトグルで切り替える
  const [pane, setPane] = useState<'board' | 'chat'>('board')
  const desktop = useIsDesktop()
  /** 「全体が見えるところまで寄せる」の要求（初回に 1 回だけ上げる）。 */
  const [fitRequest, setFitRequest] = useState(0)
  const fitted = useRef<string | null>(null)
  /** エージェントが走っているか（WS で届き、開き直したときは API で拾う）。 */
  const [running, setRunning] = useState(false)
  const [activity, setActivity] = useState<string | null>(null)
  // チャット欄は lg 以上でだけドラッグで広げられる（ハンドルは左縁なので反転）。
  const isWide = useIsWide()
  const chatPanel = useResizablePanel(CHAT_WIDTH_KEY, CHAT_WIDTH, 'x', {
    inverted: true,
  })

  const fail = useCallback((cause: unknown) => {
    setError(
      cause instanceof ApiError
        ? formatDetail(cause.detail)
        : cause instanceof Error
          ? cause.message
          : String(cause),
    )
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const next = await api.getCanvasBoard(projectId, tab)
      setBoard(next)
      // 初めて開いたときは、鏡が並べたカードが画面外にも広がっている。表示位置に
      // 覚えがある（動かしたことがある）ならそれを尊重し、既定のままなら寄せる。
      // タブごとに別の盤面なので、寄せたかどうかもタブごとに覚える。
      const key = `${projectId}:${tab ?? 'common'}`
      const untouched =
        next.viewport.x === 0 && next.viewport.y === 0 && next.viewport.zoom === 1
      if (fitted.current !== key && untouched && next.cards.length > 0) {
        fitted.current = key
        setFitRequest((count) => count + 1)
      }
    } catch (cause) {
      fail(cause)
    } finally {
      setLoading(false)
    }
  }, [projectId, tab, fail])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    // model カードのワークフロー・LoRA 選択に使う（生成フォームと同じ選択肢）。
    api
      .options()
      .then(setOptions)
      .catch(() => setOptions(null))
  }, [])

  useEffect(() => {
    // 実行中に画面を開き直しても「動いている」ことが分かるように拾っておく。
    api
      .getCanvasAgentState(projectId)
      .then((state) => {
        setRunning(state.running)
        setActivity(state.activity)
      })
      .catch(() => {})
  }, [projectId])

  // 取り直しは WS のフレームから呼ぶので、識別子の変化で再実行しないよう控える。
  const loadRef = useRef(load)
  loadRef.current = load
  const reloadStudioRef = useRef(onReloadStudio)
  reloadStudioRef.current = onReloadStudio

  useEffect(() => {
    if (!event || event.project_id !== projectId) return
    setRunning(event.running)
    setActivity(event.activity)
    // 会話は 1 件ずつ届く（`canvas_messages` が正なので、取り直せば必ず揃う）。
    if (event.message) {
      const message = event.message
      setBoard((current) =>
        current
          ? { ...current, messages: appendMessage(current.messages, message) }
          : current,
      )
    }
    // 終わった瞬間に盤面とスタジオを取り直す（エージェントが両方を書き換える）。
    if (!event.running) {
      void loadRef.current()
      void reloadStudioRef.current()
    }
  }, [event, projectId])

  /**
   * 盤面とスタジオをまとめて取り直す（連打を潰す軽いデバウンスつき）。
   *
   * キャンバスは自分の操作だけでは追いつけない: 生成が終われば新しい Take の
   * カードが増え、別のタブでスタジオを編集すれば中身が変わる。取り直しさえ
   * すれば鏡（サーバー側）が差分を埋めてくれるので、ここでやるのは「いつ
   * 取り直すか」だけ。
   */
  const reloadTimer = useRef<number | null>(null)
  const refresh = useCallback(() => {
    if (reloadTimer.current !== null) window.clearTimeout(reloadTimer.current)
    reloadTimer.current = window.setTimeout(() => {
      reloadTimer.current = null
      void loadRef.current()
      void reloadStudioRef.current()
    }, RELOAD_DEBOUNCE_MS)
  }, [])

  useEffect(
    () => () => {
      if (reloadTimer.current !== null) window.clearTimeout(reloadTimer.current)
    },
    [],
  )

  // 生成ジョブの状態が動いたら取り直す（テイクの出現・完成が盤面に出る）。
  // 進捗の % では動かないよう、job_id と状態だけを見る（logic.jobSignature）。
  const signature = jobSignature(progress)
  const lastSignature = useRef(signature)
  useEffect(() => {
    if (signature === lastSignature.current) return
    lastSignature.current = signature
    refresh()
  }, [signature, refresh])

  // 別のタブ（スタジオ表示や別ウィンドウ）で編集された変更には WS のフレームが
  // 無いので、戻ってきたときに取り直す。
  useEffect(() => {
    const onFocus = () => {
      if (document.visibilityState === 'hidden') return
      refresh()
    }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [refresh])

  /** 開いているタブの盤面（タブを切り替えた直後は取り直すまで null）。 */
  const shown = board && board.episode_id === tab ? board : null
  // カードの絞り込みはサーバー側でも済んでいるが、話の付け替え直後など取り直す
  // までの一瞬に他のタブのカードが混ざらないよう、ここでも同じ規則で絞る。
  const cards = shown ? cardsInTab(shown.cards, entityEpisodes(detail), tab) : []
  const tabs = canvasTabs(detail)

  /** タブを切り替える（盤面と表示位置はそのタブのものを取り直す）。 */
  const openTab = (episodeId: string | null) => {
    setTab(episodeId)
    rememberTab(projectId, episodeId)
    setSelectedId(null)
    setEditingId(null)
  }

  /** 「＋」: 話を 1 つ足して、そのタブを開く。 */
  const addEpisode = () =>
    void run(async () => {
      const episode = await api.createStudioEpisode(projectId)
      await onReloadStudio()
      openTab(episode.id)
    })

  /** 取得済みのボードに 1 枚ぶんの変更を反映する（応答で確定）。 */
  const mergeCard = useCallback((card: CanvasCard) => {
    setBoard((current) =>
      current ? { ...current, cards: upsertCard(current.cards, card) } : current,
    )
  }, [])

  /**
   * 変更操作の共通の型: 走らせて、エラーはバナーに出すだけ。途中の状態は
   * 残さない（次の取り直しでサーバー側に揃う）。
   */
  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true)
      setError(null)
      try {
        await action()
      } catch (cause) {
        fail(cause)
      } finally {
        setBusy(false)
      }
    },
    [fail],
  )

  // ---------------------------------------------------------------- カード

  const createCard = (payload: Omit<CanvasCardCreate, 'kind' | 'x' | 'y'>) => {
    if (!adding) return
    const { kind, point } = adding
    const spot = freeSpot(cards, point)
    void run(async () => {
      const created = await api.createCanvasCard(projectId, {
        // 新しいものは「いま開いているタブ」に置く。メモ・モデル設定は
        // カード自身がタブを覚え、場はその話の中にできる（カットは所属する
        // 場でタブが決まるので、ここでは何も足さない）。
        ...(isStandalone(kind) || kind === 'scene' ? { episode_id: tab } : {}),
        kind,
        ...payload,
        x: spot.x,
        y: spot.y,
        w: CARD_W,
        h: CARD_H,
      })
      mergeCard(created)
      setAdding(null)
      setSelectedId(created.id)
      // 一緒にエンティティを作ったときは、先に詳細を取り直してから編集を開く
      // （カードの中身はスタジオ側にあるので、取り直すまで空に見えてしまう）。
      if (!isStandalone(kind)) await onReloadStudio()
      // 作ったばかりのものは中身が空なので、そのまま編集を開く。
      setEditingId(created.id)
    })
  }

  /**
   * 場カードの「＋カット」: その場に属するカットを 1 手で作る。
   *
   * カードは作らない——鏡（サーバー側）が新しいカットをカード化するので、
   * 盤面とスタジオを取り直せば出てくる。
   */
  const addShotToScene = (sceneId: string) =>
    void run(async () => {
      await api.createStudioShot(projectId, {
        title: `カット ${detail.shots.length + 1}`,
        scene_id: sceneId,
      })
      await load()
      await onReloadStudio()
    })

  /** 移動は楽観更新 + 確定（エンティティには触れない軽い更新）。 */
  const moveCard = (cardId: string, x: number, y: number) => {
    setBoard((current) =>
      current
        ? {
            ...current,
            cards: current.cards.map((card) =>
              card.id === cardId ? { ...card, x, y } : card,
            ),
          }
        : current,
    )
    void api.moveCanvasCard(cardId, { x, y }).then(mergeCard).catch(fail)
  }

  /** カードを格子状に並べ直す（動かした結果はサーバーにも順に送る）。 */
  const arrange = () => {
    const layout = arrangeCards(cards)
    void run(async () => {
      for (const spot of layout) {
        mergeCard(await api.moveCanvasCard(spot.id, { x: spot.x, y: spot.y }))
      }
    })
  }

  const saveViewport = useCallback(
    (viewport: CanvasViewport) => {
      setBoard((current) => (current ? { ...current, viewport } : current))
      // 表示位置はタブごと。保存に失敗しても作業は続けられる
      void api.setCanvasViewport(projectId, viewport, tab).catch(() => {})
    },
    [projectId, tab],
  )

  const saveData = (cardId: string, data: Record<string, unknown>) =>
    void run(async () => {
      mergeCard(await api.updateCanvasCard(cardId, { data }))
      setEditingId(null)
    })

  const saveAsset = (id: string, patch: StudioAssetUpdate) =>
    void run(async () => {
      await api.updateStudioAsset(id, patch)
      await onReloadStudio()
    })

  /** 素材のメインのファイル（スタジオの World Bible と同じ受け口）。 */
  const uploadAssetFile = (id: string, file: File) =>
    void run(async () => {
      await api.uploadStudioAssetFile(id, file)
      await onReloadStudio()
    })

  const addAssetReference = (
    id: string,
    file: File,
    role: StudioAssetFileRole,
    caption: string,
  ) =>
    void run(async () => {
      await api.addStudioAssetFile(id, file, { role, caption })
      await onReloadStudio()
    })

  const removeAssetReference = (fileId: string) =>
    void run(async () => {
      await api.deleteStudioAssetFile(fileId)
      await onReloadStudio()
    })

  const saveScene = (id: string, patch: StudioSceneUpdate) =>
    void run(async () => {
      await api.updateStudioScene(id, patch)
      await onReloadStudio()
    })

  const saveShot = (id: string, patch: StudioShotUpdate) =>
    void run(async () => {
      await api.updateStudioShot(id, patch)
      await onReloadStudio()
    })

  /**
   * カードを取り除く。
   *
   * 参照カードはスタジオの写しなので、消せるのは**エンティティごと**だけ
   * （カードだけ消しても、次に開いた鏡がそのまま戻す）。
   */
  const removeCard = (cardId: string, deleteEntity: boolean) => {
    if (
      deleteEntity &&
      !window.confirm(
        'スタジオからも削除しますか？（この操作はキャンバスの外にも影響します）',
      )
    )
      return
    void run(async () => {
      await api.deleteCanvasCard(cardId, deleteEntity)
      setBoard((current) =>
        current ? { ...current, cards: dropCard(current.cards, cardId) } : current,
      )
      setEditingId(null)
      setSelectedId((current) => (current === cardId ? null : current))
      if (deleteEntity) await onReloadStudio()
    })
  }

  // ------------------------------------------------------------------ チャット

  /**
   * 発言を送ってエージェントを走らせる。
   *
   * 応答とツール実行の結果は WS（`event`）で 1 件ずつ届き、終わったところで
   * 盤面とスタジオを取り直す。
   */
  const sendMessage = (content: string, attachments: string[] = []) =>
    void run(async () => {
      // 開いているタブも渡す（「この話のカットを〜」がそのまま通るように）。
      const started = await api.runCanvasAgent(
        projectId,
        content,
        tab,
        attachments,
      )
      setRunning(started.running)
      setActivity(started.activity)
      setBoard((current) =>
        current
          ? { ...current, messages: appendMessage(current.messages, started.message) }
          : current,
      )
    })

  const stopAgent = () =>
    void run(async () => {
      const state = await api.stopCanvasAgent(projectId)
      setRunning(state.running)
    })

  // ---------------------------------------------------------------- レイアウト

  const editing = cards.find((card) => card.id === editingId) ?? null
  const boardVisible = desktop || pane === 'board'
  const chatVisible = desktop || pane === 'chat'

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {error && <Banner onClose={() => setError(null)}>{error}</Banner>}

      <div className="flex flex-wrap items-center gap-2">
        <CanvasTabs
          tabs={tabs}
          current={tab}
          busy={busy}
          onSelect={openTab}
          onAddEpisode={addEpisode}
        />
        {!desktop && (
          <div className="ml-auto flex rounded-md border border-border bg-card p-0.5">
            {(['board', 'chat'] as const).map((value) => (
              <button
                key={value}
                className={`rounded-sm px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
                  pane === value
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-secondary hover:text-foreground'
                }`}
                aria-pressed={pane === value}
                onClick={() => setPane(value)}
              >
                {value === 'board' ? 'ボード' : 'チャット'}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex min-h-0 flex-1 gap-3">
        <section
          className={`min-h-0 min-w-0 flex-1 overflow-hidden rounded-lg border border-border bg-background ${
            boardVisible ? 'flex' : 'hidden'
          }`}
        >
          {shown ? (
            <Board
              cards={cards}
              detail={detail}
              tab={tab}
              viewport={shown.viewport}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onEdit={(card) => setEditingId(card.id)}
              onMove={moveCard}
              onAdd={(kind, point) => setAdding({ kind, point })}
              onAddShot={addShotToScene}
              busy={busy}
              onViewport={saveViewport}
              onArrange={arrange}
              fitRequest={fitRequest}
            />
          ) : (
            <p className="m-auto text-xs text-muted-foreground">
              {/* 別のタブの盤面が残っているあいだも「読み込み中」（すぐ取り直す） */}
              {loading || board
                ? 'キャンバスを読み込み中…'
                : 'キャンバスを開けませんでした'}
            </p>
          )}
        </section>

        {chatVisible && desktop && (
          <ResizeHandle panel={chatPanel} label="チャット欄の幅" className="-mx-2" />
        )}

        {chatVisible && (
          <aside
            className={
              desktop
                ? 'flex min-h-0 w-80 shrink-0 flex-col gap-2 rounded-lg border border-border bg-background p-3'
                : 'flex min-h-0 flex-1 flex-col gap-2 rounded-lg border border-border bg-background p-3'
            }
            style={isWide && desktop ? { width: chatPanel.size } : undefined}
          >
            <CanvasChat
              projectId={projectId}
              messages={board?.messages ?? []}
              assets={detail.assets}
              busy={busy}
              running={running}
              activity={activity}
              onSend={sendMessage}
              onStop={stopAgent}
            />
          </aside>
        )}
      </div>

      {adding && (
        <AddCardModal
          kind={adding.kind}
          detail={detail}
          tab={tab}
          busy={busy}
          error={error}
          onClose={() => setAdding(null)}
          onCreate={createCard}
        />
      )}

      {editing && (
        <CardEditor
          key={editing.id}
          card={editing}
          detail={detail}
          options={options}
          busy={busy}
          error={error}
          onClose={() => setEditingId(null)}
          onSaveData={(data) => saveData(editing.id, data)}
          onSaveAsset={saveAsset}
          onUploadAssetFile={uploadAssetFile}
          onAddAssetReference={addAssetReference}
          onRemoveAssetReference={removeAssetReference}
          onSaveScene={saveScene}
          onSaveShot={saveShot}
          onRemove={(deleteEntity) => removeCard(editing.id, deleteEntity)}
        />
      )}
    </div>
  )
}
