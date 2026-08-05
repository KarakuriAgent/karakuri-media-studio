/**
 * キャンバス画面の純関数（UI から切り離してテストする。agent/logic.ts と同じ流儀）。
 */
import type { Node, NodeChange } from '@xyflow/react'
import type {
  CanvasCardData,
  CanvasCharacterData,
  CanvasLocationData,
  CanvasMediaData,
  CanvasMessage,
  CanvasModelData,
  CanvasNode,
  CanvasNodeKind,
  CanvasObjectData,
  CanvasProgress,
  CanvasProjectDetail,
  CanvasScriptData,
  CanvasStoryboardData,
  CanvasStyleData,
  CanvasTextData,
} from '../../types'

/** React Flow のノードに載せる中身（サーバーのカードをそのまま持つ）。 */
export type CardNodeData = { node: CanvasNode }

export type CardFlowNode = Node<CardNodeData, 'card'>

/** カード種別の並び（「＋」ポップオーバーと編集画面で共通）。 */
export const CANVAS_KINDS: CanvasNodeKind[] = [
  'style',
  'character',
  'location',
  'object',
  'script',
  'storyboard',
  'media',
  'text',
  'model',
]

export const KIND_LABEL: Record<CanvasNodeKind, string> = {
  style: '画風',
  character: 'キャラ',
  location: '場所',
  object: '小道具',
  script: '脚本',
  storyboard: '絵コンテ',
  media: '素材',
  text: 'メモ',
  model: 'モデル設定',
}

export const KIND_ICON: Record<CanvasNodeKind, string> = {
  style: '🎨',
  character: '🧑',
  location: '🏞',
  object: '📦',
  script: '📜',
  storyboard: '📽',
  media: '🖼',
  text: '📝',
  model: '⚙️',
}

/** カード種別ごとのヘッダー色（ボード上で種別を見分けるため）。 */
export const KIND_STYLE: Record<CanvasNodeKind, string> = {
  style: 'border-fuchsia-800 bg-fuchsia-950/60 text-fuchsia-200',
  character: 'border-sky-800 bg-sky-950/60 text-sky-200',
  location: 'border-emerald-800 bg-emerald-950/60 text-emerald-200',
  object: 'border-amber-800 bg-amber-950/60 text-amber-200',
  script: 'border-violet-800 bg-violet-950/60 text-violet-200',
  storyboard: 'border-indigo-800 bg-indigo-950/60 text-indigo-200',
  media: 'border-teal-800 bg-teal-950/60 text-teal-200',
  text: 'border-slate-600 bg-slate-800/60 text-slate-200',
  model: 'border-rose-800 bg-rose-950/60 text-rose-200',
}

/** 新規カードの空 data（バックエンドのスキーマの既定値と同じ）。 */
export function defaultDataFor(kind: CanvasNodeKind): CanvasCardData {
  switch (kind) {
    case 'style':
      return { description: '', palette: '', references: [], notes: '' }
    case 'character':
      return {
        description: '',
        appearance: '',
        personality: '',
        voice: '',
        images: [],
        notes: '',
      }
    case 'location':
      return { description: '', mood: '', images: [], notes: '' }
    case 'object':
      return { description: '', images: [], notes: '' }
    case 'script':
      return { synopsis: '', scenes: [], notes: '' }
    case 'storyboard':
      return { notes: '', cuts: [] }
    case 'media':
      return { media_type: 'image', url: '', job_id: null, prompt: '', caption: '' }
    case 'text':
      return { body: '' }
    case 'model':
      return {
        target: 'image',
        workflow: '',
        params: {
          aspect_ratio: '4:3 (Standard)',
          megapixels: 1.0,
          duration: 10.0,
          fps: 25,
          sage_attention: null,
          easy_cache: null,
          loras: [],
          video_loras: [],
          negative_prompt: '',
          selects: {},
          model_overrides: {},
        },
        note: '',
      }
  }
}

/** サーバーのカード -> React Flow のノード。 */
export function toFlowNode(node: CanvasNode): CardFlowNode {
  return {
    id: node.id,
    type: 'card',
    position: { x: node.x, y: node.y },
    data: { node },
    width: node.w,
    height: node.h,
    zIndex: node.z,
  }
}

/**
 * サーバーのカード列を描画中のノードへ取り込む。
 *
 * エージェント動作中は WS 再取得やポーリングで `project.nodes` が何度も差し替わる。
 * 素直に全部 `toFlowNode` で作り直すと、**ドラッグ中のカードが元の位置へ戻って
 * しまう**（サーバーはまだ移動前の座標を持っているため）ので、掴んでいるカード
 * だけは手元の位置を残す。中身（title / data / 大きさ / 重なり）はサーバー値で
 * 更新してよい。選択状態は React Flow 側だけが持つので併せて引き継ぐ。
 */
export function mergeFlowNodes(
  current: CardFlowNode[],
  incoming: CanvasNode[],
  draggingIds: ReadonlySet<string>,
): CardFlowNode[] {
  const byId = new Map(current.map((item) => [item.id, item]))
  return incoming.map((node) => {
    const existing = byId.get(node.id)
    const next = toFlowNode(node)
    if (!existing) return next
    return {
      ...next,
      selected: existing.selected,
      dragging: existing.dragging,
      position: draggingIds.has(node.id) ? existing.position : next.position,
    }
  })
}

/**
 * 位置変更の抽出。ドラッグ中（`dragging: true`）は捨て、確定した位置だけ返す
 * （移動のたびに PATCH を投げないため）。
 */
export function fromNodeChange(
  change: NodeChange<CardFlowNode>,
): { id: string; x: number; y: number } | null {
  if (change.type !== 'position' || change.dragging || !change.position) return null
  return { id: change.id, x: change.position.x, y: change.position.y }
}

/**
 * 掴んでいるカードの id 集合を更新する（:func:`mergeFlowNodes` に渡すため）。
 *
 * `position` 変更の `dragging` で出し入れし、消えたカードは落とす。
 */
export function nextDraggingIds(
  current: ReadonlySet<string>,
  changes: NodeChange<CardFlowNode>[],
): Set<string> {
  const next = new Set(current)
  for (const change of changes) {
    if (change.type === 'position') {
      if (change.dragging) next.add(change.id)
      else next.delete(change.id)
    } else if (change.type === 'remove') {
      next.delete(change.id)
    }
  }
  return next
}

function firstLine(text: string): string {
  return (text || '').split('\n')[0].trim()
}

/** カード 1 枚の要約（ボード上の 1〜2 行表示）。 */
export function cardSummary(node: CanvasNode): string {
  const data = node.data as unknown
  switch (node.kind) {
    case 'style': {
      const style = data as CanvasStyleData
      return firstLine(style.description) || firstLine(style.palette) || ''
    }
    case 'character': {
      const character = data as CanvasCharacterData
      return firstLine(character.description) || firstLine(character.appearance) || ''
    }
    case 'location': {
      const location = data as CanvasLocationData
      return firstLine(location.description) || firstLine(location.mood) || ''
    }
    case 'object': {
      const object = data as CanvasObjectData
      return firstLine(object.description)
    }
    case 'script': {
      const script = data as CanvasScriptData
      const scenes = script.scenes?.length ?? 0
      const head = firstLine(script.synopsis)
      return head ? `${head}（${scenes} シーン）` : `${scenes} シーン`
    }
    case 'storyboard': {
      const board = data as CanvasStoryboardData
      return `${board.cuts?.length ?? 0} カット`
    }
    case 'media': {
      const media = data as CanvasMediaData
      return firstLine(media.caption) || firstLine(media.prompt) || media.url || ''
    }
    case 'text':
      return firstLine((data as CanvasTextData).body)
    case 'model': {
      const model = data as CanvasModelData
      const workflow = model.workflow || '未選択'
      return `${model.target} / ${workflow}`
    }
    default:
      return ''
  }
}

/**
 * 取得済みプロジェクトを差し替えてよいか（連打時のレース対策）。
 *
 * 古いレスポンスが新しい状態を上書きしないように、記録が減る差し替えは捨てる。
 */
export function shouldReplaceProject(
  current: CanvasProjectDetail | null,
  next: CanvasProjectDetail,
): boolean {
  if (!current || current.id !== next.id) return true
  if (next.messages.length < current.messages.length) return false
  if (next.nodes.length < current.nodes.length) return false
  return true
}

// ------------------------------------------------------------------ @ 参照

/** @ オートコンプリートの候補に出す最大件数。 */
export const MENTION_LIMIT = 8

/**
 * カーソル位置から後方に `@` を探す（間に空白や別の `@` があれば補完しない）。
 *
 * `@` の直前は行頭か空白であることを求める（メールアドレスのような文字列で
 * 候補が開かないようにするため）。
 */
export function mentionQueryAt(
  text: string,
  caret: number,
): { start: number; query: string } | null {
  const head = text.slice(0, Math.max(0, Math.min(caret, text.length)))
  const start = head.lastIndexOf('@')
  if (start < 0) return null
  const before = start > 0 ? head[start - 1] : ''
  if (before && !/\s/.test(before)) return null
  const query = head.slice(start + 1)
  if (/[\s@]/.test(query)) return null
  return { start, query }
}

/** 補完の確定: `[start, caret)` を `@タイトル ` に置き換える。 */
export function applyMention(
  text: string,
  start: number,
  caret: number,
  title: string,
): { text: string; caret: number } {
  const inserted = `@${title} `
  const next = text.slice(0, start) + inserted + text.slice(caret)
  return { text: next, caret: start + inserted.length }
}

/** 入力中の語で候補カードを絞る（タイトル付きのカードのみ、上位 8 件）。 */
export function mentionCandidates(
  nodes: CanvasNode[],
  query: string,
  limit = MENTION_LIMIT,
): CanvasNode[] {
  const needle = query.trim().toLowerCase()
  return nodes
    .filter((node) => node.title)
    .filter((node) => !needle || node.title.toLowerCase().includes(needle))
    .slice(0, limit)
}

/**
 * 吹き出しに出す本文。
 *
 * ユーザー発言はサーバー側で `@` 参照を全文に展開して保存されるので、控えて
 * ある元発言（`data.text`）のほうを出す。
 */
export function messageText(message: CanvasMessage): string {
  const text = message.data?.text
  return typeof text === 'string' ? text : message.content
}

/** 生成の開始イベント（この間はポーリングの安全網を回す）。 */
const GENERATE_START = 'job_started'
/** 生成の終了イベント（成功・失敗・投入失敗）。 */
const GENERATE_END = ['job_done', 'job_failed', 'generate_failed']

/**
 * バックグラウンドの生成が走っているか（WS フレーム 1 つぶんの状態遷移）。
 *
 * エージェントのターンは投入した時点で終わるので `thinking` では追えない。
 * job_started 〜 job_done/job_failed の間だけポーリングを続けるために使う。
 */
export function nextGenerating(current: boolean, event: CanvasProgress): boolean {
  if (event.event === GENERATE_START) return true
  if (GENERATE_END.includes(event.event)) return false
  return current
}
